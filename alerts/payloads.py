"""Read payloads for the alert record list and detail, shared with the REST twins."""
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from index.common.badges import severity_badge, status_badge
from index.common.detail_payload import (
    detail_payload, field_section, link_action, list_section, text_section,
)
from index.templatetags.extras import group_delivery_outcomes


def alert_event_queryset():
    """Base queryset: the page and the REST list must page over the same rows."""
    from net.models import AlertEvent

    return AlertEvent.objects.select_related(
        "task", "target_run", "policy",
    ).prefetch_related("deliveries__channel")


def alert_event_metrics(queryset):
    from django.db.models import Count, Q

    from net.models import AlertEvent

    return queryset.aggregate(
        total=Count("pk"),
        pending=Count("pk", filter=Q(
            status__in=(AlertEvent.Status.PENDING, AlertEvent.Status.SENDING))),
        delivered=Count("pk", filter=Q(status=AlertEvent.Status.DELIVERED)),
        partial=Count("pk", filter=Q(status=AlertEvent.Status.PARTIAL)),
        failed=Count("pk", filter=Q(status=AlertEvent.Status.FAILED)),
        recorded=Count("pk", filter=Q(status=AlertEvent.Status.RECORDED)),
    )


def _event_name(event):
    if event.event_type == "summary":
        return (event.summary_data or {}).get("task_name") or event.profile_type
    return getattr(event.policy, "name", None) or event.profile_type


def _event_target(event):
    if event.event_type == "summary":
        total = (event.summary_data or {}).get("total") or 0
        return f"全部 {total} 个目标"
    return str(event.target_id)


def _deliveries(event):
    return [
        {
            "status": group["status"],
            "label": group["label"],
            **status_badge(group["status"], group["label"]),
        }
        for group in group_delivery_outcomes(event.deliveries.all())
    ]


def _delivery_section(event):
    return list_section('渠道送达', [
        {
            'label': delivery.channel.name,
            'meta': '尝试 %s / %s' % (
                delivery.attempt_count, delivery.max_attempts,
            ) + (
                ' · ' + timezone.localtime(delivery.delivered_at).strftime(
                    '%Y-%m-%d %H:%M:%S')
                if delivery.delivered_at else ''
            ),
            'detail': delivery.response_summary or '',
            'badge': status_badge(delivery.status, delivery.get_status_display()),
        }
        for delivery in event.deliveries.select_related('channel').all()
    ], empty='该事件没有可用的已启用渠道。')


def alert_detail_payload(event):
    """Same blocks as alerts/detail.html, as data."""
    from net.models import AlertEvent

    actions = [
        link_action('返回告警记录', reverse('alert_list')),
        link_action('任务详情', reverse('task_detail', args=[event.task_id])),
    ]
    if event.event_type != AlertEvent.EventType.SUMMARY and event.target_run_id:
        query = urlencode({'filter_target_id': event.target_run.target_id})
        actions.append(link_action(
            '目标执行详情', f"{reverse('task_detail', args=[event.task_id])}?{query}",
            variant='primary',
        ))

    if event.event_type == AlertEvent.EventType.SUMMARY:
        data = event.summary_data or {}
        title = data.get('message_title') or '任务总结'
        description = ' · '.join(
            part for part in (data.get('task_name'), data.get('task_status')) if part
        )
        sections = [
            field_section('任务总结', [
                ('目标统计', '总数 {total} · 正常 {normal} · 异常 {abnormal}'.format(
                    total=data.get('total') or 0,
                    normal=data.get('normal') or 0,
                    abnormal=data.get('abnormal') or 0,
                )),
                ('执行统计', '失败 {failed} · 取消 {cancelled} · 跳过 {skipped}'.format(
                    failed=data.get('failed') or 0,
                    cancelled=data.get('cancelled') or 0,
                    skipped=data.get('skipped') or 0,
                )),
            ]),
            text_section(
                '已固定的通知内容',
                data.get('message_text') or event.summary
                or '此历史总结未保存通知正文，请查看任务详情。',
            ),
            _delivery_section(event),
        ]
    else:
        title = event.get_event_type_display()
        description = '%s · %s' % (
            timezone.localtime(event.occurred_at).strftime('%Y-%m-%d %H:%M:%S'),
            event.get_status_display(),
        )
        findings = [
            {
                'label': finding.get('title') or '',
                'detail': finding.get('detail') or '',
                'badge': severity_badge(finding.get('severity')),
            }
            for finding in (event.findings or [])
            if isinstance(finding, dict)
        ]
        sections = [
            field_section('事件信息', [
                ('项目', event.policy.name if event.policy_id else event.profile_type),
                ('目标', f'{event.target_type} / {event.target_id}'),
                ('严重级别', '', severity_badge(event.severity)),
                ('摘要', event.summary or '-'),
            ]),
            list_section('合并发现', findings, empty='本次事件没有合并的异常项。'),
        ]
        sections.append(_delivery_section(event))

    return detail_payload(
        title=title, eyebrow='告警中心', description=description,
        actions=actions, sections=sections,
    )


def alert_event_payloads(events):
    """Plain dicts matching the columns of alerts/event_row.html."""
    rows = []
    for event in events:
        rows.append({
            "id": str(event.pk),
            "event_type": status_badge(event.event_type, event.get_event_type_display()),
            "name": _event_name(event),
            "target": _event_target(event),
            "severity": severity_badge(event.severity),
            "status": status_badge(event.status, event.get_status_display()),
            "deliveries": _deliveries(event),
            "occurred_at_display": timezone.localtime(event.occurred_at).strftime(
                "%Y-%m-%d %H:%M:%S"),
            "detail_url": reverse("alert_detail", args=[event.pk]),
        })
    return rows
