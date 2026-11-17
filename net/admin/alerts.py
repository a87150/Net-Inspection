"""Admin entry for planned maintenance windows."""
from django import forms
from django.contrib import admin

from net.models import MaintenanceWindow, Network_Device, Server, WeakCurrentDevice

TARGET_MODELS = {
    MaintenanceWindow.TargetType.NETWORK_DEVICE: (Network_Device, "device_name"),
    MaintenanceWindow.TargetType.SERVER: (Server, "name"),
    MaintenanceWindow.TargetType.WEAK_CURRENT: (WeakCurrentDevice, "device_name"),
}


class MaintenanceWindowAdminForm(forms.ModelForm):
    """Pick an asset; the row stores its UUID without a hard foreign key."""

    target_id = forms.ChoiceField(choices=(), label="资产")

    class Meta:
        model = MaintenanceWindow
        fields = ["target_type", "target_id", "starts_at", "ends_at", "note", "is_enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target_type = (
            self.data.get("target_type")
            or getattr(self.instance, "target_type", "")
            or MaintenanceWindow.TargetType.NETWORK_DEVICE
        )
        model, label_field = TARGET_MODELS.get(
            target_type, TARGET_MODELS[MaintenanceWindow.TargetType.NETWORK_DEVICE],
        )
        self.fields["target_id"].choices = [
            (str(pk), name)
            for pk, name in model.objects.order_by(label_field).values_list("pk", label_field)
        ]
        if self.instance.pk:
            self.fields["target_id"].initial = str(self.instance.target_id)


@admin.register(MaintenanceWindow)
class MaintenanceWindowAdmin(admin.ModelAdmin):
    form = MaintenanceWindowAdminForm
    list_display = ("target_name", "target_type", "starts_at", "ends_at", "is_enabled", "note")
    list_filter = ("target_type", "is_enabled")
    search_fields = ("note",)

    @admin.display(description="资产")
    def target_name(self, obj):
        model, label_field = TARGET_MODELS.get(
            obj.target_type, TARGET_MODELS[MaintenanceWindow.TargetType.NETWORK_DEVICE],
        )
        name = model.objects.filter(pk=obj.target_id).values_list(label_field, flat=True).first()
        return name or str(obj.target_id)
