"""Read payloads for the alert record list, shared by the page and the REST list."""
from django.urls import reverse
from django.utils import timezone

from index.common.badges import severity_badge, status_badge
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
