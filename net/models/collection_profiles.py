"""Reusable collection settings and per-device overrides; credentials are separate."""
import uuid
from django.core.exceptions import ValidationError
from django.db import models

KINDS = [('networks', '网络设备'), ('servers', '服务器'), ('monitors', '安防设备')]

class DeviceCollectionTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=16, choices=KINDS)
    vendor = models.CharField(max_length=64, blank=True, default='')
    subtype = models.CharField(max_length=32, blank=True, default='')
    version_match = models.CharField('版本匹配关键字', max_length=96, blank=True, default='', db_default='')
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    settings = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['kind','vendor','subtype','version_match'], name='net_collection_template_scope_v2')]
    def clean(self):
        from net.devices.collection_profiles import normalize_vendor, normalize_subtype, normalize_version_match, validate_settings
        self.version_match = normalize_version_match(self.version_match)
        self.vendor = normalize_vendor(self.vendor)
        self.subtype = normalize_subtype(self.kind, self.subtype)
        if self.version_match and (self.kind != 'networks' or not self.vendor or not self.subtype or not self.parent_id):
            raise ValidationError({'version_match': '版本模板仅适用于网络设备，需选择厂商、设备类型和对应的类型父模板。'})
        if self.kind == 'servers' and self.vendor:
            raise ValidationError({'vendor':'服务器模板只按操作系统分类，不配置厂商。'})
        if self.parent_id:
            parent = self.parent
            if parent.version_match:
                raise ValidationError({'parent': '系统版本模板是最后一层模板，不能继续作为父模板。'})
            if self.version_match and (not parent.subtype or parent.subtype != self.subtype):
                raise ValidationError({'parent': '系统版本模板必须继承同厂商、同设备类型的类型模板。'})
            if parent.kind != self.kind or parent.vendor != self.vendor or (parent.subtype and parent.subtype != self.subtype):
                raise ValidationError({'parent': '父模板必须属于同一类别、厂商，且为基础模板或相同设备类型。'})
            seen = {self.pk}
            while parent:
                if parent.pk in seen:
                    raise ValidationError({'parent': '模板不能继承自身或形成循环。'})
                seen.add(parent.pk)
                parent = parent.parent
        if self.pk and self.children.exclude(kind=self.kind, vendor=self.vendor).exists():
            raise ValidationError('已有子模板，不能修改成不同类别或厂商。')
        if self.pk and self.subtype and self.children.exclude(subtype=self.subtype).exists():
            raise ValidationError('已有其他类型子模板，不能把基础模板改为特定设备类型。')
        if self.version_match and self.pk and self.children.exists():
            raise ValidationError('已有子模板，不能改为系统版本模板。')
        validate_settings(self.kind, self.settings)
    def __str__(self):
        return self.name

class DeviceCollectionBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=KINDS)
    target_id = models.UUIDField()
    template = models.ForeignKey(DeviceCollectionTemplate, null=True, blank=True, on_delete=models.PROTECT)
    overrides = models.JSONField(default=dict, blank=True)
    encrypted_credentials = models.TextField(blank=True, default='', editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['kind','target_id'], name='net_collection_binding_target')]
    def clean(self):
        from net.devices.collection_profiles import validate_settings
        validate_settings(self.kind, self.overrides)
        if self.template_id and self.template.kind != self.kind:
            raise ValidationError('模板与设备类别不一致。')
