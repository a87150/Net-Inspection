import uuid

from django.db import models

from .devices import Computer, Network_Device, SecurityDevice, Server


class RecordStatus(models.TextChoices):
    SUCCESS = 'success', '成功'
    PARTIAL = 'partial', '部分成功'
    FAILED = 'failed', '失败'


REPORT_FIELDS = ('report_metrics', 'report_problem_types', 'report_severity', 'report_enrichment')


def record_report_values(record):
    """Small report projection, computed at ingestion without querying relations."""
    from net.inspections.record_summary import key_metrics
    from net.inspections.issues import issue_categories
    from net.devices.pc.severity import grade_issue, RANK
    details = record.details if isinstance(record.details, dict) else {}
    if hasattr(record, 'exceptions'):
        findings = record.exceptions or []
    else:
        findings = details.get('issue_findings') or []
        if not findings and (not record.is_reachable or record.status != 'success'):
            findings = [{'analysis_item': 'inspection_collection', 'severity': 'critical'}]
    enrichment = details.get('enrichment') or {}
    return {
        'report_metrics': key_metrics(details),
        'report_problem_types': issue_categories(findings),
        'report_severity': max((grade_issue(issue)['severity'] for issue in findings),
                               key=RANK.get, default=''),
        'report_enrichment': {key: enrichment[key] for key in (
            'personnel_id', 'employee_number', 'personnel_name', 'department',
            'user_ou', 'computer_ou', 'site',
        ) if key in enrichment},
    }


class RecordQuerySet(models.QuerySet):
    """Insert projections with results.

    Runtime result writers use create/save. Maintenance code using update or
    bulk_update for payload/status fields must also refresh REPORT_FIELDS;
    those APIs deliberately bypass model save hooks.
    """
    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.refresh_report()
        if kwargs.get('update_conflicts') and kwargs.get('update_fields'):
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | set(REPORT_FIELDS))
        return super().bulk_create(objs, *args, **kwargs)


class DynamicRecord(models.Model):
    objects = RecordQuerySet.as_manager()
    report_metrics = models.TextField(blank=True, default='无可用指标')
    report_problem_types = models.TextField(blank=True, default='')
    report_severity = models.CharField(max_length=16, blank=True, default='')
    report_enrichment = models.JSONField(default=dict, blank=True)

    def refresh_report(self):
        """Refresh projections before a maintenance bulk_update including REPORT_FIELDS."""
        for name, value in record_report_values(self).items():
            setattr(self, name, value)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is None or set(update_fields) & {'details', 'exceptions', 'status', 'is_reachable'}:
            self.refresh_report()
            if update_fields is not None:
                kwargs['update_fields'] = set(update_fields) | set(REPORT_FIELDS)
        return super().save(*args, **kwargs)

    @property
    def execution_status(self):
        return self.get_status_display()

    @property
    def task_source(self):
        return self.task_target.task.get_source_display() if self.task_target_id else '未关联任务'

    @property
    def error_count(self):
        if hasattr(self, '_report_error_count'):
            return self._report_error_count
        return self.errors.count() or (0 if self.status == 'success' else 1)

    @property
    def key_metrics(self):
        if hasattr(self, '_report_error_count') or 'details' in self.get_deferred_fields():
            return self.report_metrics
        from net.inspections.record_summary import key_metrics
        return key_metrics(self.details)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20,
        choices=RecordStatus.choices,
        default=RecordStatus.SUCCESS,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    summary = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    task_target = models.OneToOneField(
        'net.TaskTargetRun',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='%(class)s_record',
    )

    class Meta:
        abstract = True


PC_LOG_SECTIONS = {'系统信息概览': 'system_info', '网络信息': 'network_info', '计算机硬件资源情况': 'hardware_info', '日志文件元数据': 'log_metadata', 'Windows激活信息': 'windows_activation', 'KMS服务器连通情况': 'kms_connectivity', '当前与域服务器通讯情况': 'domain_communication', '已安装软件列表': 'installed_software', '当前运行进程清单': 'running_processes', 'BitLocker状态': 'bitlocker', 'WindowsDefender状态': 'defender', '系统更新历史': 'system_updates', '已应用策略': 'applied_policies', '浏览器插件情况': 'browser_extensions', '计算机和用户匹配情况': 'identity_match', '事件发现': 'event_findings', '采集诊断': 'collection_diagnostics'}


class ComputerLogFile(models.Model):
    class Platform(models.TextChoices):
        WINDOWS = 'windows', 'Windows'
        MACOS = 'macos', 'macOS'

    computer = models.ForeignKey(
        Computer,
        on_delete=models.PROTECT,
        related_name='log_files',
    )
    collected_date = models.DateField()
    collected_at = models.DateTimeField(db_index=True)
    platform = models.CharField(max_length=20, choices=Platform.choices)
    content_hash = models.CharField(max_length=64, unique=True)
    file_size = models.PositiveBigIntegerField(default=0)
    retained = models.BooleanField(default=True, db_index=True)
    present_sections = models.JSONField(default=list, blank=True)
    extra_fields = models.JSONField(default=dict, blank=True)
    system_info = models.JSONField(null=True, blank=True)
    network_info = models.JSONField(null=True, blank=True)
    hardware_info = models.JSONField(null=True, blank=True)
    log_metadata = models.JSONField(null=True, blank=True)
    windows_activation = models.JSONField(null=True, blank=True)
    kms_connectivity = models.JSONField(null=True, blank=True)
    domain_communication = models.JSONField(null=True, blank=True)
    installed_software = models.JSONField(null=True, blank=True)
    running_processes = models.JSONField(null=True, blank=True)
    bitlocker = models.JSONField(null=True, blank=True)
    defender = models.JSONField(null=True, blank=True)
    system_updates = models.JSONField(null=True, blank=True)
    applied_policies = models.JSONField(null=True, blank=True)
    browser_extensions = models.JSONField(null=True, blank=True)
    identity_match = models.JSONField(null=True, blank=True)
    event_findings = models.JSONField(null=True, blank=True)
    collection_diagnostics = models.JSONField(null=True, blank=True)

    @property
    def payload(self):
        from django.utils import timezone
        value = dict(self.extra_fields or {})
        for key in self.present_sections or []:
            if key in PC_LOG_SECTIONS:
                value[key] = getattr(self, PC_LOG_SECTIONS[key])
        if self.platform:
            value['platform'] = self.platform
        if self.collected_at:
            stamp = self.collected_at
            if timezone.is_aware(stamp):
                stamp = timezone.localtime(stamp)
            value['日志时间'] = stamp.strftime('%Y-%m-%d %H:%M:%S')
        return value

    @property
    def analyses(self):
        return ComputerAnalysis.objects.filter(log_id=self.pk)

    @payload.setter
    def payload(self, value):
        from django.utils import timezone
        from net.devices.pc.checks import parse_local_datetime
        value = value if isinstance(value, dict) else {}
        self.present_sections = [key for key in PC_LOG_SECTIONS if key in value]
        self.extra_fields = {key: item for key, item in value.items()
                             if key not in PC_LOG_SECTIONS and key not in ('platform', '日志时间')}
        for key, field in PC_LOG_SECTIONS.items():
            setattr(self, field, value.get(key))
        if 'platform' in value:
            self.platform = value['platform']
        parsed = parse_local_datetime(value.get('日志时间'))
        if parsed:
            self.collected_at = timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['computer', 'collected_at', 'id'], name='net_pc_latest_log_idx')]


class ComputerAnalysis(DynamicRecord):
    @property
    def problem_types(self):
        if 'exceptions' in self.get_deferred_fields():
            return self.report_problem_types
        from net.inspections.issues import issue_categories
        return issue_categories(self.exceptions or [])

    @property
    def result_level(self):
        if hasattr(self, '_report_level'):
            return self._report_level
        from net.devices.pc.severity import severity_counts
        counts = severity_counts(self.exceptions or [])
        level = next((level for level in ('critical', 'warning', 'info') if counts[level]), None)
        if level:
            return level
        if self.status == 'failed':
            return 'critical'
        has_errors = getattr(self, 'has_errors', None)
        if has_errors is None:
            has_errors = self.errors.exists()
        return 'warning' if has_errors else 'normal'

    @property
    def graded_findings(self):
        from net.devices.pc.severity import grade_issue
        return [grade_issue(issue) for issue in self.exceptions or []]

    computer = models.ForeignKey(
        Computer,
        on_delete=models.CASCADE,
        related_name='analyses',
    )
    log_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True, db_column='log_file_id')

    @property
    def log_file_id(self):
        return self.log_id

    @log_file_id.setter
    def log_file_id(self, value):
        self.log_id = value

    @property
    def log_file(self):
        if not hasattr(self, '_source_log'):
            self._source_log = ComputerLogFile.objects.filter(pk=self.log_id).first() if self.log_id else None
        return self._source_log

    @log_file.setter
    def log_file(self, value):
        self.log_id = value.pk if value is not None else None
        self._source_log = value
        if value is not None:
            self.source_collected_at = value.collected_at
            self.analysis_date = value.collected_date

    analysis_profile = models.ForeignKey('net.ComputerAnalysisProfile', null=True, blank=True, on_delete=models.PROTECT)
    analysis_date = models.DateField(null=True, blank=True, db_index=True)
    source_collected_at = models.DateTimeField(null=True, blank=True)
    retention_managed = models.BooleanField(default=False)
    analysis_items = models.JSONField(default=list, blank=True)
    exceptions = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-created_at']


class InfrastructureRecord(DynamicRecord):
    is_reachable = models.BooleanField(default=True)
    duration_ms = models.PositiveIntegerField(default=0)
    raw_output = models.JSONField(null=True, blank=True)

    class Meta:
        abstract = True


class Network_Device_Inspection(InfrastructureRecord):
    device = models.ForeignKey(
        Network_Device,
        on_delete=models.CASCADE,
        related_name='inspections',
    )

    class Meta:
        ordering = ['-created_at']


class Server_Inspection(InfrastructureRecord):
    server = models.ForeignKey(
        Server,
        on_delete=models.CASCADE,
        related_name='inspections',
    )

    class Meta:
        ordering = ['-created_at']


class Monitor_Inspection(InfrastructureRecord):
    monitor = models.ForeignKey(
        SecurityDevice,
        on_delete=models.CASCADE,
        related_name='inspections',
    )

    class Meta:
        ordering = ['-created_at']


class Error_Computer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    error_type = models.CharField(max_length=255)
    error_message = models.TextField()
    inspection = models.ForeignKey(
        ComputerAnalysis,
        on_delete=models.CASCADE,
        related_name='errors',
    )


class Error_Network_Device(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    error_message = models.JSONField()
    inspection = models.ForeignKey(
        Network_Device_Inspection,
        on_delete=models.CASCADE,
        related_name='errors',
    )


class Error_Server(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    error_message = models.JSONField()
    inspection = models.ForeignKey(
        Server_Inspection,
        on_delete=models.CASCADE,
        related_name='errors',
    )


class Error_Monitor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    error_message = models.JSONField()
    inspection = models.ForeignKey(
        Monitor_Inspection,
        on_delete=models.CASCADE,
        related_name='errors',
    )
