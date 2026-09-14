import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
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


class Domain_Account(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    object_guid = models.UUIDField(null=True, blank=True, unique=True)
    distinguished_name = models.CharField(max_length=1024, null=True, blank=True, db_index=True)
    account_name = models.CharField(max_length=255)
    login_name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    ou = models.CharField(max_length=255, blank=True, null=True)
    allowed_workstations = models.TextField(blank=True, null=True)
    last_login_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.login_name


class Domain_Computer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    object_guid = models.UUIDField(null=True, blank=True, unique=True)
    distinguished_name = models.CharField(max_length=1024, null=True, blank=True, db_index=True)
    computer_name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    ou = models.CharField(max_length=255, blank=True, null=True)
    os = models.CharField(max_length=255, blank=True, null=True)
    last_login_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.computer_name


class Domain_Group(models.Model):
    class Scope(models.TextChoices):
        DOMAIN_LOCAL = 'domain_local', '域本地'
        GLOBAL = 'global', '全局'
        UNIVERSAL = 'universal', '通用'
        UNKNOWN = 'unknown', '未知'

    class Category(models.TextChoices):
        SECURITY = 'security', '安全组'
        DISTRIBUTION = 'distribution', '通讯组'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    object_guid = models.UUIDField(null=True, blank=True, unique=True)
    distinguished_name = models.CharField(max_length=1024, unique=True, db_index=True)
    group_name = models.CharField(max_length=255)
    login_name = models.CharField(max_length=255, blank=True, db_index=True)
    description = models.TextField(blank=True)
    ou = models.CharField(max_length=255, blank=True)
    group_scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.UNKNOWN)
    group_category = models.CharField(
        max_length=16, choices=Category.choices, default=Category.SECURITY,
    )
    member_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.group_name


class Domain_Controller_Config(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    name = models.CharField(max_length=100, default='主域控')
    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(default=389)
    use_ssl = models.BooleanField(default=False)
    base_dn = models.CharField(max_length=500, blank=True, help_text='例如：DC=example,DC=com')
    bind_username = models.CharField(max_length=255, blank=True)
    bind_password = models.CharField(max_length=255, blank=True)
    user_filter = models.CharField(max_length=500, default='(&(objectCategory=person)(objectClass=user))')
    computer_filter = models.CharField(max_length=500, default='(objectCategory=computer)')
    group_filter = models.CharField(max_length=500, default='(objectCategory=group)')
    inactive_days = models.PositiveIntegerField('未登录天数', default=60, validators=[MinValueValidator(1), MaxValueValidator(36500)])
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class DomainOperation(models.Model):
    """The immutable audit envelope for one queued domain operation."""

    class ObjectType(models.TextChoices):
        ACCOUNT = 'account', '域账号'
        COMPUTER = 'computer', '域计算机'

    class Action(models.TextChoices):
        CREATE_USER = 'create_user', '添加用户'
        MOVE_OU = 'move_ou', '移动到 OU'
        ADD_GROUP = 'add_group', '加入安全组'
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
