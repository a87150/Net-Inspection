"""Selected, repeatable analysis of imported PowerShell computer logs."""

from __future__ import annotations

from collections.abc import Mapping
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from net.models import Computer, ComputerAnalysis, ComputerLogFile, Error_Computer, RecordStatus
from net.services.sanitization import sanitize
from net.tool import check_bitlocker, parse_local_datetime


ANALYSIS_ITEMS = frozenset({
    'activation',
    'software',
    'processes',
    'bitlocker',
    'defender',
    'patches',
    'domain',
    'resource',
    'event_findings',
})

# A present empty collection means collected/no entries. Missing keys, null,
# wrong shapes and unknown values must never be silently converted to [].
# The shipped legacy PS uploader lacks processes/domain/events: selecting them
# explicitly reports missing until a producer supplies this documented schema.
ITEM_FIELDS = {
    'activation': ('Windows激活信息',),
    'software': ('已安装软件列表',),
    'processes': ('当前运行进程清单',),
    'bitlocker': ('BitLocker状态',),
    'defender': ('WindowsDefender状态',),
    'patches': ('系统更新历史',),
    'domain': ('已应用策略', '当前与域服务器通讯情况'),
    'resource': ('计算机硬件资源情况',),
    'event_findings': ('事件发现',),
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
    if item in {'software', 'processes', 'patches', 'event_findings'}:
        validators = {
            'software': lambda row: _known_text(row.get('软件名')),
            'processes': lambda row: _known_text(row.get('进程名')),
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
    elif item == 'domain':
        valid = payload[keys[1]] in ('正常通讯', '无法访问', '未加入域')
    elif item == 'resource':
        valid = all(
            isinstance(value.get(key), str) and
            re.fullmatch(r'(?:\d+(?:\.\d+)?)%', value[key]) is not None and
            0 <= float(value[key][:-1]) <= 100
            for key in ('当前CPU占用率', '当前内存使用率')
        )
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
    if any(not isinstance(item, str) or item not in ANALYSIS_ITEMS for item in value):
        raise ValidationError({'analysis_items': '包含不支持的分析项目。'})
    if len(value) != len(set(value)):
        raise ValidationError({'analysis_items': '分析项目不能重复。'})
    return list(value)


def _issue(issues, issue_type, detail):
    issues.append({'问题类型': issue_type, '详细问题': detail})


def _activation(payload, issues):
    activation = _as_dict(payload.get('Windows激活信息'))
    if not activation:
        _issue(issues, '系统激活问题', '未采集到 Windows 激活信息')
    elif activation.get('许可证状态') != '已授权':
        _issue(issues, '系统激活问题', f"许可证状态为：{activation.get('许可证状态') or '未知'}")
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
        if severity in {'error', 'critical', '错误', '严重'}:
            detail = str(finding.get('消息') or finding.get('message') or finding)
            _issue(issues, '事件日志问题', detail)
    return findings


def _details_for_item(item, payload, issues):
    state = _schema_state(item, payload)
    if state != 'known':
        issues.append({
            '问题类型': MISSING_LABELS.get(item, f'{item}数据缺失或未知'),
            '详细问题': f'所选项目 {item} 数据状态为 {state}，不能判定正常。',
            'analysis_item': item, 'data_state': state,
        })
        # Retain null/empty/malformed shapes in selected details as evidence.
        return {key: payload.get(key) for key in ITEM_FIELDS[item]}
    if item == 'activation':
        return _activation(payload, issues)
    if item == 'software':
        return _as_list(payload.get('已安装软件列表'))
    if item == 'processes':
        return _as_list(payload.get('当前运行进程清单'))
    if item == 'bitlocker':
        check_bitlocker(payload, issues)
        return _as_dict(payload.get('BitLocker状态'))
    if item == 'defender':
        defender = _as_dict(payload.get('WindowsDefender状态'))
        if not defender:
            _issue(issues, 'WindowsDefender数据缺失', '未采集到 Defender 状态')
        return defender
    if item == 'patches':
        patches = _as_list(payload.get('系统更新历史'))
        return patches
    if item == 'domain':
        if payload['当前与域服务器通讯情况'] == '无法访问':
            _issue(issues, '域通讯问题', '无法访问域服务器')
        return {
            '已应用策略': _as_dict(payload.get('已应用策略')),
            '当前与域服务器通讯情况': payload.get('当前与域服务器通讯情况', ''),
        }
    if item == 'resource':
        return _as_dict(payload.get('计算机硬件资源情况'))
    if item == 'event_findings':
        return _event_findings(payload, issues)
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


def analyze_log(log_file, analysis_items, *, task_target=None, started_at=None) -> ComputerAnalysis:
    """Create a new immutable analysis from an already imported local log.

    The source file is never read or moved here. Calling this method repeatedly
    preserves the evidence and produces independent historical analyses.
    """
    if not isinstance(log_file, ComputerLogFile):
        raise ValidationError({'log_file': '必须提供已导入的日志文件。'})
    selected_items = _items(analysis_items)
    payload = log_file.payload
    if log_file.import_status != 'imported' or not isinstance(payload, dict):
        raise ValidationError({'log_file': '日志导入失败，不能执行分析。'})
    computer = _computer_for_payload(payload)
    now = timezone.now()
    issues = []
    details = {}
    for item in selected_items:
        item_issues = []
        details[item] = _details_for_item(item, payload, item_issues)
        issues.extend({**issue, 'analysis_item': item} for issue in item_issues)
    status = RecordStatus.FAILED if issues else RecordStatus.SUCCESS
    summary = f'发现 {len(issues)} 项异常' if issues else '分析正常'
    with transaction.atomic():
        analysis = ComputerAnalysis.objects.create(
            computer=computer,
            log_file=log_file,
            task_target=task_target,
            status=status,
            started_at=started_at or now,
            finished_at=now,
            summary=sanitize(summary)[:4096],
            details=sanitize(details),
            analysis_items=selected_items,
            exceptions=sanitize(issues),
        )
        Error_Computer.objects.bulk_create([
            Error_Computer(
                inspection=analysis,
                error_type=issue['问题类型'],
                error_message=issue['详细问题'],
            )
            for issue in issues
        ])
    return analysis
