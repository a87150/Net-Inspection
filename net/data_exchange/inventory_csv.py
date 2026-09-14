import csv
import io
import ipaddress
from pathlib import Path
from decimal import Decimal, InvalidOperation
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction

from net.models import Computer, Domain_Account, Domain_Computer, Network_Device, People, SecurityDevice, Server
from net.data_exchange.xlsx import build_xlsx, read_xlsx_rows


IMPORTABLE_ENTITIES = {'people', 'networks', 'servers', 'monitors'}
MAX_CSV_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 5000
MAX_CSV_COLUMNS = 64
MAX_CSV_CELL_CHARS = 10000


def _text(value):
    return str(value or '').strip()


def _boolean(value):
    normalized = _text(value).lower()
    if normalized in {'1', 'true', 'yes', '是', '启用', '在职'}:
        return True
    if normalized in {'0', 'false', 'no', '否', '停用', '离职'}:
        return False
    raise ValueError('应填写“是/否”或“true/false”')


def _ip(value):
    normalized = _text(value)
    return str(ipaddress.ip_address(normalized))


def _date(value):
    normalized = _text(value)
    if not normalized:
        return None
    return datetime.strptime(normalized, '%Y-%m-%d').date()


def _integer(value):
    number = int(_text(value))
    if not 1 <= number <= 65535:
        raise ValueError('端口必须在 1-65535 之间')
    return number


def _non_negative_integer(value):
    normalized = _text(value)
    if not normalized:
        return None
    number = int(normalized)
    if number < 0:
        raise ValueError('必须是非负整数')
    return number


def _gib(value):
    normalized = _text(value)
    if not normalized:
        return None
    try:
        number = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError('必须是 GiB 数值') from exc
    if number < 0:
        raise ValueError('必须是非负 GiB 数值')
    return number


def _server_type(value):
    normalized = _text(value).lower()
    aliases = {'linux': 'linux', 'windows': 'windows', 'win': 'windows', '微软': 'windows'}
    if normalized not in aliases:
        raise ValueError('服务器类型应填写 Linux 或 Windows')
    return aliases[normalized]


ENTITY_SPECS = {
    'people': {
        'name': '人员',
        'model': People,
        'key': 'employee_id',
        'columns': [
            ('姓名', 'name', _text), ('工号', 'employee_id', _text), ('邮箱', 'email', _text),
            ('手机号', 'phone', _text),
            ('部门', 'department', _text), ('上级', 'leader', _text), ('是否在职', 'is_active', _boolean),
            ('入职日期', 'hire_date', _date), ('离职日期', 'departure_date', _date),
        ],
        'sample': ('张三', 'H10001', 'zhangsan@example.invalid', '13800138000', '信息技术部', '李经理', '是', '2026-01-15', ''),
    },
    'accounts': {
        'name': '域账号',
        'model': Domain_Account,
        'key': 'login_name',
        'columns': [
            ('账号名称', 'account_name', _text), ('登录名', 'login_name', _text),
            ('是否启用', 'is_active', _boolean), ('组织单位', 'ou', _text),
            ('允许登录计算机', 'allowed_workstations', _text),
            ('最后登录日期', 'last_login_date', _date),
        ],
    },
    'computers': {
        'name': '计算机',
        'model': Computer,
        'key': 'computer_name',
        'columns': [
            ('计算机名', 'computer_name', _text), ('操作系统', 'os', _text),
            ('当前用户', 'user_name', _text), ('最后上报时间', 'last_report_at', _text),
            ('IP地址', 'ip_addresses', _text),
            ('MAC地址', 'mac_addresses', _text), ('系统版本', 'os_version', _text),
            ('系统构建号', 'os_build', _text), ('系统安装时间', 'system_installed_at', _text),
            ('制造商', 'manufacturer', _text), ('型号', 'model', _text),
            ('序列号', 'serial_number', _text), ('系统架构', 'architecture', _text),
            ('CPU 型号', 'cpu_model', _text),
            ('CPU 物理核心数', 'cpu_physical_core_count', _non_negative_integer),
            ('CPU 逻辑处理器数', 'cpu_logical_processor_count', _non_negative_integer),
            ('内存总量', 'memory_total_gb', _gib), ('磁盘总量', 'disk_total_gb', _gib),
        ],
    },
    'domain_computers': {
        'name': '域计算机',
        'model': Domain_Computer,
        'key': 'computer_name',
        'columns': [
            ('计算机名', 'computer_name', _text), ('是否启用', 'is_active', _boolean),
            ('组织单位', 'ou', _text), ('操作系统', 'os', _text),
            ('最后登录日期', 'last_login_date', _date),
        ],
    },
    'networks': {
        'name': '网络设备',
        'model': Network_Device,
        'key': 'ip',
        'columns': [
            ('设备名称', 'device_name', _text), ('IP地址', 'ip', _ip),
            ('设备类型', 'device_type', _text), ('厂商', 'vendor', _text),
            ('连接方式', 'connection_type', _text), ('深信服 API地址', 'api_url', _text), ('深信服共享密钥', 'api_shared_secret', _text), ('校验HTTPS证书', 'verify_ssl', _boolean), ('SSH端口', 'port', _integer),
            ('型号', 'model', _text), ('CPU 型号', 'cpu_model', _text),
            ('内存总量', 'memory_total_gb', _gib), ('磁盘总量', 'disk_total_gb', _gib),
            ('端口总数', 'port_count', _non_negative_integer),
            ('VLAN 数量', 'vlan_count', _non_negative_integer),
            ('SSH账号', 'username', _text), ('SSH密码', 'password', _text),
            ('SNMP 版本', 'snmp_version', _text),
            ('SNMP 端口', 'snmp_port', _integer),
            ('SNMP Community', 'snmp_community', _text),
            ('SNMPv3 用户名', 'snmp_username', _text),
            ('安全级别', 'snmp_security_level', _text),
            ('认证协议', 'snmp_auth_protocol', _text),
            ('认证密码', 'snmp_auth_password', _text),
            ('加密协议', 'snmp_priv_protocol', _text),
            ('加密密码', 'snmp_priv_password', _text),
            ('上下文', 'snmp_context_name', _text),
            ('重试次数', 'snmp_retries', _non_negative_integer),
        ],
        'secret_fields': {
            'password', 'api_shared_secret', 'snmp_community', 'snmp_auth_password',
            'snmp_priv_password',
        },
        'template_fields': (
            'device_name', 'ip', 'device_type', 'vendor', 'connection_type', 'api_url', 'api_shared_secret', 'verify_ssl',
            'port', 'username', 'password', 'snmp_version', 'snmp_port',
            'snmp_community', 'snmp_username', 'snmp_security_level',
            'snmp_auth_protocol', 'snmp_auth_password', 'snmp_priv_protocol',
            'snmp_priv_password', 'snmp_context_name', 'snmp_retries',
        ),
        'template_samples': (
            (
                '核心交换机', '192.0.2.10', '交换机', 'H3C', 'ssh', '', '', '是', '22',
                'readonly', 'CHANGE-ME', 'v2c', '161', '', '', 'noAuthNoPriv', '', '', '', '', '', '1',
            ),
            (
                '接入交换机', '192.0.2.11', '交换机', 'Example', 'snmp', '', '', '是', '22',
                '', '', 'v2c', '161', 'CHANGE-ME', '', 'noAuthNoPriv', '', '', '', '', '', '1',
            ),
            (
                '汇聚交换机', '192.0.2.12', '交换机', 'Example', 'hybrid', '', '', '是', '22',
                'readonly', 'CHANGE-ME', 'v3', '161', '', 'snmp-reader', 'authPriv', 'sha256', 'CHANGE-ME', 'aes128', 'CHANGE-ME', '', '1',
            ),
            (
                '深信服上网行为管理', '192.0.2.13', 'ac_gateway', 'sangfor', 'sangfor_api',
                'https://192.0.2.13:443', 'CHANGE-ME', '是', '22',
                '', '', 'v2c', '161', '', '', 'noAuthNoPriv', '', '', '', '', '', '1',
            ),
        ),
        'sample': (
            '核心交换机', '192.0.2.10', '交换机', 'H3C', 'hybrid', '22',
            'S5560X', 'Intel Atom', '4', '8', '48', '10', 'readonly',
            'DEMO-ONLY-NOT-A-SECRET', 'v3', '161', '', 'snmp-reader',
            'authPriv', 'sha256', 'DEMO-ONLY-NOT-A-SECRET', 'aes128',
            'DEMO-ONLY-NOT-A-SECRET', 'demo-context', '1',
        ),
    },
    'servers': {
        'name': '服务器',
        'model': Server,
        'key': 'ip',
        'columns': [
            ('服务器名称', 'name', _text), ('IP地址', 'ip', _ip),
            ('服务器类型', 'server_type', _server_type), ('操作系统', 'os', _text),
            ('管理端口', 'port', _integer), ('SSH账号', 'username', _text),
            ('SSH密码', 'password', _text), ('Windows API地址', 'api_url', _text),
            ('API令牌', 'api_token', _text), ('校验HTTPS证书', 'verify_ssl', _boolean),
            ('系统版本', 'os_version', _text), ('系统构建号', 'os_build', _text),
            ('系统安装时间', 'system_installed_at', _text),
            ('制造商', 'manufacturer', _text), ('型号', 'model', _text),
            ('序列号', 'serial_number', _text), ('系统架构', 'architecture', _text),
            ('CPU 型号', 'cpu_model', _text), ('内存总量', 'memory_total_gb', _gib),
            ('CPU 物理核心数', 'cpu_physical_core_count', _non_negative_integer),
            ('CPU 逻辑处理器数', 'cpu_logical_processor_count', _non_negative_integer),
            ('磁盘总量', 'disk_total_gb', _gib),
        ],
        'secret_fields': {'password', 'api_token'},
        'template_fields': (
            'name', 'ip', 'server_type', 'port', 'username', 'password',
            'api_url', 'api_token', 'verify_ssl',
        ),
        'template_samples': (
            (
                'Linux 应用服务器', '192.0.2.20', 'Linux', '22', 'readonly',
                'CHANGE-ME', '', '', '是',
            ),
            (
                'Windows 应用服务器', '192.0.2.21', 'Windows', '9180', '', '',
                'https://192.0.2.21:9180/inspection', 'CHANGE-ME', '是',
            ),
        ),
        'sample': ('应用服务器', '192.0.2.20', 'Linux', 'Ubuntu 24.04', '22', 'readonly', 'CHANGE-ME', '', '', '否', '24.04', '', '2026-01-15', 'Example', 'Rack Server', 'DEMO-SRV-001', 'x86_64', 'Xeon', '32', '8', '16', '512'),
    },
    'monitors': {
        'name': '安防设备',
        'model': SecurityDevice,
        'key': 'ip',
        'columns': [
            ('设备名称', 'device_name', _text), ('IP地址', 'ip', _ip),
            ('设备类型', 'device_type', _text), ('厂商', 'vendor', _text),
            ('API地址', 'api_url', _text), ('API账号', 'api_username', _text),
            ('API密码', 'api_password', _text), ('API令牌', 'api_token', _text),
            ('校验HTTPS证书', 'verify_ssl', _boolean),
            ('型号', 'model', _text), ('CPU 型号', 'cpu_model', _text),
            ('内存总量', 'memory_total_gb', _gib), ('磁盘总量', 'disk_total_gb', _gib),
        ],
        'secret_fields': {'api_password', 'api_token'},
        'template_fields': (
            'device_name', 'ip', 'device_type', 'vendor', 'api_url',
            'api_username', 'api_password', 'api_token', 'verify_ssl',
        ),
        'template_samples': (
            (
                '前门摄像机', '192.0.2.30', '摄像机', 'Hikvision',
                'https://192.0.2.30/api/status', 'readonly', 'CHANGE-ME', '', '是',
            ),
            (
                '机房门禁', '192.0.2.31', '门禁', 'Example',
                'https://192.0.2.31/api/status', '', '', 'CHANGE-ME', '是',
            ),
        ),
        'sample': ('前门门禁闸机', '192.0.2.30', '门禁闸机', 'Dahua', 'https://192.0.2.30/api/status', 'readonly', 'CHANGE-ME', '', '否', 'ASI7213Y', 'ARM', '2', '8'),
    },
}


def get_spec(entity):
    try:
        return ENTITY_SPECS[entity]
    except KeyError as exc:
        raise ValueError('不支持的数据类型') from exc


def _template_columns(spec):
    requested = spec.get('template_fields')
    if not requested:
        return spec['columns']
    columns_by_field = {column[1]: column for column in spec['columns']}
    return [columns_by_field[field] for field in requested]


def _template_samples(spec):
    return spec.get('template_samples') or (spec['sample'],)


def export_csv(entity, template_only=False):
    spec = get_spec(entity)
    columns = _template_columns(spec) if template_only else [
        column for column in spec['columns']
        if column[1] not in spec.get('secret_fields', set())
    ]
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow([label for label, _, _ in columns])
    if template_only:
        writer.writerows(_template_samples(spec))
    else:
        for obj in spec['model'].objects.all().order_by('pk'):
            row = []
            for _, field, _ in columns:
                value = getattr(obj, field, '')
                if isinstance(value, bool):
                    value = '是' if value else '否'
                row.append(value if value is not None else '')
            writer.writerow(row)
    return '\ufeff' + stream.getvalue()


def export_xlsx_template(entity):
    spec = get_spec(entity)
    columns = _template_columns(spec)
    return build_xlsx(
        ([label for label, _, _ in columns], *_template_samples(spec)),
        sheet_name=spec['name'],
    )


def import_file(entity, uploaded_file):
    suffix = Path(uploaded_file.name or '').suffix.casefold()
    if suffix == '.csv':
        return import_csv(entity, uploaded_file)
    if suffix != '.xlsx':
        raise ValueError('仅支持 CSV 或 Excel (.xlsx) 文件')
    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > MAX_CSV_UPLOAD_BYTES:
        raise ValueError('文件不能超过 2 MB')
    rows = read_xlsx_rows(
        uploaded_file,
        max_rows=MAX_CSV_ROWS + 1,
        max_columns=MAX_CSV_COLUMNS,
        max_cell_chars=MAX_CSV_CELL_CHARS,
    )
    stream = io.StringIO(newline='')
    csv.writer(stream).writerows(rows)
    converted = io.BytesIO(('\ufeff' + stream.getvalue()).encode('utf-8'))
    return import_csv(entity, converted)


def _decode_upload(uploaded_file):
    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > MAX_CSV_UPLOAD_BYTES:
        raise ValueError('文件不能超过 2 MB')
    try:
        raw = uploaded_file.read(MAX_CSV_UPLOAD_BYTES + 1)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError('读取上传文件失败') from exc
    if not isinstance(raw, bytes):
        raise ValueError('读取上传文件失败')
    if len(raw) > MAX_CSV_UPLOAD_BYTES:
        raise ValueError('文件不能超过 2 MB')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError('文件编码无法识别，请使用 UTF-8 或 Excel CSV 格式')


def _field_label(spec, field_name):
    labels = {field: label for label, field, _converter in spec['columns']}
    return labels.get(field_name, '数据' if field_name == '__all__' else field_name)


def _validation_messages(spec, row_number, exc):
    messages = []
    for field_name, field_errors in exc.message_dict.items():
        label = _field_label(spec, field_name)
        messages.extend(
            f'第 {row_number} 行“{label}”：{message}'
            for message in field_errors
        )
    return messages


def _key_converter(spec):
    return next(
        converter
        for _label, field, converter in spec['columns']
        if field == spec['key']
    )


def _existing_objects_by_key(spec, imported_keys):
    model = spec['model']
    key_field = spec['key']
    if key_field != 'ip':
        return {
            getattr(obj, key_field): obj
            for obj in model.objects.select_for_update().filter(
                **{f'{key_field}__in': imported_keys},
            )
        }

    converter = _key_converter(spec)
    existing = {}
    for obj in model.objects.all().iterator(chunk_size=500):
        try:
            normalized_key = converter(getattr(obj, key_field))
        except (TypeError, ValueError):
            continue
        if normalized_key not in imported_keys:
            continue
        if normalized_key in existing:
            raise ValueError(f'数据库中存在重复 IP 地址：{normalized_key}')
        existing[normalized_key] = obj
    return existing


def _validated_candidates(spec, prepared):
    imported_keys = {values[spec['key']] for _row_number, values in prepared}
    existing = _existing_objects_by_key(spec, imported_keys)
    candidates = []
    validation_errors = []
    for row_number, values in prepared:
        key_value = values[spec['key']]
        candidate = existing.get(key_value)
        created = candidate is None
        if created:
            candidate = spec['model']()
        if spec['model'] is People:
            if candidate.sync_source_id or candidate.source not in ('manual', 'csv'):
                validation_errors.append(f'第 {row_number} 行：CSV 不能修改目录来源所有的人员。')
                continue
            candidate.source = 'csv'
            candidate.platform_user_id = ''
            candidate.last_synced_at = None
        for field_name, value in values.items():
            setattr(candidate, field_name, value)
        try:
            candidate.full_clean()
        except ValidationError as exc:
            validation_errors.extend(_validation_messages(spec, row_number, exc))
        candidates.append((candidate, created))
    if validation_errors:
        raise ValueError('；'.join(validation_errors[:20]))
    return candidates


def import_csv(entity, uploaded_file):
    if entity not in IMPORTABLE_ENTITIES:
        raise ValueError('该数据由系统自动获取，不支持手动导入')
    spec = get_spec(entity)
    try:
        reader = csv.DictReader(
            io.StringIO(_decode_upload(uploaded_file)), strict=True,
        )
        if not reader.fieldnames:
            raise ValueError('CSV 文件没有表头')
        if len(reader.fieldnames) > MAX_CSV_COLUMNS:
            raise ValueError(f'CSV 最多允许 {MAX_CSV_COLUMNS} 列')

        aliases = {}
        for label, field, converter in spec['columns']:
            aliases[label.strip()] = (field, converter)
            aliases[field] = (field, converter)
        if entity == 'people':
            for alias in ('电话', '手机号码', '联系电话', 'mobile'):
                aliases[alias] = ('phone', _text)
        normalized_headers = [_text(name) for name in reader.fieldnames]
        if any(len(name) > MAX_CSV_CELL_CHARS for name in normalized_headers):
            raise ValueError(f'单元格内容不能超过 {MAX_CSV_CELL_CHARS} 个字符')
        unknown_headers = [name for name in normalized_headers if name not in aliases]
        if unknown_headers:
            displayed = '、'.join(f'“{name or "空列名"}”' for name in unknown_headers)
            raise ValueError(f'列{displayed}不属于{spec["name"]}导入模板')
        mapped_fields = [aliases[name][0] for name in normalized_headers]
        duplicate_fields = sorted({
            field for field in mapped_fields if mapped_fields.count(field) > 1
        })
        if duplicate_fields:
            raise ValueError(f'导入列重复：{"、".join(duplicate_fields)}')
        missing_key = spec['key'] not in set(mapped_fields)
        if missing_key:
            key_label = next(
                label for label, field, _ in spec['columns']
                if field == spec['key']
            )
            raise ValueError(f'缺少主键列：{key_label}')

        prepared = []
        errors = []
        blank_people_employee_id_rows = []
        seen_keys = {}
        for row_number, row in enumerate(reader, start=2):
            if row_number > MAX_CSV_ROWS + 1:
                raise ValueError(f'CSV 最多允许 {MAX_CSV_ROWS} 行数据')
            cell_values = [
                value
                for raw_value in row.values()
                for value in (raw_value if isinstance(raw_value, list) else [raw_value])
            ]
            if any(len(_text(value)) > MAX_CSV_CELL_CHARS for value in cell_values):
                raise ValueError(
                    f'第 {row_number} 行单元格内容不能超过 '
                    f'{MAX_CSV_CELL_CHARS} 个字符',
                )
            extra_values = row.get(None) or []
            if any(_text(value) for value in extra_values):
                errors.append(f'第 {row_number} 行列数超过表头')
                continue
            if not any(_text(value) for value in row.values()):
                continue
            values = {}
            for original_header, raw_value in row.items():
                mapping = aliases.get(_text(original_header))
                if not mapping:
                    continue
                field, converter = mapping
                # Missing phone columns preserve existing values; an explicit
                # blank cell clears the phone. Short CSV rows remain absent.
                if entity == 'people' and field == 'phone' and raw_value is None:
                    continue
                if _text(raw_value) == '' and field != spec['key'] and not (entity == 'people' and field == 'phone'):
                    if converter not in {_date, _non_negative_integer, _gib}:
                        continue
                try:
                    values[field] = converter(raw_value)
                except (ValueError, TypeError) as exc:
                    errors.append(f'第 {row_number} 行“{original_header}”：{exc}')
            key_value = values.get(spec['key'])
            if not key_value:
                if entity == 'people':
                    blank_people_employee_id_rows.append(row_number)
                else:
                    errors.append(f'第 {row_number} 行主键不能为空')
            elif key_value in seen_keys:
                errors.append(
                    f'第 {row_number} 行主键重复'
                    f'（首次出现在第 {seen_keys[key_value]} 行）',
                )
            else:
                seen_keys[key_value] = row_number
            prepared.append((row_number, values))
    except csv.Error as exc:
        raise ValueError(f'CSV 格式错误：{exc}') from exc

    if blank_people_employee_id_rows:
        displayed_rows = '、'.join(
            str(row_number) for row_number in blank_people_employee_id_rows[:20]
        )
        remaining_count = len(blank_people_employee_id_rows) - 20
        remaining_text = f'，另有 {remaining_count} 行' if remaining_count > 0 else ''
        errors.insert(
            0,
            f'发现 {len(blank_people_employee_id_rows)} 条人员记录的“工号”为空'
            f'（第 {displayed_rows} 行{remaining_text}），请补充后重新导入；'
            '本次未写入任何数据',
        )
    if errors:
        raise ValueError('；'.join(errors[:20]))
    if not prepared:
        raise ValueError('文件中没有可导入的数据')

    try:
        with transaction.atomic():
            candidates = _validated_candidates(spec, prepared)
            for candidate, _created in candidates:
                candidate.save()
    except DatabaseError as exc:
        raise ValueError('数据库写入失败，请检查数据后重试') from exc
    created_count = sum(int(created) for _candidate, created in candidates)
    updated_count = len(candidates) - created_count
    return created_count, updated_count
