"""Safe, data-only network collection templates.

Templates deliberately never import Python or execute MIB code.  They are
validated at the device boundary and may only describe read-only commands,
bounded text parsing, and numeric SNMP object identifiers.
"""
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from django.core.exceptions import ValidationError
from net.inspections.selection import NETWORK_FUNCTION_ITEMS


TEMPLATE_ITEMS = frozenset({'device_info', 'cpu', 'memory', 'temperature',
                            'interface_status', 'vlan_status', 'logs'}) | frozenset(NETWORK_FUNCTION_ITEMS)
_OID_RE = re.compile(r'^\.?\d+(?:\.\d+)+$')
_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9-]{0,63}$')
_ITEM_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_SYMBOLIC_OID_RE = re.compile(r'^([A-Za-z][A-Za-z0-9-]{0,63})::([A-Za-z][A-Za-z0-9-]{0,63})$')
_MAX_TEXT = 1024 * 1024


def _error(message):
    raise ValidationError({'collection_settings': message})


def _numeric_oid(value):
    return isinstance(value, str) and bool(_OID_RE.fullmatch(value.strip()))


def validate_collection_settings(value):
    """Validate only the network-template subset and return a safe copy."""
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        _error('采集模板必须是 JSON 对象。')
    result = {}
    for key in ('version', 'vendor', 'subtype', 'builtin'):
        if key in value:
            if key == 'builtin' and value[key] is not True:
                _error('builtin 必须为 true。')
            if key == 'version' and value[key] != 1:
                _error('仅支持版本 1 的采集模板。')
            if key not in {'version', 'builtin'} and (not isinstance(value[key], str) or len(value[key]) > 64):
                _error(f'{key} 必须是短文本。')
            result[key] = value[key]
    commands = value.get('commands', {})
    if not isinstance(commands, dict): _error('commands 必须是对象。')
    clean_commands = {}
    for item, values in commands.items():
        if item not in TEMPLATE_ITEMS or not isinstance(values, list) or not 1 <= len(values) <= 32:
            _error('commands 只能为受支持项目提供 1 至 32 条命令。')
        if any(not isinstance(command, str) or not command.strip() or len(command) > 4096 or any(ord(c) < 32 for c in command) or not re.match(r'^(?:show|display|get)\b', command.strip(), re.I) or ';' in command or '&&' in command or '||' in command or '>' in command or '<' in command or re.search(r'\b(?:reboot|reload|shutdown|configure|delete|erase|format|reset)\b', command, re.I) for command in values):
            _error('模板命令必须以 show、display 或 get 开头，且不能包含写入、重定向或命令分隔符。')
        clean_commands[item] = list(values)
    if clean_commands: result['commands'] = clean_commands
    parsers = value.get('parsers', {})
    if not isinstance(parsers, dict): _error('parsers 必须是对象。')
    clean_parsers = {}
    for item, parser in parsers.items():
        if item not in TEMPLATE_ITEMS or not isinstance(parser, dict) or parser.get('engine') not in {'textfsm', 'regex'}:
            _error('parser 必须指定受支持项目以及 textfsm 或 regex 引擎。')
        template = parser.get('template')
        if not isinstance(template, str) or not template or len(template) > 65536:
            _error('解析模板不能为空且不能超过 64 KiB。')
        flags = parser.get('flags', '')
        if not isinstance(flags, str) or set(flags) - set('ims'):
            _error('regex flags 仅支持 i、m、s。')
        if parser['engine'] == 'regex' and re.search(r'\([^)]*[+*][^)]*\)[+*{]', template):
            _error('regex 不允许嵌套重复符。')
        clean_parsers[item] = {'engine': parser['engine'], 'template': template, 'flags': flags}
        empty=parser.get('empty_pattern')
        if empty is not None:
            if not isinstance(empty,str) or len(empty)>1024 or re.search(r'\([^)]*[+*][^)]*\)[+*{]',empty):_error('空表识别规则无效。')
            clean_parsers[item]['empty_pattern']=empty
    if clean_parsers: result['parsers'] = clean_parsers
    transforms=value.get('snmp_transforms',{})
    import math
    if not isinstance(transforms,dict):_error('SNMP 数值解析配置必须是对象。')
    for name, transform in transforms.items():
        if name not in {'cpu','memory_total','memory_used','temperature'} or not isinstance(transform,dict) or set(transform)-{'scale','offset'}:
            _error('不支持的 SNMP 数值解析项目。')
        if any(isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) for number in transform.values()):
            _error('SNMP 倍率和偏移必须是有限数值。')
        if transform.get('scale',1)<=0:_error('SNMP 倍率必须大于零。')
    if transforms:result['snmp_transforms']=transforms
    oids = value.get('snmp_oids', {})
    if not isinstance(oids, dict): _error('snmp_oids 必须是对象。')
    clean_oids = {}
    for name, oid in oids.items():
        if not _ITEM_RE.fullmatch(name) or not isinstance(oid, str) or len(oid) > 128 or not (_numeric_oid(oid) or _SYMBOLIC_OID_RE.fullmatch(oid)):
            _error('SNMP OID 必须为数字 OID 或 MODULE::symbol。')
        clean_oids[name] = oid.lstrip('.')
    if clean_oids: result['snmp_oids'] = clean_oids
    modules = value.get('mib_modules', [])
    if not isinstance(modules, list) or len(modules) > 32: _error('mib_modules 必须是至多 32 项的列表。')
    clean_modules = []
    for module in modules:
        if not isinstance(module, dict) or not _NAME_RE.fullmatch(module.get('name', '')) or not isinstance(module.get('symbols'), dict):
            _error('MIB 模块必须含 name 与 symbols。')
        symbols = module['symbols']
        if len(symbols) > 2048 or any(not _NAME_RE.fullmatch(k) or not _numeric_oid(v) for k, v in symbols.items()):
            _error('MIB 符号必须是受限名称到数字 OID 的映射。')
        clean_modules.append({'name': module['name'], 'symbols': {k: v.lstrip('.') for k, v in symbols.items()}})
    if clean_modules: result['mib_modules'] = clean_modules
    return result


def resolve_symbolic_oid(value, modules):
    """Resolve a stored symbolic OID against an inline, compiled MIB snapshot."""
    if _numeric_oid(value): return value.lstrip('.')
    match = _SYMBOLIC_OID_RE.fullmatch(value or '')
    if not match: raise ValueError('invalid OID')
    module, symbol = match.groups()
    for entry in modules or []:
        if entry.get('name') == module and symbol in entry.get('symbols', {}):
            return entry['symbols'][symbol]
    raise ValueError('unknown MIB symbol')


def compile_mib_text(text):
    """Extract static ASN.1 OID assignments; imports/macros are never executed."""
    if not isinstance(text, str) or not text or len(text) > 1024 * 1024:
        raise ValidationError('MIB 内容为空或超过 1 MiB。')
    source = re.sub(r'--.*$', '', text, flags=re.M)
    header = re.search(r'(?m)^\s*([A-Za-z][A-Za-z0-9-]{0,63})\s+DEFINITIONS\s*::=', source)
    if not header: raise ValidationError('未找到 MIB 模块定义。')
    symbols = {'iso': '1', 'org': '1.3', 'dod': '1.3.6', 'internet': '1.3.6.1', 'private': '1.3.6.1.4', 'enterprises': '1.3.6.1.4.1'}
    pending = re.findall(r'(?ms)^\s*([A-Za-z][A-Za-z0-9-]{0,63})\s+(?:OBJECT\s+IDENTIFIER|OBJECT-TYPE|MODULE-IDENTITY|NOTIFICATION-TYPE)[^{]*::=\s*\{([^}]*)\}', source)
    for _ in range(len(pending) + 1):
        changed = False
        for name, body in pending:
            if name in symbols: continue
            body = re.sub(r'([A-Za-z][A-Za-z0-9-]*)\s*\(\s*\d+\s*\)', r'\1', body)
            tokens = re.findall(r'[A-Za-z][A-Za-z0-9-]*|\d+', body)
            parts = []
            for token in tokens:
                if token.isdigit(): parts.append(token)
                elif token in symbols: parts.extend(symbols[token].split('.'))
                else: parts = []; break
            if parts and len(parts) <= 128:
                symbols[name] = '.'.join(parts); changed = True
        if not changed: break
    extracted = {name: oid for name, oid in symbols.items() if name not in {'iso','org','dod','internet','private','enterprises'}}
    if not extracted: raise ValidationError('MIB 未包含可静态解析的 OID 声明。')
    return {'name': header.group(1), 'symbols': extracted}


def execute_template_commands(settings, send_command, *, timeout=12):
    """Execute validated read-only template commands through an existing SSH session.

    ``send_command`` is supplied by the Netmiko boundary.  Per-item failures
    are reported as structured states so one bad command cannot discard other
    device evidence.  The caller owns the session and never uses this for
    ``config_info`` native backups.
    """
    from net.devices.network.configuration import BAD_OUTPUT
    settings = validate_collection_settings(settings)
    raw, data = {}, {}
    for item, commands in settings.get('commands', {}).items():
        outputs = []
        failed = False
        for command in commands:
            try:
                original = str(send_command(command, read_timeout=timeout))
                if len(original)>_MAX_TEXT:failed=True
                output = original[:_MAX_TEXT]
                raw[command] = output
                if not output.strip() or BAD_OUTPUT.search(output):
                    failed = True
                    continue
                raw[command] = output
                outputs.append(output)
            except Exception:
                failed = True
        if not outputs:
            data[item] = {'status': 'failed', 'message': '模板命令未返回有效证据。'}
            continue
        parser = settings.get('parsers', {}).get(item)
        if parser:
            try:
                records = []
                for output in outputs:
                    records.extend(parse_template_output(parser, output, timeout=min(.25, timeout)))
                if records:
                    data[item] = records[0] if len(records) == 1 else records
                elif parser.get('empty_pattern') and all(parse_template_output({'engine':'regex','template':parser['empty_pattern'],'flags':'im'},output) for output in outputs):
                    data[item] = {'_empty_table':True}
                else:
                    data[item] = {'status': 'missing', 'message': '模板解析未匹配有效记录。'}
            except (ValueError, TimeoutError, re.error):
                data[item] = {'status': 'failed', 'message': '模板解析失败或超时。'}
        else:
            data[item] = {'raw': '\n'.join(outputs)}
        if failed and not (isinstance(data[item], dict) and data[item].get('status')):
            data[item] = {'status':'partial','records':data[item] if isinstance(data[item],list) else [data[item]],'message':'部分命令失败，记录不完整。'}
    return data, raw


def _isolated_parse_worker(engine, template, flags, output, queue):
    """Child process has no device/session objects and returns structured data only."""
    try:
        if engine == 'textfsm':
            import textfsm
            from io import StringIO
            table = textfsm.TextFSM(StringIO(template))
            queue.put(('ok', [dict(zip(table.header, row)) for row in table.ParseText(output)]))
        else:
            re_flags = sum({'i': re.I, 'm': re.M, 's': re.S}[c] for c in flags)
            queue.put(('ok', [m.groupdict() or {'match': m.group(0)} for m in re.finditer(template, output, re_flags)]))
    except BaseException:
        queue.put(('error', None))


_BUILTIN_COMMANDS = {
    'huawei': ['display version', 'display device', 'display cpu-usage', 'display memory-usage', 'display environment', 'display interface brief', 'display vlan', 'display logbuffer'],
    'h3c': ['display version', 'display device', 'display cpu-usage', 'display memory', 'display environment', 'display interface brief', 'display vlan', 'display logbuffer'],
    'ruijie': ['show version', 'show inventory', 'show cpu', 'show memory', 'show environment', 'show interfaces status', 'show vlan', 'show logging'],
    'cisco': ['show version', 'show inventory', 'show processes cpu', 'show processes memory', 'show environment all', 'show interfaces status', 'show vlan brief', 'show logging | last 100'],
}
_BUILTIN_ITEMS = ('device_info', 'device_info', 'cpu', 'memory', 'temperature', 'interface_status', 'vlan_status', 'logs')


def builtin_collection_settings(vendor, subtype='other'):
    """Return a reviewable default using the established vendor command table.

    The returned commands intentionally have no parser override: the native
    collector retains its compatible parsing.  Unsupported Sangfor models are
    explicit and require an administrator-supplied template.
    """
    vendor = (vendor or 'generic').strip().lower()
    subtype = (subtype or 'other').strip().lower()
    if vendor == 'sangfor':
        return {'version': 1, 'vendor': vendor, 'subtype': subtype, 'unsupported': '深信服型号命令集未内置；请提供经验证的只读模板。'}
    commands = _BUILTIN_COMMANDS.get(vendor)
    if not commands:
        return {'version': 1, 'vendor': vendor, 'subtype': subtype, 'unsupported': '该厂商没有内置命令模板。'}
    grouped = {}
    for item, command in zip(_BUILTIN_ITEMS, commands):
        grouped.setdefault(item, []).append(command)
    return {'version': 1, 'vendor': vendor, 'subtype': subtype, 'builtin': True, 'commands': grouped}


def parse_template_output(parser, output, timeout=0.25):
    """Run parsing in a disposable process and drain its bounded result queue."""
    import multiprocessing
    import queue as queue_module
    import time
    parser = dict(parser)
    context = multiprocessing.get_context('spawn')
    queue = context.Queue(1)
    process = context.Process(target=_isolated_parse_worker, args=(parser['engine'], parser['template'], parser.get('flags', ''), str(output)[:_MAX_TEXT], queue))
    deadline = time.monotonic() + max(2.0, min(float(timeout), 2.0))
    try:
        process.start()
        while time.monotonic() < deadline:
            try:
                status, value = queue.get(timeout=min(.05, deadline - time.monotonic()))
                if status != 'ok':
                    raise ValueError('template parse failed')
                return value
            except queue_module.Empty:
                if not process.is_alive():
                    break
        raise TimeoutError('template parse timed out') if process.is_alive() else ValueError('template parse failed')
    finally:
        if process.is_alive():
            process.terminate()
        process.join(.5)
        queue.close()
        queue.join_thread()
