"""Write-side serializers for the REST layer.

Read rows come from the page payload builders (assets.maintenance.row_payloads)
so the hydrated first paint and the REST list share one field definition.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from assets.maintenance import KIND_TARGET_TYPES, TARGET_MODELS, asset_names
from net.models import IpSubnet, MaintenanceWindow
from net.models.ipam import normalize_cidr


class MaintenanceWindowCreateSerializer(serializers.Serializer):
    """Batch create: one window per selected asset."""

    target_type = serializers.ChoiceField(choices=[
        (value, label) for value, label in MaintenanceWindow.TargetType.choices
    ])
    target_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=500,
    )
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate(self, attrs):
        if attrs["ends_at"] <= attrs["starts_at"]:
            raise serializers.ValidationError({"ends_at": "结束时间必须晚于开始时间。"})
        return attrs

    def validate_target_ids(self, value):
        target_type = self.initial_data.get("target_type")
        if target_type not in TARGET_MODELS:
            raise serializers.ValidationError("未知的资产类型。")
        known = asset_names(target_type, value)
        missing = [str(item) for item in value if str(item) not in known]
        if missing:
            raise serializers.ValidationError(
                "有 %d 个资产不存在或不属于该类型。" % len(missing),
            )
        return value


class IpSubnetCreateSerializer(serializers.Serializer):
    """One subnet. CIDR normalization and pool checks stay in the model's clean()."""

    cidr = serializers.CharField(max_length=43)
    purpose = serializers.ChoiceField(
        choices=[(value, label) for value, label in IpSubnet.Purpose.choices],
        required=False,
    )
    gateway = serializers.CharField(max_length=39, required=False, allow_blank=True)
    vlan = serializers.CharField(max_length=32, required=False, allow_blank=True)
    pool_start = serializers.CharField(max_length=39, required=False, allow_blank=True)
    pool_end = serializers.CharField(max_length=39, required=False, allow_blank=True)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True)

    def validate_cidr(self, value):
        """Point the CIDR error at the field the operator typed into."""
        try:
            return normalize_cidr(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages[0]) from error

    def validate(self, attrs):
        for key in ("gateway", "pool_start", "pool_end"):
            attrs[key] = attrs.get(key) or None
        attrs.setdefault("purpose", IpSubnet.Purpose.OFFICE)
        attrs.setdefault("vlan", "")
        attrs.setdefault("note", "")
        subnet = IpSubnet(**attrs)
        try:
            subnet.full_clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(
                getattr(error, "message_dict", error.messages),
            ) from error
        # clean() rewrote the CIDR to its network address; persist that form.
        attrs["cidr"] = subnet.cidr
        return attrs


def kind_target_type(kind):
    """List-page kind -> alert target type; None when the kind has no windows."""
    return KIND_TARGET_TYPES.get(kind)
