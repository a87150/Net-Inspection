"""Detail payloads for the inspection record and PC analysis pages."""
import json

from django.urls import reverse
from django.utils import timezone

from index.common.badges import severity_badge
from index.common.detail_payload import (
    alert_section, detail_payload, field_section, group_section, link_action,
    list_section, table_section, text_section,
)


def _pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _findings_groups(findings):
    """Same grouping the template did with regroup|dictsort:category."""
    if not findings:
        return []
    buckets = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        buckets.setdefault(finding.get('category') or '未分类', []).append(finding)
    return [
        {
            'title': title,
            'count': len(items),
            'items': [
                {
                    'label': item.get('问题类型') or '',
                    'detail': item.get('详细问题') or '',
                    'badge': severity_badge(item.get('severity')),
                }
                for item in items
            ],
        }
        for title, items in sorted(buckets.items())
    ]


def _traffic_section(details):
    traffic = (details or {}).get('traffic')
    if not traffic:
        return None
    rows = [
        [
            str(row.get('name') or ''),
            '—' if row.get('rx_mbps') is None else str(row['rx_mbps']),
            '—' if row.get('tx_mbps') is None else str(row['tx_mbps']),
            '未知' if row.get('speed_mbps') is None else str(row['speed_mbps']),
            '未知' if row.get('utilization_percent') is None else str(row['utilization_percent']),
            '有效' if row.get('data_state') == 'known' else (row.get('reason') or '数据不足'),
        ]
        for row in traffic.get('interfaces') or []
    ]
    return table_section(
        '实时接口流量',
        ['接口', '接收 Mbps', '发送 Mbps', '端口带宽 Mbps', '带宽使用率 %', '采样状态'],
        rows,
        empty=traffic.get('message') or '暂无有效的速率采样，请检查 SNMP 配置。',
        note='本次巡检采样窗口 %s 秒，展示采样期间的收发速率；不是累计流量，也不是持续刷新的监控曲线。'
             % traffic.get('sample_seconds', '未知'),
    )


def _inspection_alert(inspection):
    if inspection.is_reachable and inspection.status == 'success':
        return alert_section(
            'success', '深度巡检成功 · 耗时 %s ms' % inspection.duration_ms)
    if inspection.is_reachable:
        return alert_section(
            'warning',
            '设备在线，但数据采集未完整成功：%s' % (inspection.summary or '请检查协议配置'),
        )
    return alert_section(
        'danger',
        '设备不可达或管理协议连接失败：%s' % (inspection.summary or '请检查网络、电源或地址配置'),
    )


def inspection_record_detail_payload(*, inspection, record_kind, item_name,
                                     asset_name, detail_fields, inspection_errors,
                                     back_url):
    created = timezone.localtime(inspection.created_at).strftime('%Y-%m-%d %H:%M:%S')
    details = inspection.details or {}
    sections = []
    traffic = _traffic_section(details)
    if traffic:
        sections.append(traffic)
    sections.append(_inspection_alert(inspection))

    groups = _findings_groups(details.get('issue_findings'))
    if groups:
        sections.append(group_section('分类问题', inspection.summary, groups))
    elif inspection_errors:
        sections.append(list_section('异常信息', [
            {
                'label': '需要处理',
                'detail': _pretty(error.error_message),
            }
            for error in inspection_errors
        ]))

    if detail_fields:
        sections.append(field_section('巡检明细', [
            (name, value if isinstance(value, str) else _pretty(value))
            for name, value in detail_fields
        ]))

    return detail_payload(
        title='%s 巡检详情' % asset_name,
        eyebrow='巡检记录',
        description='%s · %s' % (item_name, created),
        actions=[link_action('返回巡检记录', back_url)],
        sections=sections,
    )


def analysis_detail_payload(*, analysis, detail_fields, back_url):
    details = analysis.details or {}
    rules = details.get('rules') or {}
    log_file_id = getattr(analysis, 'log_file_id', None)
    actions = []
    if log_file_id:
        actions.append(link_action(
            '查看日志详情', reverse('computer_log_detail', args=[log_file_id]),
            variant='primary',
        ))
    actions.append(link_action('返回日志分析记录', back_url))

    sections = []
    if 'software' in (analysis.analysis_items or []):
        mode = rules.get('software_policy_mode')
        label = {'blacklist': '黑名单模式', 'whitelist': '白名单模式'}.get(
            mode, '旧版组合规则（黑名单优先，再检查白名单）',
        )
        sections.append(text_section('软件分析模式', '本次软件分析模式：%s。' % label))
    if not log_file_id:
        sections.append(alert_section('secondary', '原始日志已按保留策略替换，分析记录仍可查看。'))

    enrichment = details.get('enrichment')
    if enrichment:
        sites = enrichment.get('sites') or []
        site = enrichment.get('site') or ('多场地：%s' % '、'.join(sites) if sites else '未匹配')
        sections.append(field_section('分析时人员与目录快照', [
            ('登录标识 / 工号', '%s / %s' % (
                enrichment.get('login_identifier') or '未知',
                enrichment.get('employee_number') or '未匹配',
            )),
            ('人员 / 部门', '%s / %s' % (
                enrichment.get('personnel_name') or '未匹配',
                enrichment.get('department') or '未知',
            )),
            ('用户 OU', enrichment.get('user_ou') or '未匹配'),
            ('计算机 OU', enrichment.get('computer_ou') or '未匹配'),
            ('场地', site),
        ]))

    groups = _findings_groups(analysis.graded_findings)
    if groups:
        sections.append(group_section('分类问题', analysis.summary, groups))
    else:
        sections.append(alert_section('success', '本次分析未发现问题。'))

    sections.append(field_section('分析概况', [
        ('登录用户', analysis.computer.user_name or '-'),
        ('结果', analysis.get_status_display()),
        ('入库时间', timezone.localtime(analysis.created_at).strftime('%Y-%m-%d %H:%M:%S')),
    ]))

    rows = [(title, _pretty(value)) for title, value in detail_fields if value]
    if rows:
        sections.append(field_section('分析明细', rows))

    return detail_payload(
        title='%s 分析详情' % analysis.computer.computer_name,
        eyebrow='日志分析记录',
        description='日志时间：%s' % (
            timezone.localtime(analysis.source_collected_at).strftime('%Y-%m-%d %H:%M:%S')
            if analysis.source_collected_at else '未知'
        ),
        actions=actions,
        sections=sections,
    )
