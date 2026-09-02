import json
import re
import time

from net.infrastructure.collection import CollectionResult, Timer
from net.services.collectors.selection import LINUX_FIELDS, NETWORK_FIELDS, selected_fields
from net.exports.adapters import MAX_CONFIG_BYTES
from net.devices.network.configuration import NETWORK_CONFIG, BAD_OUTPUT, network_vendor, validate_native


LINUX_COMMANDS = {
    'hostname': 'hostname',
    'system': 'uname -a; printf "\\n---OS_RELEASE---\\n"; cat /etc/os-release 2>/dev/null',
    'uptime': 'cat /proc/uptime; uptime',
    'cpu': 'lscpu; printf "\\n---LOAD---\\n"; cat /proc/loadavg',
    'memory': 'free -b',
    'storage': 'df -P -B1',
    'network': 'ip -j address 2>/dev/null; printf "\\n---ROUTE---\\n"; ip -j route 2>/dev/null',
    'services': 'systemctl --failed --no-pager --no-legend',
    'logs': 'journalctl -p 0..3 --since "24 hours ago" --no-pager -n 100',
}

NETWORK_COMMANDS = {
    'huawei': ['display version', 'display device', 'display cpu-usage', 'display memory-usage', 'display environment', 'display interface brief', 'display vlan', 'display logbuffer'],
    'h3c': ['display version', 'display device', 'display cpu-usage', 'display memory', 'display environment', 'display interface brief', 'display vlan', 'display logbuffer'],
    'cisco': ['show version', 'show inventory', 'show processes cpu', 'show processes memory', 'show environment all', 'show interfaces status', 'show vlan brief', 'show logging | last 100'],
    'ruijie': ['show version', 'show inventory', 'show cpu', 'show memory', 'show environment', 'show interfaces status', 'show vlan', 'show logging'],
    'generic': ['show version', 'show inventory', 'show cpu', 'show memory', 'show environment', 'show interfaces', 'show vlan', 'show logging'],
}

PAGING_COMMANDS = {
    'huawei': 'screen-length 0 temporary',
    'h3c': 'screen-length disable',
    'cisco': 'terminal length 0',
    'ruijie': 'terminal length 0',
    'generic': 'terminal length 0',
}


def _paramiko():
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError('未安装 paramiko，请执行 pip install -r requirements.txt') from exc
    return paramiko


def _connect(asset, timeout):
    paramiko = _paramiko()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=asset.ip,
        port=asset.port or 22,
        username=asset.username,
        password=asset.password,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def _json_or_text(value):
    value = value.strip()
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _parse_linux(raw):
    memory = {}
    memory_lines = raw.get('memory', '').splitlines()
    if len(memory_lines) > 1:
        columns = memory_lines[1].split()
        if len(columns) >= 4:
            memory = {'total_bytes': columns[1], 'used_bytes': columns[2], 'free_bytes': columns[3]}

    disks = []
    for line in raw.get('storage', '').splitlines()[1:]:
        columns = line.split()
        if len(columns) >= 6:
            disks.append({'filesystem': columns[0], 'total_bytes': columns[1], 'used_bytes': columns[2], 'available_bytes': columns[3], 'usage': columns[4], 'mount': columns[5]})

    network_text = raw.get('network', '')
    address_text, _, route_text = network_text.partition('---ROUTE---')
    system_text = raw.get('system', '')
    uname, _, os_release = system_text.partition('---OS_RELEASE---')
    uptime_seconds = raw.get('uptime', '').splitlines()[0].split()[0] if raw.get('uptime') else ''
    return {
        'computer_name': raw.get('hostname', '').strip(),
        'system_info': {'uname': uname.strip(), 'os_release': os_release.strip(), 'uptime_seconds': uptime_seconds},
        'cpu': {'raw': raw.get('cpu', '')},
        'memory': memory,
        'storage_status': disks,
        'network_info': {'interfaces': _json_or_text(address_text), 'routes': _json_or_text(route_text)},
        'services': [line for line in raw.get('services', '').splitlines() if line.strip()],
        'logs': [line for line in raw.get('logs', '').splitlines() if line.strip()],
    }


def collect_linux_ssh(server, timeout=10, selected_items=None):
    timer = Timer()
    try:
        with timer:
            client = _connect(server, timeout)
            try:
                raw = {}
                failures = {}
                for name, command in selected_fields(LINUX_COMMANDS, selected_items, LINUX_FIELDS).items():
                    _, stdout, stderr = client.exec_command('set -e; ' + command, timeout=timeout)
                    output = stdout.read().decode('utf-8', errors='replace').strip()
                    error = stderr.read().decode('utf-8', errors='replace').strip()
                    code = stdout.channel.recv_exit_status()
                    if code != 0 or error or (not output and name not in ('services', 'logs')):
                        failures[name] = f'exit={code}; {error or "missing evidence"}'
                    else:
                        raw[name] = output
            finally:
                client.close()
        parsed = _parse_linux(raw)
        requested = list(LINUX_FIELDS) if selected_items is None else selected_items
        data = {item: parsed[item] for item in requested if item in parsed
                and all(key in raw for key in LINUX_FIELDS[item])}
        for item in ('memory', 'storage_status'):
            if item in data and not data[item]:
                failures[item] = 'unusable command evidence'
                del data[item]
        missing = [item for item in requested if item not in data]
        return CollectionResult(True, 'partial' if missing and data else 'failed' if missing else 'success',
                                '缺少有效采集证据：' + ', '.join(missing) if missing else '',
                                data=data, raw={**raw, **({'command_errors': failures} if failures else {})}, duration_ms=timer.duration_ms)
    except Exception as exc:
        return CollectionResult(False, 'failed', f'SSH 采集失败：{exc}', duration_ms=getattr(timer, 'duration_ms', 0))


def _read_channel(channel, timeout, quiet=1.5):
    deadline = time.monotonic() + timeout
    chunks = []
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        if channel.recv_ready():
            chunks.append(channel.recv(65535).decode('utf-8', errors='replace'))
            last_data = time.monotonic()
        elif chunks:
            idle = time.monotonic() - last_data
            combined_tail = ''.join(chunks)[-300:]
            if idle >= 0.2 and re.search(r'(?m)[^\r\n]{0,100}[>#]\s*$', combined_tail):
                break
            if idle >= quiet:
                break
        else:
            time.sleep(0.05)
    return ''.join(chunks)


def _network_data(raw, vendor):
    combined = '\n'.join(raw.values())
    percentages = [int(value) for value in re.findall(r'(?i)(?:cpu|usage)[^\n%\d]{0,40}(\d{1,3})\s*%', combined)]
    temperatures = [int(value) for value in re.findall(r'(?i)(?:temperature|temp)[^\n\d-]{0,20}(-?\d{1,3})', combined)]
    keys = list(raw)
    return {
        'device_info': {'vendor': vendor, 'version_output': raw.get(keys[0], '') if keys else ''},
        'cpu': {'usage_percent': percentages[0] if percentages else None},
        'memory': {'raw': next((value for key, value in raw.items() if 'memory' in key.lower()), '')},
        'temperature': {'values_celsius': temperatures},
        'interface_status': {'raw': next((value for key, value in raw.items() if 'interface' in key.lower()), '')},
        'vlan_status': {'raw': next((value for key, value in raw.items() if 'vlan' in key.lower()), '')},
        'logs': next((value for key, value in raw.items() if 'log' in key.lower()), ''),
    }


def _read_configuration_channel(channel, timeout, prompt=None):
    """Bound bytes/time and require a stable exact prompt, never quiet output.

    Decode after combining bytes so split UTF-8 characters remain intact.
    A native terminator is checked separately before recording success.
    """
    deadline = time.monotonic() + max(.1, min(timeout, 120))
    buffer = bytearray()
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        if channel.recv_ready():
            chunk = channel.recv(65535)
            if not chunk:
                raise ValueError('SSH closed before configuration prompt')
            buffer.extend(chunk)
            if len(buffer) > MAX_CONFIG_BYTES:
                raise ValueError('configuration size limit')
            last_data = time.monotonic()
        elif buffer and time.monotonic() - last_data >= .2:
            tail = bytes(buffer).split(b'\n')[-1].strip().decode('utf-8')
            if (prompt is not None and tail == prompt) or (
                prompt is None and re.fullmatch(r'(?:<[^<>\s]{1,100}>|[\w./:@-]{1,100}[>#])', tail)
            ):
                return bytes(buffer).decode('utf-8').replace('\r\n', '\n'), tail
        time.sleep(.02)
    raise TimeoutError('configuration did not terminate at the session prompt')


def _capture_configuration(channel, timeout, vendor, prompt):
    command = NETWORK_CONFIG[vendor][1]
    channel.send(command + '\n')
    output, _ = _read_configuration_channel(channel, timeout, prompt)
    lines = output.splitlines(keepends=True)
    if lines and lines[-1].strip() == prompt:
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip() in (command, prompt + command, prompt + ' ' + command):
        lines.pop(0)
    content = validate_native(''.join(lines), vendor)
    return {'status': 'success', 'vendor': vendor, 'format': 'text',
            'scope': NETWORK_CONFIG[vendor][3], 'complete': True,
            'full_backup': False, 'content': content}


def collect_network_ssh(device, timeout=12, selected_items=None):
    vendor = (device.vendor or 'generic').strip().lower()
    vendor_key = network_vendor(vendor) or next((name for name in NETWORK_COMMANDS if name in vendor), 'generic')
    wants_config = selected_items is not None and 'config_info' in selected_items
    config_vendor = network_vendor(vendor)
    config = {'status': 'unsupported', 'message': '不支持此厂商的只读配置采集。'} if wants_config else None
    raw = {}
    if wants_config and not config_vendor and set(selected_items) == {'config_info'}:
        return CollectionResult(True, 'failed', config['message'], data={'config_info': config}, raw={'config_info': config})
    timer = Timer()
    try:
        with timer:
            client = _connect(device, timeout)
            try:
                channel = client.invoke_shell(width=200, height=1000)
                prompt = None
                if wants_config and config_vendor:
                    try:
                        _, prompt = _read_configuration_channel(channel, timeout)
                        channel.send(NETWORK_CONFIG[config_vendor][0] + '\n')
                        paging, _ = _read_configuration_channel(channel, timeout, prompt)
                        if BAD_OUTPUT.search(paging):
                            raise ValueError('cannot disable pagination')
                        config = _capture_configuration(channel, timeout, config_vendor, prompt)
                    except Exception:
                        config = {'status': 'failed', 'message': '配置采集失败：命令、完整结束提示符、超时或大小校验未通过。'}
                        # A timed-out/paged shell is no longer synchronized.
                        return CollectionResult(True, 'failed', config['message'], data={'config_info': config}, raw={'config_info': config})
                else:
                    _read_channel(channel, 2)
                    channel.send(PAGING_COMMANDS[vendor_key] + '\n')
                    _read_channel(channel, 2)
                successful_items, failed_items = set(), set()
                for item, command in zip(NETWORK_FIELDS, NETWORK_COMMANDS[vendor_key]):
                    if selected_items is not None and item not in selected_items:
                        continue
                    channel.send(command + '\n')
                    output = _read_channel(channel, timeout).strip()
                    lines = output.splitlines()
                    complete = bool(lines and re.fullmatch(r'(?:<[^<>\s]+>|[\w./:@-]+[>#])', lines[-1].strip()))
                    body = '\n'.join(line for line in lines[:-1] if line.strip() not in (command, (prompt or '') + command)).strip()
                    if not complete or not body or BAD_OUTPUT.search(output):
                        failed_items.add(item)
                    else:
                        successful_items.add(item)
                        raw[command] = body
            finally:
                client.close()
        data = selected_fields(_network_data(raw, vendor_key), selected_items)
        data = {key: value for key, value in data.items() if key in successful_items - failed_items}
        if 'cpu' in data and data['cpu']['usage_percent'] is None:
            del data['cpu']
        if 'temperature' in data and not data['temperature']['values_celsius']:
            del data['temperature']
        if wants_config:
            data['config_info'] = raw['config_info'] = config
        requested = set(selected_items if selected_items is not None else NETWORK_FIELDS[:-1])
        completed = {key for key in data if key != 'config_info' or config['status'] == 'success'}
        missing = requested - completed
        status = 'partial' if missing and completed else 'failed' if missing else 'success'
        return CollectionResult(True, status, '缺少有效采集证据：' + ', '.join(sorted(missing)) if missing else '',
                                data=data, raw=raw, duration_ms=timer.duration_ms)
    except Exception as exc:
        if wants_config:
            if not config or config.get('status') != 'success':
                config = {'status': 'failed', 'message': '网络设备配置连接或采集失败。'}
            return CollectionResult(False, 'partial' if config['status'] == 'success' else 'failed',
                                    '网络设备配置连接或采集失败。',
                                    data={'config_info': config}, raw={'config_info': config},
                                    duration_ms=getattr(timer, 'duration_ms', 0))
        return CollectionResult(False, 'failed', f'网络设备 SSH 采集失败：{exc}', duration_ms=getattr(timer, 'duration_ms', 0))
