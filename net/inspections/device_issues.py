"""Evaluate only selected, typed device evidence; never infer bandwidth from counters."""
import math
from net.inspections.issues import PROJECT_RULES, METRIC_DEFAULTS
from net.devices.pc.severity import grade_issue


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip().removesuffix('%'))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def metric_values(item, evidence):
    if isinstance(evidence, list):
        return [value for child in evidence for value in metric_values(item, child)]
    if not isinstance(evidence, dict):
        return []
    if item == 'temperature':
        raw = evidence.get('values_celsius', [])
        return [value for raw_value in raw if (value := number(raw_value)) is not None and -273.15 <= value <= 1000] if isinstance(raw, list) else []
    value = number(evidence.get('usage_percent', evidence.get('used_percent', evidence.get('usage'))))
    if value is None and item == 'memory':
        total, used = number(evidence.get('total_bytes')), number(evidence.get('used_bytes'))
        if total is not None and total > 0 and used is not None:
            value = used * 100 / total
    return [value] if value is not None and 0 <= value <= 100 else []


def explicit_failure(value):
    if isinstance(value, list):
        return any(explicit_failure(child) for child in value)
    if not isinstance(value, dict):
        return False
    if value.get('online') is False:
        return True
    return any(str(value.get(key, '')).lower() in {'failed', 'error', 'fault', 'offline', 'critical', 'stopped'}
               for key in ('status', 'state', 'health'))


def evaluate_device_issues(project, selected, data, *, reachable, status, overrides=None, thresholds=None, message='', server_type=None):
    rules = PROJECT_RULES[project]
    limits = {**METRIC_DEFAULTS.get(project, {}), **(thresholds or {})}
    issues, normal = [], []

    def issue(item, detail, missing=False):
        label = rules[item][1]
        value = {'project': project, 'analysis_item': item, '问题类型': f'{label}数据不足' if missing else f'{label}异常',
                 '详细问题': detail}
        if missing:
            value['data_state'] = 'unknown'
        elif item == 'inspection_collection':
            value['severity'] = 'critical'
        issues.append(grade_issue(value, overrides=overrides or {}))

    if not reachable:
        issue('inspection_collection', message or '无法连接设备采集服务')
        return issues, normal
    normal.append('inspection_collection')
    items = list(dict.fromkeys(item for item in selected if item in rules))
    is_linux = project == 'servers' and (str(server_type or '').lower() == 'linux' or (str(data.get('system_info', {}).get('uname', '')).lower().startswith('linux ') if isinstance(data.get('system_info'), dict) else False))
    for item in items:
        evidence = data.get(item)
        if item == 'config_info' and isinstance(evidence, dict) and evidence.get('status') == 'unsupported':
            issue(item, '当前厂商或接口不支持完整配置备份。')
            continue
        if item == 'traffic':
            interfaces = evidence.get('interfaces', []) if isinstance(evidence, dict) else []
            valid = [row for row in interfaces if isinstance(row, dict) and row.get('data_state') == 'known']
            utilizations = [value for row in valid if (value := number(row.get('utilization_percent'))) is not None]
            if not interfaces or len(valid) != len(interfaces) or len(utilizations) != len(valid):
                issue(item, '部分接口缺少有效速率采样或端口带宽，无法完整判断带宽使用率。', True)
            if utilizations and max(utilizations) > limits[item]:
                issue(item, f'最高接口带宽使用率 {max(utilizations):g}% 超过阈值 {limits[item]:g}%')
            elif interfaces and len(utilizations) == len(interfaces):
                normal.append(item)
            continue
        if project == 'servers' and not is_linux and item == 'services' and isinstance(evidence, list):
            errors = data.get('collection_errors', {}) if isinstance(data, dict) else {}
            restricted = isinstance(errors, dict) and bool(errors.get('services'))
            stopped_automatic = [row for row in evidence if isinstance(row, dict)
                                 and str(row.get('Status', row.get('status', ''))).lower() in {'1', 'stopped'}
                                 and str(row.get('StartType', row.get('start_type', ''))).lower() in {'2', 'automatic', 'auto'}]
            if restricted or stopped_automatic:
                names = [str(row.get('DisplayName') or row.get('display_name') or row.get('Name') or row.get('name') or '?') for row in stopped_automatic]
                detail = '服务采集存在受限实例，当前结果仅代表已取得的部分证据。' if restricted else ''
                if names:
                    detail += ('；' if detail else '') + '检测到已停止的自动启动服务：' + '、'.join(names[:3]) + '；未取得触发器信息，需结合服务触发器配置确认是否需要处理。'
                issue(item, detail, missing=True)
                continue
        if item not in data or evidence is None or explicit_failure(evidence):
            issue(item, f'{rules[item][1]}未采集或设备返回异常状态', item not in data or evidence is None)
            continue
        if is_linux and item == 'services' and isinstance(evidence, list):
            if evidence:
                issue(item, f'检测到 {len(evidence)} 个失败服务：' + '；'.join(str(value) for value in evidence[:3]))
            else:
                normal.append(item)
            continue
        if is_linux and item == 'logs' and isinstance(evidence, list):
            if evidence:
                issue(item, f'取得 {len(evidence)} 行错误级别系统日志（含续行），请查看日志内容。')
            else:
                normal.append(item)
            continue
        if item in limits:
            values = metric_values(item, evidence)
            if not values:
                issue(item, '已取得回显，但没有可用于阈值判断的结构化数值。', True)
            elif max(values) > limits[item]:
                unit = '℃' if item == 'temperature' else '%'
                issue(item, f'{rules[item][1]}最高值 {max(values):g}{unit} 超过阈值 {limits[item]:g}{unit}')
            else:
                normal.append(item)
            continue
        if project=='networks':
            from net.inspections.selection import NETWORK_FUNCTION_ITEMS
            if item in NETWORK_FUNCTION_ITEMS:
                records=evidence.get('records') if isinstance(evidence,dict) else None
                if not isinstance(records,list) or evidence.get('status')=='partial':
                    issue(item,'没有完整的结构化记录，无法判定此项目。',True)
                    continue
                if item=='wireless_aps':
                    healthy={'nor','normal','run','r/m','r/b','registered','online','up','stdby','standby'}
                    offline={'fault','idle','i','j','ja','il','c','dc','quit','offline','down','disconnected','unregistered'}
                    bad=[row for row in records if str(row.get('state','')).lower() in offline]
                    unknown=[row for row in records if str(row.get('state','')).lower() not in healthy|offline]
                    if bad:issue(item,f'返回记录中有 {len(bad)} 台 AP 未在线；仅判断已返回 AP，不推断未登记设备。')
                    if unknown:issue(item,f'{len(unknown)} 条 AP 记录缺少可识别的状态。',True)
                    if bad or unknown:continue
                normal.append(item)
                continue
        if item == 'interface_status' and isinstance(evidence, dict):
            failed = [str(row.get('name') or row.get('index') or '?') for row in evidence.get('interfaces', [])
                      if isinstance(row, dict) and row.get('admin_status') == 'up' and row.get('oper_status') == 'down']
            if failed:
                issue(item, '已启用但链路断开的接口：' + '、'.join(failed))
                continue
        normal.append(item)
    if status != 'success' and not issues:
        normal.remove('inspection_collection')
        issue('inspection_collection', message or '采集未完整成功')
    return issues, normal
