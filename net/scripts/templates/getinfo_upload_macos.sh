#!/bin/sh
# Run repeatedly with the same local account; saves latest.json then posts to the configured API.
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
import tempfile
import time
import urllib.error
import urllib.request


def publish_latest(endpoint, token, state, collect):
    state = Path(state); state.mkdir(parents=True, exist_ok=True)
    with (state / 'latest.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = json.dumps(collect(), ensure_ascii=False).encode('utf-8')
        if len(raw) > 16 * 1024 * 1024: raise OSError('PC_LOG_TOO_LARGE: Collected JSON exceeds the 16 MiB upload limit.')
        final = state / 'latest.json'
        descriptor, partial = tempfile.mkstemp(prefix='latest.', suffix='.tmp', dir=state)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(raw); output.flush(); os.fsync(output.fileno())
            os.replace(partial, final)
        finally:
            if os.path.exists(partial): os.unlink(partial)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl): return None
        opener = urllib.request.build_opener(NoRedirect())
        for attempt in range(1, 4):
            request = urllib.request.Request(endpoint, data=raw, method='POST', headers={'Content-Type':'application/json; charset=utf-8','Authorization':'Bearer ' + token})
            try:
                with opener.open(request, timeout=10) as response:
                    if not 200 <= response.status < 300: raise OSError('unexpected API response')
                print('PC_UPLOAD_COMPLETE: latest.json was saved locally and accepted by the monitoring API.')
                return
            except (OSError, urllib.error.URLError, urllib.error.HTTPError):
                if attempt == 3: raise OSError('PC_UPLOAD_FAILED: Monitoring API upload failed after 3 attempts; latest.json was retained locally.')
                time.sleep(attempt)

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
    disk_summary = '; '.join(f"{row['device']} {row['total_bytes']} B ({row['free_bytes']} B free)" for row in disk_volumes)
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
        '计算机硬件资源情况': {
            'CPU型号': sysctl.get('machdep.cpu.brand_string', ''),
            'CPU物理核心数': physical,
            'CPU逻辑处理器数': logical,
            '当前内存容量': f'{memory_gb:.2f}GB',
            '当前CPU占用率': f'{cpu_usage:.1f}%' if cpu_usage is not None else None,
            '当前内存使用率': f'{memory_usage:.1f}%' if memory_usage is not None else None,
            '磁盘总量': f'{disk_total_gb:.2f}GB',
            '磁盘摘要': disk_summary,
        },
        '日志文件元数据': metadata,
    }
    return payload

if __name__ == '__main__':
    config = json.loads(base64.b64decode('__PC_CONFIG_BASE64__'))
    state = Path.home() / 'Library' / 'Application Support' / 'PCDailyCollector'
    publish_latest(config['endpoint_url'], config['token'], state, lambda: collect_payload(config))
PC_COLLECTOR
