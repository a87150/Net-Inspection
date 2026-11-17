"""Planned maintenance windows for network devices, servers and weak-current assets."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
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


def attach_phase(windows, now=None):
    """Label each row for the page; no extra query."""
    now = now or timezone.now()
    for window in windows:
        if window.starts_at <= now < window.ends_at:
            window.phase = "active"
        elif now < window.starts_at:
            window.phase = "pending"
        else:
            window.phase = "ended"
        window.phase_label = PHASES[window.phase]
        window.phase_badge = PHASE_BADGES[window.phase]
    return windows


def _asset_names(target_type, target_ids):
    model = TARGET_MODELS[target_type]
    label_field = "name" if target_type == "server" else "device_name"
    return {
        str(pk): name
        for pk, name in model.objects.filter(pk__in=target_ids).values_list("pk", label_field)
    }


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

    known = _asset_names(target_type, requested)
    missing = [value for value in requested if value not in known]
    if missing:
        messages.error(request, "有 %d 个选中项已不存在，请刷新列表后重新选择。" % len(missing))
        return redirect("asset_list", kind)

    data = form.cleaned_data
    MaintenanceWindow.objects.bulk_create([
        MaintenanceWindow(
            target_type=target_type, target_id=value,
            starts_at=data["starts_at"], ends_at=data["ends_at"], note=data["note"],
        )
        for value in known
    ])
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
    """Every window, newest first, with its live phase."""
    state = (request.GET.get("state") or "").strip()
    windows = MaintenanceWindow.objects.order_by("-starts_at")
    now = timezone.now()
    if state == "active":
        windows = windows.filter(is_enabled=True, starts_at__lte=now, ends_at__gt=now)
    elif state == "pending":
        windows = windows.filter(is_enabled=True, starts_at__gt=now)
    elif state == "ended":
        windows = windows.filter(ends_at__lte=now)
    page = Paginator(windows, 50).get_page(request.GET.get("page"))
    rows = attach_phase(list(page.object_list), now)
    names = {}
    for target_type in TARGET_MODELS:
        ids = [row.target_id for row in rows if row.target_type == target_type]
        if ids:
            names.update(_asset_names(target_type, ids))
    for row in rows:
        row.asset_name = names.get(str(row.target_id), str(row.target_id))
        row.type_label = TARGET_LABELS.get(row.target_type, row.target_type)
    return render(request, "maintenance/list.html", {
        "page_obj": page,
        "windows": rows,
        "state": state,
        "states": (("", "全部"), ("active", "进行中"), ("pending", "未开始"), ("ended", "已结束")),
    })
