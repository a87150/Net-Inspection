#!/bin/sh
# Run repeatedly with the same local account. Configure an already mounted share.
# PC_CONFIG: __PC_CONFIG_BASE64__
set -eu
if ! PYTHON3=$(command -v python3 2>/dev/null); then
  printf '%s\n' 'Python 3 is required.' >&2
  exit 2
fi
exec "$PYTHON3" - <<'PC_COLLECTOR'
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import uuid


def publish_daily(destination, state, computer, collect):
    destination, state = Path(destination), Path(state)
    state.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[^a-zA-Z0-9._-]', '_', computer)
    day = dt.datetime.now().strftime('%Y%m%d')
    marker = state / f'{safe}-{day}.done'
    # OS locks are automatically released on process termination.
    with (state / f'{safe}.lock').open('a+b') as lock:
        if os.name == 'nt':  # Allows local-only verification on Windows.
            import msvcrt
            lock.seek(0)
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if marker.exists():
            return
        if not destination.is_dir():
            raise OSError('Shared folder is unavailable')
        final = destination / f'{safe}-{day}.json'
        if not final.exists():
            partial = destination / f'{safe}-{day}.{uuid.uuid4().hex}.uploading'
            try:
                payload = collect()
                with partial.open('x', encoding='utf-8') as output:
                    json.dump(payload, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                partial.rename(final)
            finally:
                partial.unlink(missing_ok=True)
        marker.write_text(day, encoding='ascii')


def read_command(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def collect_payload(config):
    observed = {
        'SYSTEM_PROFILER_OUTPUT': read_command('/usr/sbin/system_profiler', 'SPHardwareDataType', 'SPSoftwareDataType'),
        'SYSCTL_OUTPUT': read_command('/usr/sbin/sysctl', 'hw.physicalcpu', 'hw.logicalcpu', 'hw.memsize', 'vm.page_size', 'machdep.cpu.brand_string'),
        'DISKUTIL_OUTPUT': read_command('/usr/sbin/diskutil', 'info', '/'),
        'DF_OUTPUT': read_command('/bin/df', '-kP'),
        'IFCONFIG_OUTPUT': read_command('/sbin/ifconfig'),
        'CPU_USAGE_OUTPUT': read_command('/usr/bin/top', '-l', '1', '-n', '0'),
        'VM_STAT_OUTPUT': read_command('/usr/bin/vm_stat'),
        'CURRENT_USER_ACCOUNT': read_command('/usr/bin/id', '-un').strip(),
        'CURRENT_USER_FULL_NAME': read_command('/usr/bin/id', '-F').strip(),
    }
    now = dt.datetime.now().astimezone()
    metadata = []

    sysctl = dict(
        line.split(': ', 1) for line in observed['SYSCTL_OUTPUT'].splitlines() if ': ' in line
    )
    physical = int(sysctl.get('hw.physicalcpu', '0') or 0)
    logical = int(sysctl.get('hw.logicalcpu', '0') or 0)

    def clamp_percent(value):
        return max(0.0, min(100.0, value))


    cpu_match = re.search(r'CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys', observed['CPU_USAGE_OUTPUT'])
    cpu_usage = clamp_percent(sum(float(value) for value in cpu_match.groups())) if cpu_match else None
    page_size = int(sysctl.get('vm.page_size', '4096') or 4096)
    vm_counts = {}
    for line in observed['VM_STAT_OUTPUT'].splitlines():
        match = re.match(r'^(Pages [^:]+):\s+(\d+)\.', line)
        if match:
            vm_counts[match.group(1)] = int(match.group(2))
    memory_bytes = int(sysctl.get('hw.memsize', '0') or 0)
    used_pages = sum(vm_counts.get(label, 0) for label in (
        'Pages active', 'Pages wired down', 'Pages occupied by compressor'))
    memory_usage = clamp_percent((used_pages * page_size / memory_bytes) * 100) if memory_bytes and vm_counts else None
    memory_gb = memory_bytes / (1024 ** 3)

    disk_volumes = []
    for line in observed['DF_OUTPUT'].splitlines()[1:]:
        columns = line.split()
        if len(columns) >= 4 and columns[0].startswith('/dev/') and columns[1].isdigit() and columns[3].isdigit():
            disk_volumes.append({'device': columns[0], 'total_bytes': int(columns[1]) * 1024, 'free_bytes': int(columns[3]) * 1024})
    disk_summary = '; '.join(observed['DF_OUTPUT'].splitlines()[1:])
    disk_match = re.search(r'Disk Size:\s*[^\n]*\(([\d,]+) Bytes\)', observed['DISKUTIL_OUTPUT'])
    disk_bytes = int(disk_match.group(1).replace(',', '')) if disk_match else 0
    if not disk_bytes:
        for line in observed['DF_OUTPUT'].splitlines()[1:]:
            columns = line.split()
            if len(columns) >= 2 and columns[1].isdigit():
                disk_bytes += int(columns[1]) * 1024
    disk_total_gb = disk_bytes / (1024 ** 3)

    interfaces = {}
    current_interface = None
    for line in observed['IFCONFIG_OUTPUT'].splitlines():
        interface_match = re.match(r'^([A-Za-z0-9_.-]+):\s+flags=', line)
        if interface_match:
            current_interface = interface_match.group(1)
            interfaces[current_interface] = {'ips': [], 'mac': ''}
            continue
        if not current_interface:
            continue
        mac_match = re.search(r'\bether\s+([0-9A-Fa-f:]{17})', line)
        if mac_match:
            interfaces[current_interface]['mac'] = mac_match.group(1)
        ip_match = re.search(r'\binet6?\s+([^\s%]+(?:%[^\s]+)?)', line)
        if ip_match and ip_match.group(1) not in {'127.0.0.1', '::1'}:
            interfaces[current_interface]['ips'].append(ip_match.group(1))
    network_rows = [
        {'接口名称': name, 'IP地址': ', '.join(dict.fromkeys(data['ips'])), 'MAC地址': data['mac']}
        for name, data in interfaces.items() if data['ips'] or data['mac']
    ]

    payload = {
        'platform': 'macos',
        '日志时间': now.strftime('%Y-%m-%d %H:%M:%S'),
        '系统信息概览': {
            '计算机名': socket.gethostname(),
            '当前登录用户工号': observed['CURRENT_USER_ACCOUNT'],
            '当前登录用户姓名': observed['CURRENT_USER_FULL_NAME'] or observed['CURRENT_USER_ACCOUNT'],
            '系统主要版本名': 'macOS',
            '系统详细版本': observed['SYSTEM_PROFILER_OUTPUT'],
            '系统版本类型': 'macOS',
        },
        '网络信息': network_rows,
        '磁盘空间情况': disk_volumes,
        '计算机硬件资源情况': {
            'CPU型号': sysctl.get('machdep.cpu.brand_string', ''),
            'CPU物理核心数': physical,
            'CPU逻辑处理器数': logical,
            '当前内存容量': f'{memory_gb:.2f}GB',
            '当前CPU占用率': f'{cpu_usage:.1f}%' if cpu_usage is not None else None,
            '当前内存使用率': f'{memory_usage:.1f}%' if memory_usage is not None else None,
            '磁盘总量': f'{disk_total_gb:.2f}GB',
            '磁盘摘要': disk_summary,
            'cpu_physical_core_count': physical,
            'cpu_logical_processor_count': logical,
            'disk_summary': disk_summary,
        },
        '日志文件元数据': metadata,
    }
    return payload

if __name__ == '__main__':
    config = json.loads(base64.b64decode('__PC_CONFIG_BASE64__'))
    PC_LOG_DESTINATION = Path(config['destination'])
    # Refuse a plain directory left behind after the volume is unmounted.
    mount = next((parent for parent in (PC_LOG_DESTINATION, *PC_LOG_DESTINATION.parents)
                  if parent.parent == Path('/Volumes')), None)
    if mount is None or not os.path.ismount(mount):
        raise OSError('Configured share must be mounted under /Volumes')
    state = Path.home() / 'Library' / 'Application Support' / 'PCDailyCollector'
    publish_daily(PC_LOG_DESTINATION, state, socket.gethostname(), lambda: collect_payload(config))
PC_COLLECTOR
