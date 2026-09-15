import hashlib
import json
import re
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from .domain import contains_sensitive_payload


POSITIVE_WORKER_VALIDATORS = [MinValueValidator(1), MaxValueValidator(64)]


def _normalize_target_identities(target_scope_snapshot):
    default_type = str(target_scope_snapshot.get('target_type') or '')
    raw_targets = target_scope_snapshot.get('targets')
    if raw_targets is None:
        raw_targets = target_scope_snapshot.get('target_ids', [])

    identities = set()
    for target in raw_targets:
        if isinstance(target, dict):
            target_type = str(target.get('target_type') or default_type)
            target_id = target.get('target_id', target.get('id'))
        else:
            target_type = default_type
            target_id = target
        identities.add((target_type, str(target_id)))
    return sorted(identities)


def validate_string_list(value):
    if not isinstance(value, list):
        raise ValidationError('必须是字符串列表。')
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValidationError('列表中的每一项都必须是非空字符串。')
    if len(value) != len(set(value)):
        raise ValidationError('列表中不能包含重复项。')


def validate_json_object(value):
    if not isinstance(value, dict):
        raise ValidationError('必须是 JSON 对象。')


def contains_sensitive_snapshot_value(value):
    return contains_sensitive_payload(value)


class AlertPolicyMode(models.TextChoices):
    INHERIT = 'inherit', '继承默认告警策略'
    OVERRIDE = 'override', '使用项目告警策略'


class InspectionProfile(models.Model):
    class DeviceType(models.TextChoices):
        NETWORK_DEVICE = 'network_device', '网络设备'
        SERVER = 'server', '服务器'
        MONITOR = 'monitor', '安防设备'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    device_type = models.CharField(max_length=32, choices=DeviceType.choices)
    is_enabled = models.BooleanField(default=True)
    selected_items = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_string_list],
    )
    target_selector = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
    )
    timeout_seconds = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(1), MaxValueValidator(3600)],
    )
    concurrent_workers = models.PositiveSmallIntegerField(
        default=4,
        validators=POSITIVE_WORKER_VALIDATORS,
    )
    alert_policy_mode = models.CharField(
        max_length=16,
        choices=AlertPolicyMode.choices,
        default=AlertPolicyMode.INHERIT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('device_type', 'name'),
                name='net_profile_device_name_uniq',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        try:
            validate_string_list(self.selected_items)
        except ValidationError as exc:
            errors['selected_items'] = exc.messages
        if not isinstance(self.target_selector, dict):
            errors['target_selector'] = '必须是 JSON 对象。'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class ComputerAnalysisProfile(models.Model):
    matching_mode = models.CharField(max_length=16, default='logs', choices=(
        ('logs', '不按人员匹配（日志为主）'), ('people', '按人员匹配（人员为主）')))
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    is_enabled = models.BooleanField(default=True)
    analysis_items = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_string_list],
    )
    software_policy_path = models.TextField(blank=True)
    software_policy_mode = models.CharField(
        '软件分析模式', max_length=16, default='whitelist', db_default='whitelist',
        choices=(('whitelist', '白名单模式'), ('blacklist', '黑名单模式')),
    )
    minimum_windows_release = models.CharField(max_length=16, blank=True, default='23H2')
    defender_update_max_days = models.PositiveSmallIntegerField(
        default=7, validators=[MinValueValidator(1), MaxValueValidator(3650)],
    )
    defender_scan_max_days = models.PositiveSmallIntegerField(
        default=7, validators=[MinValueValidator(1), MaxValueValidator(3650)],
    )
    patch_max_days = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(1), MaxValueValidator(3650)],
    )
    uptime_max_hours = models.PositiveIntegerField(
        default=168, validators=[MinValueValidator(1), MaxValueValidator(87600)],
    )
    disk_max_percent = models.PositiveSmallIntegerField(
        default=90, validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    cpu_max_percent = models.PositiveSmallIntegerField(
        default=90, validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    cpu_temperature_max_celsius = models.PositiveSmallIntegerField(
        default=85, validators=[MinValueValidator(1), MaxValueValidator(150)],
    )
    site_ip_prefixes = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    memory_max_percent = models.PositiveSmallIntegerField(
        default=90, validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    kms_servers = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_string_list],
    )
    concurrent_workers = models.PositiveSmallIntegerField(
        default=4,
        validators=POSITIVE_WORKER_VALIDATORS,
    )
    alert_policy_mode = models.CharField(
        max_length=16,
        choices=AlertPolicyMode.choices,
        default=AlertPolicyMode.INHERIT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        errors = {}
        for field_name in ('analysis_items', 'kms_servers'):
            try:
                validate_string_list(getattr(self, field_name))
            except ValidationError as exc:
                errors[field_name] = exc.messages
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class Schedule(models.Model):
    domain_config = models.ForeignKey('Domain_Controller_Config', null=True, blank=True,
                                     on_delete=models.PROTECT, related_name='schedules')
    class Kind(models.TextChoices):
        INTERVAL = 'interval', '间隔执行'
        DAILY = 'daily', '每天执行'

    class IntervalUnit(models.TextChoices):
        MINUTES = 'minutes', '分钟'
        HOURS = 'hours', '小时'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection_profile = models.ForeignKey(
        InspectionProfile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='schedules',
    )
    analysis_profile = models.ForeignKey(
        ComputerAnalysisProfile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='schedules',
    )
    people_source = models.ForeignKey(
        'PeopleSyncSource',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='schedules',
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    interval_value = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )
    interval_unit = models.CharField(
        max_length=16,
        choices=IntervalUnit.choices,
        blank=True,
        default='',
    )
    daily_time = models.TimeField(null=True, blank=True)
    is_enabled = models.BooleanField(default=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_enqueued_at = models.DateTimeField(null=True, blank=True)
    last_schedule_attempt_at = models.DateTimeField(null=True, blank=True)
    last_schedule_status = models.CharField(max_length=16, blank=True, default='')
    last_schedule_error = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        inspection_profile__isnull=False,
                        analysis_profile__isnull=True,
                        people_source__isnull=True,
                        domain_config__isnull=True,
                    )
                    | models.Q(
                        inspection_profile__isnull=True,
                        analysis_profile__isnull=False,
                        people_source__isnull=True,
                        domain_config__isnull=True,
                    )
                    | models.Q(
                        inspection_profile__isnull=True,
                        analysis_profile__isnull=True,
                        people_source__isnull=False,
                        domain_config__isnull=True,
                    )
                    | models.Q(domain_config__isnull=False, inspection_profile__isnull=True,
                               analysis_profile__isnull=True, people_source__isnull=True)
                ),
                name='net_schedule_one_profile_ck',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind='interval',
                        interval_value__isnull=False,
                        interval_value__gt=0,
                        interval_unit__in=('minutes', 'hours'),
                        daily_time__isnull=True,
                    )
                    | models.Q(
                        kind='daily',
                        interval_value__isnull=True,
                        interval_unit='',
                        daily_time__isnull=False,
                    )
                ),
                name='net_schedule_kind_fields_ck',
            ),
            models.UniqueConstraint(fields=['inspection_profile'], name='net_schedule_inspection_uniq'),
            models.UniqueConstraint(fields=['analysis_profile'], name='net_schedule_analysis_uniq'),
            models.UniqueConstraint(fields=['people_source'], name='net_schedule_people_source_uniq'),
            models.UniqueConstraint(fields=['domain_config'], name='net_schedule_domain_config_uniq'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        has_inspection = self.inspection_profile_id is not None
        has_analysis = self.analysis_profile_id is not None
        has_people = self.people_source_id is not None
        if sum((has_inspection, has_analysis, has_people, self.domain_config_id is not None)) != 1:
            errors['inspection_profile'] = '必须且只能选择一个配置。'
            errors['analysis_profile'] = '必须且只能选择一个配置。'
            errors['people_source'] = '必须且只能选择一个配置。'

        if self.kind == self.Kind.INTERVAL:
            if self.interval_value is None:
                errors['interval_value'] = '间隔计划必须设置间隔数值。'
            if not self.interval_unit:
                errors['interval_unit'] = '间隔计划必须设置分钟或小时。'
            if self.daily_time is not None:
                errors['daily_time'] = '间隔计划不能设置每日时间。'
        elif self.kind == self.Kind.DAILY:
            if self.daily_time is None:
                errors['daily_time'] = '每日计划必须设置执行时间。'
            if self.interval_value is not None:
                errors['interval_value'] = '每日计划不能设置间隔数值。'
            if self.interval_unit:
                errors['interval_unit'] = '每日计划不能设置间隔单位。'

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        profile = self.inspection_profile or self.analysis_profile or self.people_source or self.domain_config
        return f'{profile} - {self.get_kind_display()}'


class TaskRun(models.Model):
    class TaskType(models.TextChoices):
        DOMAIN_SYNC = 'domain_sync', '域控同步'
        INSPECTION = 'inspection', '设备巡检'
        COMPUTER_ANALYSIS = 'computer_analysis', '计算机日志分析'
        COMPUTER_FETCH = 'computer_fetch', 'PC 日志获取'
        PEOPLE_TEST = 'people_test', '人员目录连接测试'
        PEOPLE_PREVIEW = 'people_preview', '人员目录同步预览'
        PEOPLE_SYNC = 'people_sync', '人员自动同步'
        DOMAIN_OPERATION = 'domain_operation', '域控操作'
        ACCESS_SYNC = 'access_sync', '门禁记录采集'

    class Source(models.TextChoices):
        MANUAL = 'manual', '手动执行'
        SCHEDULED = 'scheduled', '定时执行'

    class Status(models.TextChoices):
        QUEUED = 'queued', '等待'
        RUNNING = 'running', '运行中'
        SUCCESS = 'success', '成功'
        PARTIAL = 'partial', '部分成功'
        FAILED = 'failed', '失败'
        CANCELLED = 'cancelled', '已取消'

    ACTIVE_STATUSES = frozenset({Status.QUEUED, Status.RUNNING})
    PEOPLE_INTERACTIVE_TASK_TYPES = frozenset({
        TaskType.PEOPLE_TEST, TaskType.PEOPLE_PREVIEW,
    })
    PEOPLE_TASK_TYPES = frozenset({
        *PEOPLE_INTERACTIVE_TASK_TYPES, TaskType.PEOPLE_SYNC,
    })
    TERMINAL_STATUSES = frozenset(
        {Status.SUCCESS, Status.PARTIAL, Status.FAILED, Status.CANCELLED}
    )
    AGGREGATED_TERMINAL_STATUSES = frozenset(
        {Status.SUCCESS, Status.PARTIAL, Status.FAILED}
    )
    IMMUTABLE_FIELDS = (
        'task_type',
        'source',
        'inspection_profile_id',
        'analysis_profile_id',
        'people_source_id',
        'schedule_id',
        'profile_snapshot',
        'parameters_snapshot',
        'selected_items_snapshot',
        'target_scope_snapshot',
    )

    @classmethod
    def build_scope_key(cls, *, task_type, profile_id, target_scope_snapshot):
        if task_type == cls.TaskType.COMPUTER_FETCH:
            # All analysis profiles share the same singleton remote inbox.
            profile_id = 'pc-log-source'
        canonical = {
            'profile_id': str(profile_id),
            'targets': _normalize_target_identities(target_scope_snapshot),
            'task_type': task_type,
        }
        encoded = json.dumps(
            canonical,
            ensure_ascii=True,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_type = models.CharField(max_length=32, choices=TaskType.choices)
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.MANUAL,
    )
    inspection_profile = models.ForeignKey(
        InspectionProfile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='task_runs',
    )
    analysis_profile = models.ForeignKey(
        ComputerAnalysisProfile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='task_runs',
    )
    schedule = models.ForeignKey(
        Schedule,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='task_runs',
    )
    people_source = models.ForeignKey(
        'PeopleSyncSource', null=True, blank=True, on_delete=models.PROTECT,
        related_name='task_runs',
    )
    people_applied_at = models.DateTimeField(null=True, blank=True, editable=False)
    alert_summary_processed_at = models.DateTimeField(null=True, blank=True)
    alert_summary_attempted_at = models.DateTimeField(null=True, blank=True)
    alert_summary_error = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    progress = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(100)],
    )
    available_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    worker_id = models.CharField(max_length=255, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    profile_snapshot = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
    )
    parameters_snapshot = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
    )
    selected_items_snapshot = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_string_list],
    )
    target_scope_snapshot = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
    )
    scope_key = models.CharField(max_length=64)
    # SQLite and MySQL both allow multiple NULLs in a unique column. Active
    # runs copy scope_key here; terminal runs clear it. This replaces a
    # backend-specific conditional unique constraint.
    active_scope_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        editable=False,
    )
    total_targets = models.PositiveIntegerField(default=0)
    completed_targets = models.PositiveIntegerField(default=0)
    successful_targets = models.PositiveIntegerField(default=0)
    failed_targets = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(task_type__in=('people_test', 'people_preview'),
                             people_source__isnull=False, inspection_profile__isnull=True,
                             analysis_profile__isnull=True, schedule__isnull=True, source='manual')
                    | models.Q(task_type='people_sync', people_source__isnull=False,
                               inspection_profile__isnull=True, analysis_profile__isnull=True,
                               schedule__isnull=False, source='scheduled',
                               people_applied_at__isnull=True)
                    | models.Q(task_type='inspection', people_source__isnull=True,
                               inspection_profile__isnull=False, analysis_profile__isnull=True,
                               people_applied_at__isnull=True)
                    | models.Q(task_type__in=('computer_analysis', 'computer_fetch'),
                               people_source__isnull=True, inspection_profile__isnull=True,
                               analysis_profile__isnull=False,
                               people_applied_at__isnull=True)
                    | models.Q(task_type='domain_sync', people_source__isnull=True,
                               inspection_profile__isnull=True, analysis_profile__isnull=True,
                               people_applied_at__isnull=True)
                    | models.Q(task_type='domain_operation', people_source__isnull=True,
                               inspection_profile__isnull=True, analysis_profile__isnull=True,
                               schedule__isnull=True, source='manual',
                               people_applied_at__isnull=True)
                    | models.Q(task_type='access_sync', people_source__isnull=True,
                               inspection_profile__isnull=True, analysis_profile__isnull=True,
                               schedule__isnull=True, source='manual',
                               people_applied_at__isnull=True)
                ),
                name='net_task_binding_shape_ck',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=('queued', 'running'),
                        active_scope_key__isnull=False,
                        active_scope_key=models.F('scope_key'),
                    )
                    | models.Q(
                        status__in=(
                            'success',
                            'partial',
                            'failed',
                            'cancelled',
                        ),
                        active_scope_key__isnull=True,
                    )
                ),
                name='net_task_active_scope_ck',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(progress__lte=100)
                    & models.Q(completed_targets__lte=models.F('total_targets'))
                    & models.Q(successful_targets__lte=models.F('completed_targets'))
                    & models.Q(failed_targets__lte=models.F('completed_targets'))
                    & models.Q(
                        completed_targets__gte=(
                            models.F('successful_targets')
                            + models.F('failed_targets')
                        )
                    )
                ),
                name='net_task_progress_counts_ck',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='queued',
                        finished_at__isnull=True,
                        lease_expires_at__isnull=True,
                    )
                    | (
                        models.Q(
                            status='running',
                            started_at__isnull=False,
                            finished_at__isnull=True,
                            lease_expires_at__isnull=False,
                        )
                        & ~models.Q(worker_id='')
                    )
                    | models.Q(
                        status='success',
                        finished_at__isnull=False,
                        lease_expires_at__isnull=True,
                        progress=100,
                        completed_targets=models.F('total_targets'),
                        successful_targets=models.F('total_targets'),
                        failed_targets=0,
                    )
                    | (
                        models.Q(
                            status='partial',
                            finished_at__isnull=False,
                            lease_expires_at__isnull=True,
                            progress=100,
                            completed_targets=models.F('total_targets'),
                            failed_targets__gt=0,
                        )
                        & models.Q(
                            completed_targets=(
                                models.F('successful_targets')
                                + models.F('failed_targets')
                            )
                        )
                    )
                    | models.Q(
                        status='failed',
                        finished_at__isnull=False,
                        lease_expires_at__isnull=True,
                        progress=100,
                        completed_targets=models.F('total_targets'),
                        successful_targets=0,
                        failed_targets=models.F('total_targets'),
                    )
                    | models.Q(
                        status='cancelled',
                        finished_at__isnull=False,
                        lease_expires_at__isnull=True,
                    )
                ),
                name='net_task_state_shape_ck',
            ),
        ]
        indexes = [
            models.Index(
                fields=('status', 'available_at'),
                name='net_task_status_avail_idx',
            ),
            models.Index(fields=('task_type', '-created_at', '-id'), name='net_task_type_created_idx'),
            models.Index(fields=('inspection_profile', '-created_at', '-id'), name='net_task_profile_created_idx'),
            models.Index(
                fields=('lease_expires_at',),
                name='net_task_lease_exp_idx',
            ),
            models.Index(
                fields=('schedule', 'created_at'),
                name='net_task_sched_created_idx',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}

        has_inspection = self.inspection_profile_id is not None
        has_analysis = self.analysis_profile_id is not None
        if self.task_type in self.PEOPLE_INTERACTIVE_TASK_TYPES:
            if not self.people_source_id or has_inspection or has_analysis:
                errors['people_source'] = '人员目录任务必须且只能关联一个目录来源。'
            if self.schedule_id or self.source != self.Source.MANUAL:
                errors['schedule'] = '人员目录操作只支持手动任务。'
            owner_digest = (self.parameters_snapshot.get('owner_session_digest', '')
                            if isinstance(self.parameters_snapshot, dict) else '')
            if not isinstance(owner_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', owner_digest):
                errors['parameters_snapshot'] = '人员目录操作必须绑定发起会话。'
            if self.selected_items_snapshot or self.total_targets != 1:
                errors['total_targets'] = '人员目录操作必须且只能包含当前来源目标。'
        elif self.task_type == self.TaskType.PEOPLE_SYNC:
            if not self.people_source_id or has_inspection or has_analysis:
                errors['people_source'] = '人员自动同步必须且只能关联一个目录配置。'
            if not self.schedule_id or self.source != self.Source.SCHEDULED:
                errors['schedule'] = '人员自动同步必须关联定时计划。'
            if self.selected_items_snapshot or self.total_targets != 1:
                errors['total_targets'] = '人员自动同步必须且只能包含当前平台目标。'
        elif self.people_source_id or self.people_applied_at:
            errors['people_source'] = '其他任务不能关联人员目录来源或确认状态。'
        if self.task_type == self.TaskType.DOMAIN_OPERATION:
            if has_inspection or has_analysis or self.people_source_id:
                errors['task_type'] = '域控操作任务不能关联巡检、分析或人员目录配置。'
            if self.schedule_id or self.source != self.Source.MANUAL:
                errors['schedule'] = '域控操作只支持手动任务。'
            for field_name in (
                'profile_snapshot',
                'parameters_snapshot',
                'target_scope_snapshot',
            ):
                if contains_sensitive_snapshot_value(getattr(self, field_name)):
                    errors[field_name] = '域控操作快照不能包含敏感载荷。'
        if self.task_type == self.TaskType.ACCESS_SYNC:
            if has_inspection or has_analysis or self.people_source_id or self.schedule_id:
                errors['task_type'] = '门禁记录采集不能关联其他任务配置。'
            if self.source != self.Source.MANUAL or self.total_targets != 1 or self.selected_items_snapshot:
                errors['task_type'] = '门禁记录采集必须是单一手动来源任务。'
            for name in ('profile_snapshot', 'parameters_snapshot', 'target_scope_snapshot'):
                if contains_sensitive_snapshot_value(getattr(self, name)):
                    errors[name] = '门禁记录采集快照不能包含敏感载荷。'
        if self.task_type == self.TaskType.DOMAIN_SYNC:
            if has_inspection or has_analysis or self.people_source_id or self.total_targets != 1:
                errors['task_type'] = '域控同步必须且只能包含一个域控目标。'
            if self.selected_items_snapshot:
                errors['selected_items_snapshot'] = '域控同步统一获取账号、计算机和分组。'
            for name in ('profile_snapshot', 'parameters_snapshot', 'target_scope_snapshot'):
                if contains_sensitive_snapshot_value(getattr(self, name)):
                    errors[name] = '域控同步快照不能包含敏感载荷。'
        if self.people_applied_at and (
            self.task_type != self.TaskType.PEOPLE_PREVIEW or self.status != self.Status.SUCCESS
        ):
            errors['people_applied_at'] = '只能确认成功的人员目录预览。'
        if self.task_type == self.TaskType.INSPECTION:
            if not has_inspection:
                errors['inspection_profile'] = '设备巡检任务必须关联巡检配置。'
            if has_analysis:
                errors['analysis_profile'] = '设备巡检任务不能关联分析配置。'
        elif self.task_type in {
            self.TaskType.COMPUTER_ANALYSIS,
            self.TaskType.COMPUTER_FETCH,
        }:
            if not has_analysis:
                errors['analysis_profile'] = '计算机日志任务必须关联分析配置。'
            if has_inspection:
                errors['inspection_profile'] = '计算机日志任务不能关联巡检配置。'

        if self.source == self.Source.SCHEDULED and self.schedule_id is None:
            errors['schedule'] = '定时任务必须关联计划。'
        if self.source == self.Source.MANUAL and self.schedule_id is not None:
            errors['schedule'] = '手动任务不能关联计划。'
        if self.source == self.Source.SCHEDULED and self.schedule_id is not None:
            schedule = self.schedule
            if self.task_type == self.TaskType.DOMAIN_SYNC and not schedule.domain_config_id:
                errors['schedule'] = '域控同步必须使用域控同步计划。'
            if (
                self.task_type == self.TaskType.INSPECTION
                and schedule.inspection_profile_id != self.inspection_profile_id
            ):
                errors['schedule'] = '定时任务必须使用计划关联的巡检配置。'
            if self.task_type in {
                self.TaskType.COMPUTER_ANALYSIS,
                self.TaskType.COMPUTER_FETCH,
            } and schedule.analysis_profile_id != self.analysis_profile_id:
                errors['schedule'] = '定时任务必须使用计划关联的分析配置。'
            if (
                self.task_type == self.TaskType.PEOPLE_SYNC
                and schedule.people_source_id != self.people_source_id
            ):
                errors['schedule'] = '定时任务必须使用计划关联的人员 API 配置。'
        for field_name in (
            'profile_snapshot',
            'parameters_snapshot',
            'target_scope_snapshot',
        ):
            if not isinstance(getattr(self, field_name), dict):
                errors[field_name] = '必须是 JSON 对象。'
        try:
            validate_string_list(self.selected_items_snapshot)
        except ValidationError as exc:
            errors['selected_items_snapshot'] = exc.messages

        if self.status == self.Status.RUNNING:
            if self.started_at is None:
                errors['started_at'] = '运行中任务必须记录开始时间。'
            if not self.worker_id:
                errors['worker_id'] = '运行中任务必须记录 Worker 标识。'
            if self.lease_expires_at is None:
                errors['lease_expires_at'] = '运行中任务必须持有租约。'
            if self.finished_at is not None:
                errors['finished_at'] = '运行中任务不能记录完成时间。'
        if self.status in self.TERMINAL_STATUSES:
            if self.finished_at is None:
                errors['finished_at'] = '终态任务必须记录完成时间。'
            if self.lease_expires_at is not None:
                errors['lease_expires_at'] = '终态任务不能保留活动租约。'
        if self.status in self.AGGREGATED_TERMINAL_STATUSES:
            if self.progress != 100:
                errors['progress'] = '完成结果汇总后进度必须为 100。'
            if self.completed_targets != self.total_targets:
                errors['completed_targets'] = '完成结果必须汇总全部目标。'
            if self.successful_targets + self.failed_targets != self.total_targets:
                errors['successful_targets'] = '成功和失败目标数必须等于目标总数。'
                errors['failed_targets'] = '成功和失败目标数必须等于目标总数。'
            if self.status == self.Status.SUCCESS:
                if self.successful_targets != self.total_targets:
                    errors['successful_targets'] = '成功任务的全部目标都必须成功。'
                if self.failed_targets != 0:
                    errors['failed_targets'] = '成功任务不能包含失败目标。'
            elif self.status == self.Status.PARTIAL:
                if self.failed_targets == 0:
                    errors['failed_targets'] = '部分成功任务必须包含失败目标。'
            elif self.status == self.Status.FAILED:
                if self.successful_targets != 0:
                    errors['successful_targets'] = '失败任务不能包含成功目标。'
                if self.failed_targets != self.total_targets:
                    errors['failed_targets'] = '失败任务的全部目标都必须失败。'
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            errors['finished_at'] = '完成时间不能早于开始时间。'

        if self.completed_targets > self.total_targets:
            errors['completed_targets'] = '完成目标数不能超过目标总数。'
        if self.successful_targets > self.completed_targets:
            errors['successful_targets'] = '成功目标数不能超过完成目标数。'
        if self.failed_targets > self.completed_targets:
            errors['failed_targets'] = '失败目标数不能超过完成目标数。'
        if self.successful_targets + self.failed_targets > self.completed_targets:
            errors.setdefault('successful_targets', '成功与失败目标数之和无效。')
            errors.setdefault('failed_targets', '成功与失败目标数之和无效。')

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.task_type == self.TaskType.DOMAIN_OPERATION:
            sensitive_fields = {
                field_name: '域控操作快照不能包含敏感载荷。'
                for field_name in (
                    'profile_snapshot',
                    'parameters_snapshot',
                    'target_scope_snapshot',
                )
                if contains_sensitive_snapshot_value(getattr(self, field_name))
            }
            if sensitive_fields:
                raise ValidationError(sensitive_fields)
        if not self._state.adding:
            original = type(self).objects.only(*self.IMMUTABLE_FIELDS).get(pk=self.pk)
            changed = {
                field_name.removesuffix('_id'): '任务执行快照创建后不能修改。'
                for field_name in self.IMMUTABLE_FIELDS
                if getattr(self, field_name) != getattr(original, field_name)
            }
            if changed:
                raise ValidationError(changed)
        profile_id = self.inspection_profile_id or self.analysis_profile_id or self.people_source_id
        if self.task_type == self.TaskType.DOMAIN_OPERATION:
            profile_snapshot = self.profile_snapshot if isinstance(self.profile_snapshot, dict) else {}
            profile_id = profile_snapshot.get('action', '')
        self.scope_key = self.build_scope_key(
            task_type=self.task_type,
            profile_id=profile_id,
            target_scope_snapshot=self.target_scope_snapshot,
        )
        self.active_scope_key = (
            self.scope_key if self.status in self.ACTIVE_STATUSES else None
        )
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {
                'scope_key',
                'active_scope_key',
            }
        super().save(*args, **kwargs)


class TaskTargetRun(models.Model):
    fetched_logs = models.ManyToManyField('net.ComputerLogFile', blank=True, related_name='intended_scans')
    class TargetType(models.TextChoices):
        DOMAIN_CONFIG = 'domain_config', '域控目录'
        NETWORK_DEVICE = 'network_device', '网络设备'
        SERVER = 'server', '服务器'
        MONITOR = 'monitor', '安防设备'
        COMPUTER_LOG = 'computer_log', '计算机日志'
        COMPUTER_SOURCE = 'computer_source', 'PC 日志来源'
        PEOPLE_SOURCE = 'people_source', '人员目录来源'
        DOMAIN_ACCOUNT = 'domain_account', '域账号'
        DOMAIN_COMPUTER = 'domain_computer', '域计算机'
        ACCESS_SOURCE = 'access_source', '门禁平台来源'

    IMMUTABLE_FIELDS = (
        'task_id', 'target_type', 'target_id', 'target_snapshot',
        'execution_scope_key',
    )
    RESULT_FIELDS = ('result_type', 'result_id', 'result_snapshot')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        TaskRun,
        on_delete=models.PROTECT,
        related_name='target_runs',
    )
    target_type = models.CharField(max_length=32, choices=TargetType.choices)
    target_id = models.CharField(max_length=64)
    target_snapshot = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
    )
    execution_scope_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=TaskRun.Status.choices,
        default=TaskRun.Status.QUEUED,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result_type = models.CharField(max_length=64, blank=True)
    result_id = models.CharField(max_length=64, blank=True)
    # Recovery linkage is distinct from the immutable original execution result.
    analysis_handoff_task = models.ForeignKey(
        'net.TaskRun', null=True, blank=True, on_delete=models.PROTECT,
        related_name='recovered_fetch_targets', editable=False,
    )
    result_snapshot = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
    )
    error_message = models.TextField(blank=True)
    # Separate from immutable execution results: NULL remains eligible after a
    # crash between the record commit and alert processing, including no-event runs.
    alert_processed_at = models.DateTimeField(null=True, blank=True)
    alert_attempted_at = models.DateTimeField(null=True, blank=True)
    alert_processing_error = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('task', 'target_type', 'target_id'),
                name='net_task_target_uniq',
            ),
            models.UniqueConstraint(
                fields=('execution_scope_key',),
                condition=models.Q(
                    execution_scope_key__isnull=False,
                    status__in=TaskRun.ACTIVE_STATUSES,
                ),
                name='net_target_active_execution_scope_uniq',
            ),
        ]
        indexes = [
            models.Index(
                fields=('task', 'status'),
                name='net_target_task_status_idx',
            ),
            models.Index(
                fields=('alert_processed_at', 'alert_attempted_at', 'finished_at'),
                name='net_target_alert_pending_idx',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if not isinstance(self.target_snapshot, dict):
            errors['target_snapshot'] = '必须是 JSON 对象。'
        if not isinstance(self.result_snapshot, dict):
            errors['result_snapshot'] = '必须是 JSON 对象。'

        if bool(self.result_type) != bool(self.result_id):
            missing_field = 'result_id' if self.result_type else 'result_type'
            errors[missing_field] = '结果类型和结果 ID 必须同时设置。'

        try:
            task = self.task
        except TaskRun.DoesNotExist:
            task = None
        if task is not None:
            if task.task_type in TaskRun.PEOPLE_TASK_TYPES:
                expected_target_type = self.TargetType.PEOPLE_SOURCE
                if self.target_id != str(task.people_source_id):
                    errors['target_id'] = '目标必须是任务绑定的人员目录来源。'
            elif task.task_type == TaskRun.TaskType.COMPUTER_ANALYSIS:
                expected_target_type = self.TargetType.COMPUTER_LOG
            elif task.task_type == TaskRun.TaskType.COMPUTER_FETCH:
                expected_target_type = self.TargetType.COMPUTER_SOURCE
            elif task.task_type == TaskRun.TaskType.INSPECTION:
                profile_snapshot = task.profile_snapshot
                scope_snapshot = task.target_scope_snapshot
                expected_target_type = (
                    profile_snapshot.get('device_type')
                    if isinstance(profile_snapshot, dict) else None
                )
                if not expected_target_type and isinstance(scope_snapshot, dict):
                    expected_target_type = scope_snapshot.get('target_type')
                if not expected_target_type and task._state.adding:
                    profile = task.inspection_profile
                    expected_target_type = (
                        profile.device_type if profile is not None else None
                    )
            elif task.task_type == TaskRun.TaskType.DOMAIN_SYNC:
                expected_target_type = self.TargetType.DOMAIN_CONFIG
                if self.target_id != '1':
                    errors['target_id'] = '域控同步只支持当前域控配置。'
            elif task.task_type == TaskRun.TaskType.DOMAIN_OPERATION:
                expected_target_type = self.target_type
                if expected_target_type not in {
                    self.TargetType.DOMAIN_ACCOUNT,
                    self.TargetType.DOMAIN_COMPUTER,
                }:
                    errors['target_type'] = '域控操作只能包含域账号或域计算机目标。'
                for field_name in ('target_snapshot', 'result_snapshot'):
                    if contains_sensitive_snapshot_value(getattr(self, field_name)):
                        errors[field_name] = '域控操作快照不能包含敏感载荷。'
            elif task.task_type == TaskRun.TaskType.ACCESS_SYNC:
                expected_target_type = self.TargetType.ACCESS_SOURCE
            else:
                expected_target_type = None
            if expected_target_type and self.target_type != expected_target_type:
                errors['target_type'] = '目标类型必须与任务配置的设备类型一致。'

        if self.status == TaskRun.Status.RUNNING and self.started_at is None:
            errors['started_at'] = '运行中的目标必须记录开始时间。'
        if (
            self.status
            in {
                TaskRun.Status.SUCCESS,
                TaskRun.Status.PARTIAL,
                TaskRun.Status.FAILED,
                TaskRun.Status.CANCELLED,
            }
            and self.finished_at is None
        ):
            errors['finished_at'] = '已结束的目标必须记录完成时间。'
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            errors['finished_at'] = '完成时间不能早于开始时间。'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.task_id:
            try:
                is_domain_task = self.task.task_type == TaskRun.TaskType.DOMAIN_OPERATION
            except TaskRun.DoesNotExist:
                is_domain_task = False
            if is_domain_task:
                sensitive_fields = {
                    field_name: '域控操作快照不能包含敏感载荷。'
                    for field_name in ('target_snapshot', 'result_snapshot')
                    if contains_sensitive_snapshot_value(getattr(self, field_name))
                }
                if sensitive_fields:
                    raise ValidationError(sensitive_fields)
        if not self._state.adding:
            original = type(self).objects.only(
                *self.IMMUTABLE_FIELDS,
                'status',
                *self.RESULT_FIELDS,
            ).get(pk=self.pk)
            changed = {
                field_name.removesuffix('_id'): '目标执行快照创建后不能修改。'
                for field_name in self.IMMUTABLE_FIELDS
                if getattr(self, field_name) != getattr(original, field_name)
            }
            if original.status in TaskRun.TERMINAL_STATUSES:
                if self.status not in TaskRun.TERMINAL_STATUSES:
                    changed['status'] = '已结束的目标不能重新进入等待或运行状态。'
                changed.update({
                    field_name: '目标结束后结果引用和结果快照不能修改。'
                    for field_name in self.RESULT_FIELDS
                    if getattr(self, field_name) != getattr(original, field_name)
                })
            if changed:
                raise ValidationError(changed)
        super().save(*args, **kwargs)
