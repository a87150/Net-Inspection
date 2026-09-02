import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


SENSITIVE_PAYLOAD_KEYS = frozenset({
    'password', 'new_password', 'bind_password', 'unicode_pwd',
    'user_password', 'initial_password', 'ciphertext', 'encrypted_payload',
})


def _normalized_payload_key(key):
    value = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', str(key).strip())
    return re.sub(r'[^a-z0-9]+', '_', value.casefold()).strip('_')


def contains_sensitive_payload(value):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _normalized_payload_key(key)
            parts = normalized.split('_')
            if (
                normalized in SENSITIVE_PAYLOAD_KEYS
                or 'secret' in parts
                or 'token' in parts
            ):
                return True
            if contains_sensitive_payload(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(contains_sensitive_payload(child) for child in value)
    return False


def validate_parameter_summary(value):
    """Keep password and encrypted secret material out of persisted audit data."""
    if not isinstance(value, dict):
        raise ValidationError('参数摘要必须是 JSON 对象。')

    if contains_sensitive_payload(value):
        raise ValidationError('参数摘要不能包含敏感载荷。')


class DomainOperation(models.Model):
    """The immutable audit envelope for one queued domain operation."""

    class ObjectType(models.TextChoices):
        ACCOUNT = 'account', '域账号'
        COMPUTER = 'computer', '域计算机'

    class Action(models.TextChoices):
        CREATE_USER = 'create_user', '添加用户'
        MOVE_OU = 'move_ou', '移动到 OU'
        ADD_GROUP = 'add_group', '加入安全组'
        REMOVE_GROUP = 'remove_group', '移出安全组'
        RESET_PASSWORD = 'reset_password', '重置密码'
        MUST_CHANGE_PASSWORD = 'must_change_password', '下次登录修改密码'
        PASSWORD_NEVER_EXPIRES = 'password_never_expires', '密码永不过期'
        UNLOCK = 'unlock', '解锁'
        ENABLE = 'enable', '启用'
        DISABLE = 'disable', '停用'

    class Status(models.TextChoices):
        QUEUED = 'queued', '等待'
        RUNNING = 'running', '运行中'
        SUCCESS = 'success', '成功'
        PARTIAL = 'partial', '部分成功'
        FAILED = 'failed', '失败'
        CANCELLED = 'cancelled', '已取消'

    IMMUTABLE_AUDIT_FIELDS = (
        'action', 'object_type', 'requested_by_id', 'target_count',
        'parameter_summary', 'task_id',
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.CharField(max_length=32, choices=Action.choices)
    object_type = models.CharField(max_length=16, choices=ObjectType.choices)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='domain_operations',
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    target_count = models.PositiveIntegerField()
    parameter_summary = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_parameter_summary],
    )
    task = models.OneToOneField(
        'net.TaskRun',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='domain_operation',
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ('manage_domain_operations', 'Can manage domain operations'),
        ]

    def save(self, *args, **kwargs):
        validate_parameter_summary(self.parameter_summary)
        if not self._state.adding:
            original = type(self).objects.only(*self.IMMUTABLE_AUDIT_FIELDS).get(
                pk=self.pk,
            )
            changed = {
                field_name.removesuffix('_id'): '域控操作审计字段创建后不能修改。'
                for field_name in self.IMMUTABLE_AUDIT_FIELDS
                if getattr(self, field_name) != getattr(original, field_name)
            }
            if changed:
                raise ValidationError(changed)
        super().save(*args, **kwargs)


class DomainOperationSecret(models.Model):
    """Private encrypted payload consumed once by the Worker."""

    operation = models.OneToOneField(
        DomainOperation,
        on_delete=models.CASCADE,
        related_name='secret',
    )
    encrypted_payload = models.BinaryField()
    purpose_fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
