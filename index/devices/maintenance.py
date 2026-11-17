"""Planned maintenance windows for network devices, servers and weak-current assets.

The REST layer (index/api) and the server-rendered page both read rows through
row_payloads(), so a column added here reaches Vue and the template together.
"""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from index.common.access import admin_required
from net.models import MaintenanceWindow, Network_Device, Server, WeakCurrentDevice

# One asset kind per list page; the values mirror AlertEvent.target_type.
KIND_TARGET_TYPES = {
    "networks": "network_device",
    "servers": "server",
    "weakcurrent": "monitor",
}
TARGET_MODELS = {
    "network_device": Network_Device,
    "server": Server,
    "monitor": WeakCurrentDevice,
}
TARGET_LABELS = dict(MaintenanceWindow.TargetType.choices)

PHASES = {"active": "进行中", "pending": "未开始", "ended": "已结束"}
PHASE_BADGES = {"active": "warning", "pending": "info", "ended": "neutral"}
STATE_CHOICES = (("", "全部"), ("active", "进行中"), ("pending", "未开始"), ("ended", "已结束"))
PAGE_SIZE = 50


class MaintenanceWindowForm(forms.Form):
    starts_at = forms.DateTimeField(input_formats=["%Y-%m-%dT%H:%M"])
    ends_at = forms.DateTimeField(input_formats=["%Y-%m-%dT%H:%M"])
    note = forms.CharField(required=False, max_length=200)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("starts_at")
        end = cleaned.get("ends_at")
        if start and end and end <= start:
            raise forms.ValidationError("结束时间必须晚于开始时间。")
        return cleaned


def form_defaults(now=None):
    start = timezone.localtime(now or timezone.now()).replace(second=0, microsecond=0)
    return {
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
    }


def phase_of(window, at=None):
    """Half-open [starts_at, ends_at); a disabled window still reports its phase."""
    at = at or timezone.now()
    if window.starts_at <= at < window.ends_at:
        return "active"
    return "pending" if at < window.starts_at else "ended"


def attach_phase(windows, now=None):
    now = now or timezone.now()
    for window in windows:
        window.phase = phase_of(window, now)
        window.phase_label = PHASES[window.phase]
        window.phase_badge = PHASE_BADGES[window.phase]
    return windows


def window_metrics(now=None):
    """Counters for the page header cards; one aggregate query."""
    now = now or timezone.now()
    return MaintenanceWindow.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(
            is_enabled=True, starts_at__lte=now, ends_at__gt=now)),
        pending=Count("id", filter=Q(is_enabled=True, starts_at__gt=now)),
        ended=Count("id", filter=Q(ends_at__lte=now)),
    )


def asset_names(target_type, target_ids):
    model = TARGET_MODELS[target_type]
    label_field = "name" if target_type == "server" else "device_name"
    return {
        str(pk): name
        for pk, name in model.objects.filter(pk__in=target_ids).values_list("pk", label_field)
    }


def window_queryset(state="", now=None):
    """Shared filter for the page and the REST list."""
    now = now or timezone.now()
    windows = MaintenanceWindow.objects.order_by("-starts_at", "-created_at")
    if state == "active":
        return windows.filter(is_enabled=True, starts_at__lte=now, ends_at__gt=now)
    if state == "pending":
        return windows.filter(is_enabled=True, starts_at__gt=now)
    if state == "ended":
        return windows.filter(ends_at__lte=now)
    return windows


def row_payloads(windows, now=None):
    """Plain dicts for Vue; the same shape the REST list returns."""
    now = now or timezone.now()
    rows = list(windows)
    attach_phase(rows, now)
    names = {}
    for target_type in TARGET_MODELS:
        ids = [row.target_id for row in rows if row.target_type == target_type]
        if ids:
            names.update(asset_names(target_type, ids))
    return [
        {
            "id": str(row.pk),
            "target_type": row.target_type,
            "target_type_label": TARGET_LABELS.get(row.target_type, row.target_type),
            "target_id": str(row.target_id),
            "asset_name": names.get(str(row.target_id), str(row.target_id)),
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(),
            "starts_at_display": timezone.localtime(row.starts_at).strftime("%Y-%m-%d %H:%M"),
            "ends_at_display": timezone.localtime(row.ends_at).strftime("%Y-%m-%d %H:%M"),
            "note": row.note,
            "is_enabled": row.is_enabled,
            "phase": row.phase,
            "phase_label": row.phase_label,
            "phase_badge": row.phase_badge,
        }
        for row in rows
    ]


def create_windows(target_type, target_ids, starts_at, ends_at, note=""):
    """One window per asset. Shared by the form view and the REST endpoint."""
    return MaintenanceWindow.objects.bulk_create([
        MaintenanceWindow(
            target_type=target_type, target_id=value,
            starts_at=starts_at, ends_at=ends_at, note=note,
        )
        for value in target_ids
    ])


@admin_required
@require_POST
def maintenance_window_create(request, kind):
    """Create one window per selected asset; the list page posts here."""
    target_type = KIND_TARGET_TYPES.get(kind)
    if not target_type:
        messages.error(request, "这类资产不支持维护窗口。")
        return redirect("asset_list", kind)

    form = MaintenanceWindowForm(request.POST)
    requested = [value for value in request.POST.getlist("target_ids") if value]
    if not requested:
        messages.error(request, "请先在列表里勾选要加入维护窗口的资产。")
        return redirect("asset_list", kind)
    if not form.is_valid():
        messages.error(request, "维护窗口未保存：%s" % " ".join(
            "%s %s" % (field, " ".join(errors)) for field, errors in form.errors.items()
        ))
        return redirect("asset_list", kind)

    known = asset_names(target_type, requested)
    missing = [value for value in requested if value not in known]
    if missing:
        messages.error(request, "有 %d 个选中项已不存在，请刷新列表后重新选择。" % len(missing))
        return redirect("asset_list", kind)

    data = form.cleaned_data
    create_windows(target_type, list(known), data["starts_at"], data["ends_at"], data["note"])
    messages.success(
        request,
        "已为 %d 个资产登记维护窗口：窗口内告警照常入库，但不外发。" % len(known),
    )
    return redirect("asset_list", kind)


@admin_required
@require_POST
def maintenance_window_delete(request, window_id):
    deleted, _ = MaintenanceWindow.objects.filter(pk=window_id).delete()
    messages.success(request, "维护窗口已删除。" if deleted else "维护窗口不存在。")
    return redirect(request.POST.get("next") or "maintenance_list")


@admin_required
@require_http_methods(["GET"])
def maintenance_list(request):
    """Server-rendered first paint; the Vue page takes over from the same payload."""
    state = (request.GET.get("state") or "").strip()
    now = timezone.now()
    page = Paginator(window_queryset(state, now), PAGE_SIZE).get_page(request.GET.get("page"))
    bootstrap = {
        "state": state,
        "states": [{"value": value, "label": label} for value, label in STATE_CHOICES],
        "endpoint": "/api/v1/maintenance-windows/",
        "count": page.paginator.count,
        "page": page.number,
        "pages": page.paginator.num_pages,
        "page_size": PAGE_SIZE,
        "rows": row_payloads(page.object_list, now),
        "metrics": window_metrics(now),
        "can_administer": True,
    }
    return render(request, "maintenance/list.html", {"bootstrap": bootstrap})
