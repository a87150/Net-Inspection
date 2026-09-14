"""Resolve immutable non-secret device settings before enqueueing."""
from copy import deepcopy
import hashlib
import json
import math
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q

VENDORS = {
    'networks': [('', '全部厂商'), ('huawei', '华为'), ('h3c', '华三'),
                 ('ruijie', '锐捷'), ('sangfor', '深信服'), ('cisco', '思科'), ('generic', '其他')],
    'monitors': [('', '全部厂商'), ('hikvision', '海康威视'), ('dahua', '大华'),
                 ('uniview', '宇视'), ('tiandy', '天地伟业'), ('zkteco', '中控智慧'), ('generic', '其他')],
    'servers': [],
}


def vendor_choices(kind, current=''):
    """Offer category-specific brands, retaining only this record's legacy value."""
    choices = list(VENDORS[kind])
    if kind != 'servers' and current and current not in dict(choices):
        choices.append((current, current + '（已有值）'))
    return choices

SUBTYPES = {
 'networks': [('','全部类型'),('router','路由器'),('switch','交换机'),('ac','无线控制器 AC'),('ac_gateway','上网行为管理 / 安全网关 AC'),('ap','无线 AP'),('firewall','防火墙'),('other','其他')],
 'monitors': [('','全部类型'),('nvr','录像机'),('camera','监控摄像头'),('access','门禁'),('other','其他')],
 'servers': [('','全部类型'),('linux','Linux'),('windows','Windows')],
}
ALIASES = {'华为':'huawei','华三':'h3c','锐捷':'ruijie','深信服':'sangfor','思科':'cisco','海康':'hikvision','海康威视':'hikvision','大华':'dahua','其它':'generic','宇视':'uniview','天地伟业':'tiandy','中控':'zkteco','中控智慧':'zkteco'}
TYPE_ALIASES = {'路由':'router','路由器':'router','交换':'switch','交换机':'switch','无线控制器':'ac','上网行为管理':'ac_gateway','安全网关':'ac_gateway','无线ap':'ap','防火墙':'firewall','录像机':'nvr','dvr':'nvr','监控':'camera','摄像头':'camera','摄像机':'camera','门禁':'access','门禁控制器':'access'}
SECRET_FIELDS = ('snmp_community','snmp_auth_password','snmp_priv_password')
SNMP_FIELDS = ('snmp_version','snmp_port','snmp_security_level','snmp_username','snmp_auth_protocol','snmp_priv_protocol','snmp_context_name','snmp_retries')
NETWORK_KEYS = {'version','vendor','subtype','commands','parsers','snmp_oids','mib_modules','snmp_transforms'}


def normalize_vendor(value):
    value = str(value or '').strip().casefold()
    return ALIASES.get(value, value)


def normalize_subtype(kind, value):
    value = str(value or '').strip().casefold()
    return TYPE_ALIASES.get(value, value)


def merge(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def collection_method_choices(kind, item):
    if item == 'inspection_collection':return []
    if kind == 'networks':
        from net.inspections.selection import NETWORK_FUNCTION_ITEMS
        if item in NETWORK_FUNCTION_ITEMS:return [('ssh','SSH 命令')]
        if item == 'traffic':return [('snmp','SNMP')]
        if item in {'logs','config_info'}:return [('ssh','SSH 命令')]
        return [('snmp','SNMP'),('ssh','SSH 命令'),('auto','按设备默认方式')]
    if kind == 'servers':return [('auto','按操作系统：Linux SSH / Windows HTTP')]
    choices=[('auto','继承设备协议'),('api','HTTP API')]
    if item in {'device_info','status_data'}:choices.append(('snmp','SNMP'))
    if item == 'status_data':choices.append(('ping','Ping 在线检查'))
    return choices


def validate_settings(kind, value):
    from net.inspections.issues import PROJECT_RULES, METRIC_DEFAULTS
    if kind not in SUBTYPES or not isinstance(value, dict):
        raise ValidationError('设备配置必须是当前类别的 JSON 对象。')
    allowed = NETWORK_KEYS | {'selected_items','alert_items','severity_overrides','thresholds','protocol','snmp','item_methods','item_enabled'}
    if set(value) - allowed:
        raise ValidationError('配置包含不支持的字段。')
    if len(json.dumps(value, ensure_ascii=False)) > 262144:
        raise ValidationError('配置内容超过 256 KB。')
    rules = PROJECT_RULES[kind]
    states=value.get('item_enabled',{})
    if not isinstance(states,dict) or any(item not in rules or not isinstance(enabled,bool) for item,enabled in states.items()):
        raise ValidationError('项目适用状态无效。')
    methods=value.get('item_methods',{})
    if not isinstance(methods,dict) or any(item not in rules or method not in dict(collection_method_choices(kind,item)) for item,method in methods.items()):
        raise ValidationError('巡检项目的采集方式不适用于当前设备类别。')

    for field in ('selected_items','alert_items'):
        items = value.get(field)
        if items is not None and (not isinstance(items,list) or any(not isinstance(x,str) or x not in rules for x in items) or len(items)!=len(set(items))):
            raise ValidationError('巡检或告警项目不属于当前设备类别。')
    if 'inspection_collection' in value.get('selected_items',[]):
        raise ValidationError('采集连接属于告警项目，请勿作为采集字段。')
    for field in ('severity_overrides','thresholds'):
        if field in value and not isinstance(value[field],dict):
            raise ValidationError('规则和阈值必须是 JSON 对象。')
    for key, level in value.get('severity_overrides',{}).items():
        if key.removeprefix('missing.') not in rules or level not in {'info','warning','critical'}:
            raise ValidationError('问题等级配置无效。')
    for key, threshold in value.get('thresholds',{}).items():
        if key not in METRIC_DEFAULTS.get(kind,{}) or isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or not 0 < threshold <= (200 if key=='temperature' else 100):
            raise ValidationError('指标阈值不属于当前设备类别或超出范围。')
    snmp = value.get('snmp',{})
    if not isinstance(snmp,dict) or set(snmp)-set(SNMP_FIELDS):
        raise ValidationError('SNMP 凭据必须在独立密码字段填写，不能放入模板。')
    from net.models.devices import Network_Device
    for key, item in snmp.items():
        Network_Device._meta.get_field(key).clean(item, None)
    if value.get('protocol','auto') not in {'auto','api','snmp','ping'}:
        raise ValidationError('安防采集协议无效。')
    if kind != 'monitors' and ('protocol' in value or 'snmp' in value):
        raise ValidationError('网络设备 SNMP 连接参数请在设备基本配置中填写。')
    if any(key in value for key in ('commands','parsers','snmp_oids','mib_modules','snmp_transforms')):
        from net.devices.network.templates import validate_collection_settings
        validate_collection_settings({k:v for k,v in value.items() if k in NETWORK_KEYS})
        if value.get('snmp_oids'):
            from net.devices.network import snmp as snmp_module
            supported = {'cpu', 'memory_total', 'memory_used', 'temperature'} | {
                name.lower() for name, oid in vars(snmp_module).items()
                if name.isupper() and isinstance(oid, str) and oid.startswith('1.3.6.')
            }
            if set(value['snmp_oids']) - supported:
                raise ValidationError('Unsupported SNMP metric key; use a standard MIB field or cpu, memory_total, memory_used, temperature.')

    if kind != 'networks' and (value.get('commands') or value.get('parsers')):
        raise ValidationError('自定义 SSH 命令与解析仅用于网络设备。')
    for key in ('severity_overrides','thresholds'):
        if key in value and not isinstance(value[key],dict):
            raise ValidationError('规则和阈值必须是 JSON 对象。')
    return value


def _cipher():
    try:
        return Fernet(settings.DEVICE_BACKUP_ENCRYPTION_KEY)
    except Exception:
        raise ValidationError('保存 SNMP 凭据需配置 DEVICE_BACKUP_ENCRYPTION_KEY。') from None


def encrypt_credentials(values):
    return _cipher().encrypt(json.dumps(values).encode()).decode() if values else ''


def decrypt_credentials(binding):
    if not binding or not binding.encrypted_credentials:
        return {}
    try:
        return json.loads(_cipher().decrypt(binding.encrypted_credentials.encode()))
    except (InvalidToken, ValueError, TypeError):
        raise ValidationError('设备 SNMP 凭据无法解密，请检查加密密钥。') from None


def supported_collection_items(kind, asset, effective):
    from net.inspections.selection import NETWORK_FIELDS, NETWORK_FUNCTION_ITEMS, LINUX_FIELDS, WINDOWS_FIELDS, SECURITY_FIELDS
    if kind == 'networks':
        is_sangfor_api = getattr(asset, 'connection_type', '') == 'sangfor_api'
        allowed = set(NETWORK_FIELDS) | {'traffic'}
        if is_sangfor_api:
            # The documented Open API is the complete capability surface.  Keep
            # item_enabled overrides effective instead of returning early here.
            from net.devices.network.sangfor import DEFAULT_ITEMS
            allowed = set(DEFAULT_ITEMS)
        else:
            # Function items require an explicit executable template, not just a type label.
            allowed.update(item for item in NETWORK_FUNCTION_ITEMS if effective.get('commands',{}).get(item) and effective.get('parsers',{}).get(item))
        if getattr(asset, 'connection_type', '') == 'snmp':
            allowed.discard('logs')
    elif kind == 'servers':
        allowed = set(WINDOWS_FIELDS if str(getattr(asset, 'server_type', '')).lower() == 'windows' else LINUX_FIELDS)
    else:
        allowed = set(SECURITY_FIELDS)
        protocol = effective.get('protocol', 'auto')
        has_snmp = bool(effective.get('snmp')) or effective.get('_credentials_identity', hashlib.sha256(b'').hexdigest()) != hashlib.sha256(b'').hexdigest()
        if protocol == 'ping' or (protocol == 'auto' and not getattr(asset, 'api_url', '') and not has_snmp):
            allowed = {'status_data'}
        elif protocol == 'snmp' or (protocol == 'auto' and not getattr(asset, 'api_url', '') and has_snmp):
            allowed = {'device_info', 'status_data'}
        elif normalize_subtype(kind, getattr(asset, 'device_type', '')) == 'access':
            allowed -= {'channel_status', 'storage_status'}
    for item,method in effective.get('item_methods',{}).items():
        if (kind != 'networks' or not is_sangfor_api) and method!='auto' and method in dict(collection_method_choices(kind,item)):
            allowed.add(item)
    allowed -= {item for item,enabled in effective.get('item_enabled',{}).items() if not enabled}
    if kind == 'networks' and getattr(asset, 'connection_type', '') != 'sangfor_api':
        allowed.add('config_info')  # Daily backups remain independent for SSH/SNMP devices.
    return sorted(allowed)


def collection_settings_for_assets(kind, assets):
    from net.models import DeviceCollectionTemplate, DeviceCollectionBinding
    assets = list(assets)
    templates = list(DeviceCollectionTemplate.objects.filter(kind=kind))
    bindings = {str(row.target_id): row for row in DeviceCollectionBinding.objects.filter(
        kind=kind, target_id__in=[asset.pk for asset in assets]).select_related('template')}
    return {str(asset.pk): resolve_collection_settings(kind, asset, templates=templates, bindings=bindings) for asset in assets}


def template_settings(template, *, templates=None):
    """Resolve only the explicitly declared parent chain, preserving field sources."""
    available = {row.pk: row for row in templates} if templates is not None else None
    chain, seen = [], set()
    while template is not None:
        if template.pk in seen:
            raise ValidationError('模板继承关系存在循环。')
        seen.add(template.pk)
        chain.append(template)
        template = (available.get(template.parent_id) if available is not None else template.parent) if template.parent_id else None
    result, sources = {}, {}
    for row in reversed(chain):
        result = merge(result, row.settings)
        for section, values in row.settings.items():
            if isinstance(values, dict):
                for key in values:
                    sources[section + '.' + key] = row.name
    if chain:
        result['_templates'] = [str(row.pk) for row in reversed(chain)]
        result['_rule_sources'] = sources
    return result


def resolve_collection_settings(kind, asset, *, templates=None, bindings=None):
    from net.models import DeviceCollectionTemplate, DeviceCollectionBinding
    vendor = '' if kind == 'servers' else normalize_vendor(getattr(asset,'vendor','') or getattr(asset,'manufacturer',''))
    subtype = normalize_subtype(kind, getattr(asset,'server_type','') if kind=='servers' else getattr(asset,'device_type',''))
    templates = list(templates) if templates is not None else list(DeviceCollectionTemplate.objects.filter(kind=kind))
    rows = {(r.vendor,r.subtype):r for r in templates if r.is_enabled}
    binding = bindings.get(str(asset.pk)) if bindings is not None else DeviceCollectionBinding.objects.filter(kind=kind,target_id=asset.pk).select_related('template').first()
    leaf = rows.get((vendor,subtype)) or rows.get((vendor,''))
    if binding and binding.template_id and binding.template.is_enabled:
        if binding.template.kind != kind:
            raise ValidationError('设备模板类别不匹配。')
        leaf = binding.template
    result = template_settings(leaf, templates=templates)
    if binding:
        result = merge(result,binding.overrides)
        sources = result.setdefault('_rule_sources', {})
        for section, values in binding.overrides.items():
            if isinstance(values,dict):
                for key in values:sources[section+'.'+key] = '本设备'
        result['_credentials_identity'] = hashlib.sha256(binding.encrypted_credentials.encode()).hexdigest()
    if kind == 'monitors':
        result.setdefault('_credentials_identity', hashlib.sha256(b'').hexdigest())
    result.pop('selected_items',None)
    result.pop('alert_items',None)
    if kind=='monitors' or (kind=='networks' and getattr(asset,'connection_type','')=='snmp') or result.get('item_enabled'):
        result['selected_items'] = supported_collection_items(kind,asset,result)
    return result


def attach_live_credentials(kind, target_id, effective):
    from net.models import DeviceCollectionBinding
    result = deepcopy(effective)
    if kind != 'monitors':
        return result
    row = DeviceCollectionBinding.objects.filter(kind=kind,target_id=target_id).first()
    identity = hashlib.sha256((row.encrypted_credentials if row else '').encode()).hexdigest()
    if '_credentials_identity' in result and result['_credentials_identity'] != identity:
        raise ValidationError('SNMP 凭据在任务入队后已变更，请重新创建巡检任务。')
    result['snmp'] = {**result.get('snmp',{}), **decrypt_credentials(row)}
    return result
