"""Planned maintenance windows for one device, managed from its detail page."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from index.common.access import admin_required
from net.models import MaintenanceWindow, Network_Device

PHASES = {"active": "进行中", "pending": "未开始", "ended": "已结束"}
PHASE_BADGES = {"active": "warning", "pending": "info", "ended": "neutral"}


class MaintenanceWindowForm(forms.Form):
    starts_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    ends_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    note = forms.CharField(required=False, max_length=200)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("starts_at")
        end = cleaned.get("ends_at")
        if start and end and end <= start:
            raise forms.ValidationError("结束时间必须晚于开始时间。")
        return cleaned


def windows_for(device, *, now=None):
    """Recent windows with the phase the page needs; never touches the database again."""
    now = now or timezone.now()
    windows = []
    for window in MaintenanceWindow.objects.filter(device_id=device.pk).order_by("-starts_at")[:10]:
        if window.starts_at <= now < window.ends_at:
            window.phase = "active"
        elif now < window.starts_at:
            window.phase = "pending"
        else:
            window.phase = "ended"
        window.phase_label = PHASES[window.phase]
        window.phase_badge = PHASE_BADGES[window.phase]
        windows.append(window)
    return windows


def form_defaults(now=None):
    start = timezone.localtime(now or timezone.now()).replace(second=0, microsecond=0)
    return {
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
    }


@admin_required
@require_POST
def maintenance_window_create(request, kind, pk):
    device = get_object_or_404(Network_Device, pk=pk)
    form = MaintenanceWindowForm(request.POST)
    if not form.is_valid():
        messages.error(request, "维护窗口未保存：%s" % " ".join(
            "%s %s" % (field, " ".join(errors)) for field, errors in form.errors.items()
        ))
        return redirect("asset_detail", kind, device.pk)
    data = form.cleaned_data
    MaintenanceWindow.objects.create(
        device_id=device.pk, starts_at=data["starts_at"], ends_at=data["ends_at"], note=data["note"],
    )
    messages.success(request, "维护窗口已保存：窗口内这台设备的告警照常入库，但不投递。")
    return redirect("asset_detail", kind, device.pk)


@admin_required
@require_POST
def maintenance_window_delete(request, kind, pk, window_id):
    get_object_or_404(Network_Device, pk=pk)
    deleted, _ = MaintenanceWindow.objects.filter(pk=window_id, device_id=pk).delete()
    messages.success(request, "维护窗口已删除。" if deleted else "维护窗口不存在。")
    return redirect("asset_detail", kind, pk)
