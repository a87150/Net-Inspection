"""Persistent alert-policy, state, event, and delivery contracts.

Delivery adapters deliberately live elsewhere.  These models only preserve the
validated configuration and the history that later worker code will use.
"""

import math
import re
import uuid
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, URLValidator, validate_email
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone


MAX_DELIVERY_ATTEMPTS = 5
_SMTP_HOST_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$')
ALERT_SCOPE_FIELDS = ('profile_type', 'profile_id', 'target_type', 'target_id')


class _ScopedHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if set(kwargs).intersection(self.model.SCOPE_WRITE_FIELDS):
            raise ValidationError('范围和历史关联必须通过实例 save() 校验后更新。')
        return super().update(**kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        if kwargs.get('update_conflicts'):
            raise ValidationError('告警历史不支持绕过范围校验的批量覆盖。')
        objs = list(objs)
        for obj in objs:
            obj._validate_scope(self.db)
        return super().bulk_create(objs, *args, **kwargs)


class _PolicyQuerySet(models.QuerySet):
    def _guard_scope_fields(self, fields):
        if set(fields).intersection(self.model.SCOPE_WRITE_FIELDS):
            raise ValidationError('已保存的告警策略归属不能修改。')

    def update(self, **kwargs):
        self._guard_scope_fields(kwargs)
        return super().update(**kwargs)

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False,
                    update_conflicts=False, update_fields=None, unique_fields=None):
        if update_conflicts:
            self._guard_scope_fields(update_fields or ())
        return super().bulk_create(
            objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts, update_fields=update_fields,
            unique_fields=unique_fields,
        )


def validate_finite_json(value):
    """Reject NaN and Infinity before a JSONField reaches the database."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError('JSON 配置只能包含有限数值。')
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError('JSON 对象的键必须是字符串。')
            validate_finite_json(item)
    elif isinstance(value, list):
        for item in value:
            validate_finite_json(item)


def _https_webhook(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError('必须填写 HTTPS Webhook 地址。')
    try:
        URLValidator(schemes=['https'])(value)
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            raise ValueError
    except (ValidationError, ValueError):
        raise ValidationError('Webhook 地址必须是有效的 HTTPS 地址。') from None


def _optional_text(value, field_name):
    if value in (None, ''):
        return
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f'{field_name} 必须是非空文本。')


def _validate_email_settings(settings):
    required = {'smtp_host', 'smtp_port', 'use_tls', 'use_ssl', 'from_email', 'recipients'}
    if missing := required.difference(settings):
        raise ValidationError('邮件渠道缺少必要配置。')
    allowed = required | {'username', 'password'}
    if set(settings).difference(allowed):
        raise ValidationError('邮件渠道包含不支持的配置项。')

    host = settings['smtp_host']
    if not isinstance(host, str) or not _SMTP_HOST_RE.fullmatch(host):
        raise ValidationError('SMTP 主机地址无效。')
    port = settings['smtp_port']
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValidationError('SMTP 端口必须在 1 到 65535 之间。')
    if not isinstance(settings['use_tls'], bool) or not isinstance(settings['use_ssl'], bool):
        raise ValidationError('TLS 和 SSL 设置必须是布尔值。')
    if settings['use_tls'] and settings['use_ssl']:
        raise ValidationError('TLS 与 SSL 不能同时启用。')

    try:
        validate_email(settings['from_email'])
    except ValidationError as exc:
        raise ValidationError('发件人邮箱地址无效。') from exc
    recipients = settings['recipients']
    if not isinstance(recipients, list) or not recipients:
        raise ValidationError('必须至少配置一个邮件收件人。')
    normalized_recipients = []
    for recipient in recipients:
        try:
            validate_email(recipient)
        except ValidationError as exc:
            raise ValidationError('邮件收件人地址无效。') from exc
        normalized_recipients.append(recipient.casefold())
    if len(normalized_recipients) != len(set(normalized_recipients)):
        raise ValidationError('邮件收件人不能重复。')
    _optional_text(settings.get('username'), 'SMTP 账号')
    _optional_text(settings.get('password'), 'SMTP 密码')


def validate_channel_settings(channel_type, settings):
    """Validate typed channel settings without ever echoing credentials."""
    if not isinstance(settings, dict):
        raise ValidationError('渠道配置必须是 JSON 对象。')
    validate_finite_json(settings)

    if channel_type in {'feishu', 'dingtalk'}:
        allowed = {'webhook_url', 'secret'}
        if 'webhook_url' not in settings or set(settings).difference(allowed):
            raise ValidationError('机器人渠道配置项无效。')
        _https_webhook(settings['webhook_url'])
        _optional_text(settings.get('secret'), '机器人签名密钥')
    elif channel_type == 'email':
        _validate_email_settings(settings)
    else:
        raise ValidationError('不支持的告警渠道类型。')


class AlertChannel(models.Model):
    class ChannelType(models.TextChoices):
        FEISHU = 'feishu', '飞书'
        DINGTALK = 'dingtalk', '钉钉'
        EMAIL = 'email', '邮件'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    channel_type = models.CharField(max_length=16, choices=ChannelType.choices)
    is_enabled = models.BooleanField(default=True)
    settings = models.JSONField(default=dict, validators=[validate_finite_json])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(channel_type__in=('feishu', 'dingtalk', 'email')),
            name='net_alert_channel_type_ck',
        )]

    def clean(self):
        super().clean()
        try:
            validate_channel_settings(self.channel_type, self.settings)
        except ValidationError as exc:
            # Never attach user-provided values to validation messages.
            raise ValidationError({'settings': exc.messages}) from exc

    def __str__(self):
        return f'{self.get_channel_type_display()}：{self.name}'


class AlertPolicy(models.Model):
    """A policy with immutable ownership but editable delivery preferences."""

    DEFAULT_SLOT = 'default'
    SCOPE_FIELDS = ('is_default', 'default_slot', 'inspection_profile_id', 'analysis_profile_id')
    SCOPE_WRITE_FIELDS = (*SCOPE_FIELDS, 'inspection_profile', 'analysis_profile')
    objects = _PolicyQuerySet.as_manager()

    class Mode(models.TextChoices):
        INHERIT = 'inherit', '继承默认告警策略'
        OVERRIDE = 'override', '使用项目告警策略'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    is_default = models.BooleanField(default=False)
    # A nullable unique field works on both MySQL and SQLite, unlike a
    # conditional unique constraint.  Save keeps it in sync with is_default.
    default_slot = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        unique=True,
        editable=False,
    )
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.INHERIT)
    inspection_profile = models.ForeignKey(
        'net.InspectionProfile',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='alert_policies',
    )
    analysis_profile = models.ForeignKey(
        'net.ComputerAnalysisProfile',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='alert_policies',
    )
    channels = models.ManyToManyField(AlertChannel, related_name='policies', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mode__in=('inherit', 'override')),
                name='net_alert_policy_mode_ck',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_default=True,
                        default_slot='default',
                        mode='override',
                        inspection_profile__isnull=True,
                        analysis_profile__isnull=True,
                    )
                    | models.Q(
                        is_default=False,
                        default_slot__isnull=True,
                        inspection_profile__isnull=False,
                        analysis_profile__isnull=True,
                    )
                    | models.Q(
                        is_default=False,
                        default_slot__isnull=True,
                        inspection_profile__isnull=True,
                        analysis_profile__isnull=False,
                    )
                ),
                name='net_alert_policy_shape_ck',
            ),
            models.UniqueConstraint(
                fields=('inspection_profile',),
                name='net_alert_policy_inspection_uniq',
            ),
            models.UniqueConstraint(
                fields=('analysis_profile',),
                name='net_alert_policy_analysis_uniq',
            ),
        ]

    @property
    def profile_type(self):
        if self.inspection_profile_id is not None:
            return 'inspection_profile'
        if self.analysis_profile_id is not None:
            return 'computer_analysis_profile'
        return 'default'

    @property
    def profile_id(self):
        return self.inspection_profile_id or self.analysis_profile_id

    @classmethod
    def default_policy(cls):
        return cls.objects.get(default_slot=cls.DEFAULT_SLOT)

    def effective_channels(self, *, default_policy=None):
        if self.is_default or self.mode == self.Mode.OVERRIDE:
            return list(self.channels.filter(is_enabled=True).order_by('name'))
        default_policy = default_policy or self.default_policy()
        return list(default_policy.channels.filter(is_enabled=True).order_by('name'))

    def clean(self):
        super().clean()
        errors = {}
        has_inspection = self.inspection_profile_id is not None
        has_analysis = self.analysis_profile_id is not None
        if self.is_default:
            if has_inspection or has_analysis:
                errors['is_default'] = '默认策略不能关联项目配置。'
            if self.mode != self.Mode.OVERRIDE:
                errors['mode'] = '默认策略必须使用覆盖模式。'
        elif has_inspection == has_analysis:
            errors['inspection_profile'] = '项目策略必须且只能关联一个项目配置。'
            errors['analysis_profile'] = '项目策略必须且只能关联一个项目配置。'

        if self.pk:
            channel_count = self.channels.count()
            if self.mode == self.Mode.INHERIT:
                if channel_count:
                    errors['channels'] = '继承策略不能选择渠道。'
                if not type(self).objects.filter(default_slot=self.DEFAULT_SLOT).exclude(pk=self.pk).exists():
                    errors['mode'] = '继承策略需要已保存的默认告警策略。'
            elif not self.is_default and channel_count == 0:
                errors['channels'] = '覆盖策略至少需要选择一个渠道。'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Freeze scope from the first save, not just after an event references it:
        # this also closes the race between policy edits and new history rows.
        alias = kwargs.get('using') or self._state.db or 'default'
        original = type(self).objects.using(alias).filter(pk=self.pk).values(*self.SCOPE_FIELDS).first()
        if original and any(original[field] != getattr(self, field) for field in self.SCOPE_FIELDS):
            raise ValidationError('已保存的告警策略归属不能修改。')
        self.default_slot = self.DEFAULT_SLOT if self.is_default else None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class AlertState(models.Model):
    SCOPE_WRITE_FIELDS = (*ALERT_SCOPE_FIELDS, 'finding_key', 'last_event', 'last_event_id')
    objects = _ScopedHistoryQuerySet.as_manager()

    class Status(models.TextChoices):
        NORMAL = 'normal', '正常'
        ABNORMAL = 'abnormal', '异常'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile_type = models.CharField(max_length=32)
    profile_id = models.CharField(max_length=64)
    target_type = models.CharField(max_length=32)
    target_id = models.CharField(max_length=64)
    finding_key = models.CharField(max_length=191)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NORMAL)
    finding_snapshot = models.JSONField(default=dict, blank=True, validators=[validate_finite_json])
    last_seen_at = models.DateTimeField(default=timezone.now)
    last_abnormal_at = models.DateTimeField(null=True, blank=True)
    last_normal_at = models.DateTimeField(null=True, blank=True)
    last_event = models.ForeignKey(
        'net.AlertEvent',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='latest_for_states',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=('normal', 'abnormal')),
                name='net_alert_state_status_ck',
            ),
            models.UniqueConstraint(
                fields=('profile_type', 'profile_id', 'target_type', 'target_id', 'finding_key'),
                name='net_alert_state_finding_uniq',
            ),
        ]
        indexes = [
            models.Index(
                fields=('profile_type', 'profile_id', 'target_type', 'target_id'),
                name='net_alert_state_lookup_idx',
            ),
        ]

    def clean(self):
        super().clean()
        self._validate_scope()
        if not self.profile_type or not self.profile_id:
            raise ValidationError({'profile_type': '必须提供稳定的项目标识。'})
        if not self.target_type or not self.target_id:
            raise ValidationError({'target_type': '必须提供稳定的目标标识。'})
        if not self.finding_key.strip():
            raise ValidationError({'finding_key': '异常类别键不能为空。'})

    def _validate_scope(self, using=None):
        alias = using or self._state.db or 'default'
        original = type(self).objects.using(alias).filter(pk=self.pk).first()
        if original and any(getattr(original, field) != getattr(self, field)
                            for field in (*ALERT_SCOPE_FIELDS, 'finding_key')):
            raise ValidationError('已保存的告警状态标识不能修改。')
        if self.last_event_id:
            event = AlertEvent.objects.using(alias).filter(pk=self.last_event_id).first()
            if event is None or _scope(event) != _scope(self):
                raise ValidationError({'last_event': '状态与事件必须属于同一项目和目标。'})

    def save(self, *args, **kwargs):
        self._validate_scope(kwargs.get('using'))
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.profile_type}/{self.target_type}：{self.finding_key}'


class AlertEvent(models.Model):
    SCOPE_WRITE_FIELDS = (*ALERT_SCOPE_FIELDS, 'task', 'task_id', 'target_run', 'target_run_id', 'policy', 'policy_id', 'summary_task', 'summary_task_id')
    objects = _ScopedHistoryQuerySet.as_manager()

    class EventType(models.TextChoices):
        ABNORMAL = 'abnormal', '异常告警'
        RECOVERY = 'recovery', '恢复通知'
        SUMMARY = 'summary', '任务总结'

    class Status(models.TextChoices):
        PENDING = 'pending', '待发送'
        SENDING = 'sending', '发送中'
        DELIVERED = 'delivered', '已送达'
        RECORDED = 'recorded', '仅记录'
        PARTIAL = 'partial', '部分送达'
        FAILED = 'failed', '发送失败'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        'net.TaskRun',
        on_delete=models.PROTECT,
        related_name='alert_events',
    )
    target_run = models.ForeignKey(
        'net.TaskTargetRun',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='alert_events',
    )
    summary_task = models.OneToOneField(
        'net.TaskRun', null=True, blank=True, on_delete=models.PROTECT,
        related_name='summary_alert',
    )
    summary_data = models.JSONField(default=dict)
    policy = models.ForeignKey(
        AlertPolicy,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='events',
    )
    profile_type = models.CharField(max_length=32)
    profile_id = models.CharField(max_length=64)
    target_type = models.CharField(max_length=32)
    target_id = models.CharField(max_length=64)
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    severity = models.CharField(max_length=16, blank=True)
    summary = models.TextField(blank=True)
    findings = models.JSONField(default=list, validators=[validate_finite_json])
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    states = models.ManyToManyField(AlertState, related_name='events', blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(event_type__in=('abnormal', 'recovery', 'summary')),
                name='net_alert_event_type_ck',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=('pending', 'sending', 'delivered', 'partial', 'failed', 'recorded')),
                name='net_alert_event_status_ck',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(event_type='summary', summary_task__isnull=False,
                             summary_task=models.F('task'), target_run__isnull=True,
                             target_type='task')
                    | models.Q(event_type__in=('abnormal', 'recovery'),
                               summary_task__isnull=True, target_run__isnull=False)
                ),
                name='net_alert_event_summaryshape_ck',
            ),
            models.UniqueConstraint(
                fields=('target_run', 'event_type'),
                name='net_alert_event_target_type_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=('status', 'occurred_at'), name='net_alert_event_status_idx'),
            models.Index(fields=('profile_type', 'profile_id'), name='net_alert_event_profile_idx'),
        ]

    def clean(self):
        super().clean()
        self._validate_scope()
        errors = {}
        if not isinstance(self.findings, list):
            errors['findings'] = '合并异常必须是列表。'
        else:
            for finding in self.findings:
                if not isinstance(finding, dict) or not all(
                    isinstance(finding.get(field), str) and finding[field].strip()
                    for field in ('key', 'severity', 'title')
                ):
                    errors['findings'] = '每项异常必须包含键、级别和标题。'
                    break
        if errors:
            raise ValidationError(errors)

    def _validate_scope(self, using=None):
        from net.models.tasks import TaskRun, TaskTargetRun

        alias = using or self._state.db or 'default'
        if self.event_type == self.EventType.SUMMARY:
            if self.summary_task_id is None or self.summary_task_id != self.task_id:
                raise ValidationError({'summary_task': '总结必须关联同一任务。'})
            if self.target_run_id is not None:
                raise ValidationError({'target_run': '总结不能关联设备目标。'})
            task = TaskRun.objects.using(alias).filter(pk=self.task_id).first()
            if task is None:
                raise ValidationError({'task': '总结任务必须存在。'})
            target = None
        else:
            if self.summary_task_id is not None:
                raise ValidationError({'summary_task': '设备事件不能关联总结任务。'})
            target = TaskTargetRun.objects.using(alias).select_related('task').filter(pk=self.target_run_id).first()
            if target is None or self.task_id != target.task_id:
                raise ValidationError({'target_run': '告警目标必须存在且属于关联任务。'})
            task = target.task
        if task.task_type == 'inspection' and task.inspection_profile_id and not task.analysis_profile_id:
            profile_type, profile_id = 'inspection_profile', str(task.inspection_profile_id)
        elif task.task_type in ('computer_analysis', 'computer_fetch') and task.analysis_profile_id and not task.inspection_profile_id:
            profile_type, profile_id = 'computer_analysis_profile', str(task.analysis_profile_id)
        else:
            raise ValidationError({'task': '任务配置类型无效。'})
        snapshot_id = task.profile_snapshot.get('id')
        if snapshot_id is not None and str(snapshot_id) != profile_id:
            raise ValidationError({'task': '任务配置快照与关联配置不一致。'})
        canonical_target_type, canonical_target_id = (
            (target.target_type, target.target_id) if target else ('task', str(task.pk))
        )
        # A computer-analysis task is queued against an uploaded log, but its
        # durable alert identity is the Computer recorded by the analysis.  A
        # missing/failed analysis deliberately retains the log scope: it is not
        # evidence that a different computer is healthy.
        if target is not None and task.task_type == 'computer_analysis' and target.result_type == 'computer_analysis':
            from net.models.records import ComputerAnalysis

            analysis = ComputerAnalysis.objects.using(alias).filter(
                pk=target.result_id,
            ).only('computer_id').first()
            if analysis is not None:
                canonical_target_type, canonical_target_id = 'computer', str(analysis.computer_id)
        canonical = (profile_type, profile_id, canonical_target_type, canonical_target_id)
        errors = {}
        original = type(self).objects.using(alias).filter(pk=self.pk).first()
        if original and (original.task_id != self.task_id or original.target_run_id != self.target_run_id
                         or original.summary_task_id != self.summary_task_id):
            errors['target_run'] = '已保存的告警任务和目标不能修改。'
        for field, value in zip(ALERT_SCOPE_FIELDS, canonical):
            supplied = getattr(self, field)
            if supplied and str(supplied) != value:
                errors[field] = '告警标识必须与任务目标快照一致。'
            else:
                setattr(self, field, value)
        if self.policy_id:
            policy = AlertPolicy.objects.using(alias).filter(pk=self.policy_id).first()
            if policy is None or not (
                (policy.is_default and policy.default_slot == AlertPolicy.DEFAULT_SLOT)
                or (policy.profile_type == profile_type and str(policy.profile_id) == profile_id)
            ):
                errors['policy'] = '告警策略必须是默认策略或该项目的策略。'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self._validate_scope(kwargs.get('using'))
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_event_type_display()}：{self.target_type}/{self.target_id}'

    @property
    def delivery_outcomes(self):
        """Safe, per-channel outcomes shared by the history table and CSV."""
        return '; '.join(
            f'{delivery.channel.name}: {delivery.get_status_display()}'
            for delivery in sorted(self.deliveries.all(), key=lambda row: (row.channel.name, str(row.pk)))
        )


class AlertNotificationTemplate(models.Model):
    class Mode(models.TextChoices):
        COMPACT = 'compact', '简洁'
        DETAILED = 'detailed', '详细'

    key = models.CharField(max_length=32, primary_key=True, default='task_summary')
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.COMPACT)
    title_template = models.CharField(max_length=255, blank=True)
    body_template = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(key='task_summary'), name='net_alert_template_key_ck'),
            models.CheckConstraint(condition=models.Q(mode__in=('compact', 'detailed')), name='net_alert_template_mode_ck'),
        ]

    def clean(self):
        super().clean()
        if self.key != 'task_summary':
            raise ValidationError({'key': '仅支持任务总结模板。'})
        if self.mode not in self.Mode.values:
            raise ValidationError({'mode': '不支持的总结模板模式。'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class AlertDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', '待发送'
        SENDING = 'sending', '发送中'
        SENT = 'sent', '发送成功'
        RETRY = 'retry', '等待重试'
        FAILED = 'failed', '发送失败'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(AlertEvent, on_delete=models.CASCADE, related_name='deliveries')
    channel = models.ForeignKey(AlertChannel, on_delete=models.PROTECT, related_name='deliveries')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_DELIVERY_ATTEMPTS)],
    )
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.CharField(max_length=32, blank=True)
    response_summary = models.CharField(max_length=1000, blank=True)
    error_summary = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('event', 'channel'),
                name='net_alert_delivery_event_channel_uniq',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(max_attempts__gte=1)
                    & models.Q(max_attempts__lte=MAX_DELIVERY_ATTEMPTS)
                    & models.Q(attempt_count__lte=models.F('max_attempts'))
                ),
                name='net_alert_delivery_attempt_limit_ck',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=('pending', 'sending', 'sent', 'retry', 'failed'),
                ),
                name='net_alert_delivery_status_ck',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status='sent', delivered_at__isnull=False, next_attempt_at__isnull=True)
                    | models.Q(status='retry', delivered_at__isnull=True, next_attempt_at__isnull=False,
                               attempt_count__lt=models.F('max_attempts'))
                    | models.Q(status='sending', delivered_at__isnull=True,
                               next_attempt_at__isnull=True,
                               lease_expires_at__isnull=False,
                               lease_token__gt='')
                    | models.Q(status__in=('pending', 'failed'),
                               delivered_at__isnull=True, next_attempt_at__isnull=True,
                               lease_expires_at__isnull=True, lease_token='')
                ),
                name='net_alert_delivery_time_ck',
            ),
        ]
        indexes = [
            models.Index(fields=('status', 'next_attempt_at'), name='net_alert_delivery_retry_idx'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.attempt_count > self.max_attempts:
            errors['attempt_count'] = '发送尝试次数不能超过最大次数。'
        if self.status == self.Status.SENT and self.delivered_at is None:
            errors['delivered_at'] = '发送成功必须记录送达时间。'
        if self.status == self.Status.RETRY and self.next_attempt_at is None:
            errors['next_attempt_at'] = '等待重试必须记录下次发送时间。'
        if self.status == self.Status.SENDING and (
            self.lease_expires_at is None or not self.lease_token
        ):
            errors['lease_expires_at'] = '发送中的告警必须持有发送租约。'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.channel.name}：{self.get_status_display()}'


class AlertTestSend(models.Model):
    """A durable, secret-safe audit of an operator-requested channel test."""

    class Status(models.TextChoices):
        SENT = 'sent', '发送成功'
        FAILED = 'failed', '发送失败'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(
        AlertChannel, on_delete=models.CASCADE, related_name='test_send_audits',
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    response_summary = models.CharField(max_length=1000, blank=True)
    error_summary = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=('channel', 'created_at'), name='net_alert_test_channel_idx'),
        ]

    def __str__(self):
        return f'{self.channel.name}：{self.get_status_display()}'


def _scope(instance):
    return tuple(str(getattr(instance, field)) for field in ALERT_SCOPE_FIELDS)


@receiver(m2m_changed, sender=AlertEvent.states.through)
def _guard_event_state_scope(sender, instance, action, reverse, pk_set, using, **kwargs):
    if action != 'pre_add' or not pk_set:
        return
    # Read stored identities: callers may be holding stale or modified objects.
    if reverse:
        state = AlertState.objects.using(using).get(pk=instance.pk)
        events = AlertEvent.objects.using(using).filter(pk__in=pk_set)
        pairs = ((event, state) for event in events)
    else:
        event = AlertEvent.objects.using(using).get(pk=instance.pk)
        states = AlertState.objects.using(using).filter(pk__in=pk_set)
        pairs = ((event, state) for state in states)
    for event, state in pairs:
        event._validate_scope(using)
        if _scope(event) != _scope(state):
            raise ValidationError('事件与状态必须属于同一项目和目标。')
