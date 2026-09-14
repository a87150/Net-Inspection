"""Selected, repeatable analysis of imported PowerShell computer logs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import configparser
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from net.models import Computer, ComputerAnalysis, ComputerLogFile, Error_Computer, RecordStatus
from net.infrastructure.sanitization import sanitize
from net.devices.pc.checks import (
    check_activation,
    check_bitlocker,
    check_defender,
    check_resource,
    check_software,
    check_system_version,
    check_update_history,
    check_uptime,
    check_remote_item,
    load_config_as_dict,
    parse_local_datetime,
)
from net.devices.pc.enrichment import build_pc_enrichment
from net.devices.pc.severity import grade_issue, severity_counts


ANALYSIS_ITEMS = frozenset({
    'activation',
    'software',
    'processes',
    'bitlocker',
    'defender',
    'patches',
    'resource',
    'disk',
    'event_findings',
    'system',
    'uptime',
    'browser_extensions', 'identity_match', 'cpu_health', 'domain_trust', 'group_policy',
})

WINDOWS_ONLY_ITEMS = frozenset({'activation', 'bitlocker', 'defender', 'patches',
                                'domain', 'domain_trust', 'group_policy'})
REMOTE_ITEMS = frozenset({'browser_extensions', 'identity_match', 'cpu_health',
                          'domain_trust', 'group_policy'})


def analysis_items_for_platform(items, platform):
    """Use this when presenting default selectable items for a platform."""
    return [item for item in items if platform != 'macos' or item not in WINDOWS_ONLY_ITEMS]


# A present empty collection means collected/no entries. Missing keys, null,
# wrong shapes and unknown values must never be silently converted to [].
# Older producers may omit sections that newer terminal producers supply.
ITEM_FIELDS = {
    'activation': ('Windows激活信息',),
    'software': ('已安装软件列表',),
    'processes': ('当前运行进程清单',),
    'bitlocker': ('BitLocker状态',),
    'defender': ('WindowsDefender状态',),
    'patches': ('系统更新历史',),
    'resource': ('计算机硬件资源情况',),
    'event_findings': ('事件发现',),
    'system': ('系统信息概览',),
    'uptime': ('系统信息概览',),
}
MISSING_LABELS = {
    'activation': '系统激活问题', 'bitlocker': 'BitLocker数据缺失',
    'defender': 'WindowsDefender数据缺失', 'patches': '系统更新数据缺失',
}
EVENT_LEVELS = {'information', 'informational', 'info', 'warning', 'error',
                'critical', 'verbose', '信息', '警告', '错误', '严重'}


def _known_text(value):
    return (isinstance(value, str) and bool(value.strip()) and
            not any(word in value.lower() for word in
                    ('未知', '获取失败', '未采集', 'unknown', 'unavailable')))


def _valid_rows(value, validator):
    return isinstance(value, list) and all(
        isinstance(row, dict) and validator(row) for row in value
    )


def _event_level(row):
    return str(row.get('级别') or row.get('severity') or row.get('Level') or '').lower()


def _schema_state(item, payload):
    keys = ITEM_FIELDS[item]
    if item == 'event_findings' and keys[0] not in payload and '事件日志' in payload:
        keys = ('事件日志',)
    if any(key not in payload for key in keys):
        return 'missing'
    value = payload[keys[0]]
    if item == 'processes':
        # Original TerminalLogs.ps1 emits process names, newer producers may
        # include objects. Validate both without rewriting the stored evidence.
        valid = isinstance(value, list) and all(
            _known_text(row.get('进程名') if isinstance(row, dict) else row)
            for row in value
        )
    elif item in {'software', 'patches', 'event_findings'}:
        validators = {
            'software': lambda row: _known_text(row.get('软件名')),
            'patches': lambda row: _known_text(row.get('补丁名称')) and bool(parse_local_datetime(row.get('日期'))),
            'event_findings': lambda row: _event_level(row) in EVENT_LEVELS,
        }
        valid = _valid_rows(value, validators[item])
    elif not isinstance(value, dict):
        valid = False
    elif item == 'activation':
        valid = _known_text(value.get('许可证状态'))
    elif item == 'bitlocker':
        volumes = value.get('磁盘卷信息')
        if volumes == []:
            return 'empty'  # No volumes is not evidence of encryption.
        valid = _valid_rows(volumes, lambda row: _known_text(row.get('卷')) and _known_text(row.get('转换状态')))
    elif item == 'defender':
        valid = _known_text(value.get('当前病毒库版本')) and bool(parse_local_datetime(value.get('上次更新时间')))
    elif item == 'resource':
        valid = all(
            isinstance(value.get(key), str) and
            re.fullmatch(r'\s*(?:\d+(?:\.\d+)?)\s*%\s*', value[key]) is not None and
            0 <= float(value[key].strip()[:-1]) <= 100
            for key in ('当前CPU占用率', '当前内存使用率')
        )
    elif item == 'system':
        valid = _known_text(value.get('系统主要版本名'))
    elif item == 'uptime':
        valid = bool(parse_local_datetime(value.get('开机时间')))
    else:
        valid = False
    return 'known' if valid else 'unknown'


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def _items(value):
    if not isinstance(value, list) or not value:
        raise ValidationError({'analysis_items': '至少需要选择一个分析项目。'})
    if any(not isinstance(item, str) or (item != 'domain' and item not in ANALYSIS_ITEMS) for item in value):
        raise ValidationError({'analysis_items': '包含不支持的分析项目。'})
    if len(value) != len(set(value)):
        raise ValidationError({'analysis_items': '分析项目不能重复。'})
    # Old immutable task snapshots may still contain the combined option.
    # Expand it once without duplicating either dedicated check.
    return list(dict.fromkeys(part for item in value
                             for part in (('domain_trust', 'group_policy') if item == 'domain' else (item,))))


def _issue(issues, issue_type, detail):
    issues.append({'问题类型': issue_type, '详细问题': detail})


def _activation(payload, issues, rules):
    activation = _as_dict(payload.get('Windows激活信息'))
    check_activation(payload, issues, rules['kms_servers'])
    return {
        'Windows激活信息': activation,
        'KMS服务器连通情况': payload.get('KMS服务器连通情况', ''),
    }


def _event_findings(payload, issues):
    findings = payload['事件发现'] if '事件发现' in payload else payload.get('事件日志')
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        severity = str(
            finding.get('级别') or finding.get('severity') or finding.get('Level') or '',
        ).lower()
        if severity in {'warning', '警告', 'error', 'critical', '错误', '严重'}:
            detail = str(finding.get('消息') or finding.get('message') or finding)
            _issue(issues, '事件日志问题', detail)
            issues[-1]['severity'] = 'critical' if severity in {'critical', '严重'} else 'warning'
    return findings


def _software_details(payload, issues, rules):
    policy_path = rules['software_policy_path']
    frozen = rules.get('software_policy_snapshot')
    if 'software_policy_snapshot' in rules or policy_path:
        try:
            if 'software_policy_snapshot' in rules:
                if not isinstance(frozen, dict):
                    raise ValueError('软件策略快照无效。')
                if 'error' in frozen:
                    raise ValueError(sanitize(str(frozen['error'])))
                policy = frozen.get('content')
                if not isinstance(policy, dict):
                    raise ValueError('软件策略快照无效。')
            else:
                policy = load_config_as_dict(policy_path)
            if not policy_path and not policy:
                return _as_list(payload.get('已安装软件列表'))
            identity = str(
                _as_dict(payload.get('系统信息概览')).get('当前登录用户工号') or '',
            ).rsplit('\\', 1)[-1]
            check_software(
                payload,
                identity,
                issues,
                policy,
            )
        except (OSError, configparser.Error, ValueError) as exc:
            _issue(issues, '软件策略问题', f'软件策略文件无法读取：{exc}')
    return _as_list(payload.get('已安装软件列表'))


def _details_for_item(item, payload, issues, rules, collected_at, platform='windows'):
    if platform == 'macos' and item in WINDOWS_ONLY_ITEMS:
        return {'data_state': 'not_applicable', 'platform': platform}
    if item == 'disk':
        from net.devices.pc.disk import check_disk
        return check_disk(payload, issues, rules['disk_max_percent'])
    if item in REMOTE_ITEMS:
        result = check_remote_item(item, payload, issues, rules)
        diagnostic_key = {'cpu_health': 'CPU温度', 'browser_extensions': '浏览器插件情况'}.get(item)
        diagnostics = payload.get('采集诊断')
        diagnostic = diagnostics.get(diagnostic_key) if isinstance(diagnostics, dict) else None
        if diagnostic:
            result['collection_diagnostic'] = diagnostic
            result['data_state'] = 'partial'
            issues.append({'问题类型': '采集数据不足', '详细问题': str(diagnostic), 'data_state': 'partial'})
        return result
    state = _schema_state(item, payload)
    diagnostics = payload.get('采集诊断')
    item_diagnostics = diagnostics.get(ITEM_FIELDS[item][0]) if isinstance(diagnostics, dict) else None
    if item_diagnostics:
        state = 'partial' if state == 'known' else state
    if state != 'known':
        issues.append({
            '问题类型': MISSING_LABELS.get(item, f'{item}数据缺失或未知'),
            '详细问题': f'所选项目 {item} 数据状态为 {state}，不能判定正常。' + (f' 采集诊断：{item_diagnostics}' if item_diagnostics else ''),
            'analysis_item': item, 'data_state': state,
        })
        # Partial software collection still proves the presence of the rows
        # obtained successfully. Keep its diagnostic without hiding violations.
        if item != 'software' or state != 'partial':
            return {key: payload.get(key) for key in ITEM_FIELDS[item]}
    if item == 'activation':
        return _activation(payload, issues, rules)
    if item == 'software':
        return _software_details(payload, issues, rules)
    if item == 'processes':
        return _as_list(payload.get('当前运行进程清单'))
    if item == 'bitlocker':
        check_bitlocker(payload, issues)
        return _as_dict(payload.get('BitLocker状态'))
    if item == 'defender':
        defender = _as_dict(payload.get('WindowsDefender状态'))
        if rules['configured']:
            check_defender(
                payload, issues, collected_at,
                rules['defender_update_max_days'], rules['defender_scan_max_days'],
            )
        return defender
    if item == 'patches':
        patches = _as_list(payload.get('系统更新历史'))
        if rules['configured']:
            check_update_history(payload, issues, collected_at, rules['patch_max_days'])
        return patches
    if item == 'resource':
        if rules['configured']:
            check_resource(
                payload, issues, rules['cpu_max_percent'], rules['memory_max_percent'],
            )
        return _as_dict(payload.get('计算机硬件资源情况'))
    if item == 'event_findings':
        return _event_findings(payload, issues)
    if item == 'system':
        if platform != 'macos':
            check_system_version(payload, issues, rules['minimum_windows_release'])
        return _as_dict(payload.get('系统信息概览'))
    if item == 'uptime':
        check_uptime(payload, issues, collected_at, rules['uptime_max_hours'])
        return _as_dict(payload.get('系统信息概览'))
    raise AssertionError(f'未注册的分析项目：{item}')


def _computer_for_payload(payload):
    system_info = _as_dict(payload.get('系统信息概览'))
    computer_name = str(system_info.get('计算机名') or '').strip()
    if not computer_name:
        raise ValidationError({'log_file': '日志缺少可关联的计算机名。'})
    try:
        return Computer.objects.get(computer_name=computer_name)
    except Computer.DoesNotExist as exc:
        raise ValidationError({'log_file': '日志尚未生成计算机静态资产。'}) from exc


def _analysis_rules(value):
    supplied = value if isinstance(value, Mapping) else {}
    return {
        **({'software_policy_snapshot': supplied['software_policy_snapshot']}
           if 'software_policy_snapshot' in supplied else {}),
        'configured': value is not None,
        'software_policy_path': str(supplied.get('software_policy_path') or ''),
        'minimum_windows_release': str(supplied.get('minimum_windows_release') or ''),
        'defender_update_max_days': supplied.get('defender_update_max_days', 7),
        'defender_scan_max_days': supplied.get('defender_scan_max_days', 7),
        'patch_max_days': supplied.get('patch_max_days', 30),
        'uptime_max_hours': supplied.get('uptime_max_hours', 168),
        'disk_max_percent': supplied.get('disk_max_percent', 90),
        'cpu_max_percent': supplied.get('cpu_max_percent', 90),
        'cpu_temperature_max_celsius': supplied.get('cpu_temperature_max_celsius', 85),
        'site_ip_prefixes': supplied.get('site_ip_prefixes'),
        'personnel_roster': supplied.get('personnel_roster'),
        'matching_mode': supplied.get('matching_mode'),
        'issue_severity_overrides': supplied.get('issue_severity_overrides') or {},
        'memory_max_percent': supplied.get('memory_max_percent', 90),
        'kms_servers': list(supplied.get('kms_servers') or []),
    }


@dataclass(frozen=True)
class PreparedAnalysis:
    fields: dict
    errors: tuple


def prepare_log(
    log_file, analysis_items, *, rules=None, task_target=None, started_at=None,
) -> PreparedAnalysis:
    """Read inputs and compute analysis without writes or transaction locks."""
    if not isinstance(log_file, ComputerLogFile):
        raise ValidationError({'log_file': '必须提供已导入的日志文件。'})
    selected_items = _items(analysis_items)
    configured_rules = _analysis_rules(rules)
    payload = log_file.payload
    if log_file.import_status != 'imported' or not isinstance(payload, dict):
        raise ValidationError({'log_file': '日志导入失败，不能执行分析。'})
    computer = _computer_for_payload(payload)
    now = timezone.now()
    issues = []
    platform = str(log_file.platform or payload.get('platform') or '').casefold()
    if not platform:
        platform = 'macos' if 'macos' in str(_as_dict(payload.get('系统信息概览')).get('系统主要版本名', '')).casefold() else 'windows'
    details = {
        'collection_diagnostics': payload.get('采集诊断') if isinstance(payload.get('采集诊断'), dict) else {},
        'platform': platform,
        'rules': {key: value for key, value in configured_rules.items()
                  if key not in {'personnel_roster', 'software_policy_snapshot'}},
        'enrichment': build_pc_enrichment(
            computer, payload, site_ip_prefixes=configured_rules['site_ip_prefixes'],
            personnel_roster=configured_rules['personnel_roster'],
        ),
    }
    if (configured_rules['matching_mode'] == 'logs'
            and configured_rules['personnel_roster']
            and details['enrichment']['personnel_match'] != 'matched'):
        issues.append(grade_issue({
            '问题类型': '日志未匹配人员',
            '详细问题': '本日志无法唯一关联到任务人员名册中的人员，请核对人员资料和日志身份信息。',
            'analysis_item': 'identity_match', 'severity': 'warning',
        }, overrides=configured_rules['issue_severity_overrides']))
    frozen_policy = configured_rules.get('software_policy_snapshot')
    if isinstance(frozen_policy, dict):
        details['rules']['software_policy_snapshot'] = {
            key: frozen_policy[key] for key in ('sha256', 'version', 'error')
            if key in frozen_policy
        }
    for item in selected_items:
        item_issues = []
        details[item] = _details_for_item(
            item, payload, item_issues, configured_rules, log_file.modified_at, platform,
        )
        issues.extend(grade_issue({**issue, 'analysis_item': item},
                      overrides=configured_rules['issue_severity_overrides']) for issue in item_issues)
    status = RecordStatus.SUCCESS
    counts = severity_counts(issues)
    details['severity_counts'] = counts
    details['health_status'] = 'abnormal' if counts['warning'] + counts['critical'] else 'normal'
    summary = (f"分析完成：严重 {counts['critical']}，警告 {counts['warning']}，提示 {counts['info']}"
               if issues else '分析完成，未发现问题')
    return PreparedAnalysis(
        fields=dict(
            computer=computer,
            log_file=log_file,
            task_target=task_target,
            status=status,
            started_at=started_at or now,
            summary=sanitize(summary)[:4096],
            details=sanitize(details),
            analysis_items=selected_items,
            exceptions=sanitize(issues),
            finished_at=timezone.now(),
        ),
        errors=tuple(
            {'error_type': issue['问题类型'], 'error_message': issue['详细问题']}
            for issue in issues if issue['severity'] != 'info'
        ),
    )


def persist_analysis(prepared):
    """Commit a prepared result and its findings in one short transaction."""
    with transaction.atomic():
        analysis = ComputerAnalysis.objects.create(**prepared.fields)
        Error_Computer.objects.bulk_create([
            Error_Computer(
                inspection=analysis,
                **error,
            )
            for error in prepared.errors
        ])
    return analysis


def analyze_log(log_file, analysis_items, *, rules=None, task_target=None, started_at=None):
    """Create an independent immutable analysis of an imported local log."""
    return persist_analysis(prepare_log(
        log_file, analysis_items, rules=rules, task_target=task_target,
        started_at=started_at,
    ))
