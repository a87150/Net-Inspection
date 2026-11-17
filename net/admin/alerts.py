"""Admin entry for planned maintenance windows."""
from django import forms
from django.contrib import admin

from net.models import MaintenanceWindow, Network_Device


class MaintenanceWindowAdminForm(forms.ModelForm):
    """Pick a device; the row stores its UUID without a hard foreign key."""

    device = forms.ModelChoiceField(queryset=Network_Device.objects.none(), label="设备")

    class Meta:
        model = MaintenanceWindow
        fields = ["device", "starts_at", "ends_at", "note", "is_enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device"].queryset = Network_Device.objects.order_by("device_name")
        if self.instance.pk:
            self.fields["device"].initial = self.instance.device_id

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.device_id = self.cleaned_data["device"].pk
        if commit:
            instance.save()
        return instance


@admin.register(MaintenanceWindow)
class MaintenanceWindowAdmin(admin.ModelAdmin):
    form = MaintenanceWindowAdminForm
    list_display = ("device_name", "starts_at", "ends_at", "is_enabled", "note")
    list_filter = ("is_enabled",)
    search_fields = ("note",)

    @admin.display(description="设备")
    def device_name(self, obj):
        device = Network_Device.objects.filter(pk=obj.device_id).first()
        return str(device) if device else str(obj.device_id)
