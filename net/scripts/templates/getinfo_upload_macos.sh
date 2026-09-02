#!/bin/sh
# This file is generated for one inspection profile. It contains no directory-service,
# device, application, or database credentials.
set -eu

PROFILE_CONFIG_JSON=$(cat <<'NET_PROFILE_CONFIG'
__PROFILE_CONFIG_JSON__
NET_PROFILE_CONFIG
)
export PROFILE_CONFIG_JSON

if ! PYTHON3=$(command -v python3 2>/dev/null); then
  printf '%s\n' 'Python 3 is required to collect and serialize this inspection payload.' >&2
  exit 2
fi

SYSTEM_PROFILER_OUTPUT=$(/usr/sbin/system_profiler SPHardwareDataType SPSoftwareDataType 2>/dev/null || true)
SYSCTL_OUTPUT=$(/usr/sbin/sysctl hw.physicalcpu hw.logicalcpu hw.memsize vm.page_size machdep.cpu.brand_string 2>/dev/null || true)
DISKUTIL_OUTPUT=$(/usr/sbin/diskutil info / 2>/dev/null || true)
DF_OUTPUT=$(df -kP 2>/dev/null || true)
IFCONFIG_OUTPUT=$(ifconfig 2>/dev/null || true)
CPU_USAGE_OUTPUT=$(/usr/bin/top -l 1 -n 0 2>/dev/null || true)
VM_STAT_OUTPUT=$(/usr/bin/vm_stat 2>/dev/null || true)
CURRENT_USER_ACCOUNT=$(/usr/bin/id -un 2>/dev/null || true)
CURRENT_USER_FULL_NAME=$(/usr/bin/id -F "$CURRENT_USER_ACCOUNT" 2>/dev/null || true)
export SYSTEM_PROFILER_OUTPUT SYSCTL_OUTPUT DISKUTIL_OUTPUT DF_OUTPUT IFCONFIG_OUTPUT
export CPU_USAGE_OUTPUT VM_STAT_OUTPUT CURRENT_USER_ACCOUNT CURRENT_USER_FULL_NAME

PAYLOAD_JSON=$(
  "$PYTHON3" - <<'PY'
import datetime as dt
import json
import os
import re
import socket
import subprocess

config = json.loads(os.environ['PROFILE_CONFIG_JSON'])
now = dt.datetime.now().astimezone()
if config['file_time_mode'] == 'recent_days':
    start = now - dt.timedelta(days=config['recent_days'])
    end = now
else:
    start = dt.datetime.fromisoformat(config['range_start_date']).replace(tzinfo=now.tzinfo)
    end = (dt.datetime.fromisoformat(config['range_end_date']) + dt.timedelta(days=1)).replace(tzinfo=now.tzinfo)

metadata = []
for root in config['scan_directories']:
    find_command = ['/usr/bin/find', root]
    if not config['recursive']:
        find_command.extend(['-maxdepth', '1'])
    find_command.extend(['-type', 'f', '-iname', '*.json', '-print'])
    completed = subprocess.run(find_command, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    for path in completed.stdout.splitlines():
        try:
            modified = dt.datetime.fromtimestamp(os.path.getmtime(path), tz=now.tzinfo)
        except OSError:
            continue
        if start <= modified < end:
            metadata.append({'path': path, 'modified_at': modified.strftime('%Y-%m-%d %H:%M:%S'), 'size_bytes': os.path.getsize(path)})

sysctl = dict(
    line.split(': ', 1) for line in os.environ['SYSCTL_OUTPUT'].splitlines() if ': ' in line
)
physical = int(sysctl.get('hw.physicalcpu', '0') or 0)
logical = int(sysctl.get('hw.logicalcpu', '0') or 0)

def clamp_percent(value):
    return max(0.0, min(100.0, value))


cpu_match = re.search(r'CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys', os.environ['CPU_USAGE_OUTPUT'])
cpu_usage = clamp_percent(sum(float(value) for value in cpu_match.groups())) if cpu_match else 0.0
page_size = int(sysctl.get('vm.page_size', '4096') or 4096)
vm_counts = {}
for line in os.environ['VM_STAT_OUTPUT'].splitlines():
    match = re.match(r'^(Pages [^:]+):\s+(\d+)\.', line)
    if match:
        vm_counts[match.group(1)] = int(match.group(2))
memory_bytes = int(sysctl.get('hw.memsize', '0') or 0)
used_pages = sum(vm_counts.get(label, 0) for label in (
    'Pages active', 'Pages wired down', 'Pages occupied by compressor'))
memory_usage = clamp_percent((used_pages * page_size / memory_bytes) * 100) if memory_bytes else 0.0
memory_gb = memory_bytes / (1024 ** 3)

disk_summary = '; '.join(os.environ['DF_OUTPUT'].splitlines()[1:])
disk_match = re.search(r'Disk Size:\s*[^\n]*\(([\d,]+) Bytes\)', os.environ['DISKUTIL_OUTPUT'])
disk_bytes = int(disk_match.group(1).replace(',', '')) if disk_match else 0
if not disk_bytes:
    for line in os.environ['DF_OUTPUT'].splitlines()[1:]:
        columns = line.split()
        if len(columns) >= 2 and columns[1].isdigit():
            disk_bytes += int(columns[1]) * 1024
disk_total_gb = disk_bytes / (1024 ** 3)

interfaces = {}
current_interface = None
for line in os.environ['IFCONFIG_OUTPUT'].splitlines():
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
    '日志时间': now.strftime('%Y-%m-%d %H:%M:%S'),
    '系统信息概览': {
        '计算机名': socket.gethostname(),
        '当前登录用户工号': os.environ['CURRENT_USER_ACCOUNT'],
        '当前登录用户姓名': os.environ['CURRENT_USER_FULL_NAME'] or os.environ['CURRENT_USER_ACCOUNT'],
        '系统主要版本名': 'macOS',
        '系统详细版本': os.environ['SYSTEM_PROFILER_OUTPUT'],
        '系统版本类型': 'macOS',
    },
    '网络信息': network_rows,
    '计算机硬件资源情况': {
        'CPU型号': sysctl.get('machdep.cpu.brand_string', ''),
        'CPU物理核心数': physical,
        'CPU逻辑处理器数': logical,
        '当前内存容量': f'{memory_gb:.2f}GB',
        '当前CPU占用率': f'{cpu_usage:.1f}%',
        '当前内存使用率': f'{memory_usage:.1f}%',
        '磁盘总量': f'{disk_total_gb:.2f}GB',
        '磁盘摘要': disk_summary,
        'cpu_physical_core_count': physical,
        'cpu_logical_processor_count': logical,
        'disk_summary': disk_summary,
    },
    '日志文件元数据': metadata,
}
print(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
PY
)

if ! printf '%s' "$PAYLOAD_JSON" | /usr/bin/curl --fail-with-body --silent --show-error -X POST -H 'Content-Type: application/json; charset=utf-8' --data-binary @- "$("$PYTHON3" -c 'import json, os; print(json.loads(os.environ["PROFILE_CONFIG_JSON"])["upload_url"])')"; then
  FAILURE_DIRECTORY=$("$PYTHON3" -c 'import json, os; print(json.loads(os.environ["PROFILE_CONFIG_JSON"])["scan_directories"][0])')
  mkdir -p "$FAILURE_DIRECTORY"
  FAILURE_PATH="$FAILURE_DIRECTORY/$(hostname)-upload-failed-$(date +%Y%m%d%H%M%S).json"
  printf '%s' "$PAYLOAD_JSON" > "$FAILURE_PATH"
  printf '%s\n' "Inspection upload failed; payload retained at $FAILURE_PATH." >&2
  exit 1
fi
