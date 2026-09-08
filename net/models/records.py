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


class ComputerLogFile(models.Model):
    class Platform(models.TextChoices):
        WINDOWS = 'windows', 'Windows'
        MACOS = 'macos', 'macOS'

    class SourceProtocol(models.TextChoices):
        SMB = 'smb', 'SMB'
        FTP = 'ftp', 'FTP'
        FTPS = 'ftps', 'FTPS'

    computer = models.ForeignKey(
        Computer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='log_files',
    )
    collected_date = models.DateField(null=True, blank=True)
    platform = models.CharField(max_length=20, choices=Platform.choices, blank=True)
    source_protocol = models.CharField(max_length=8, choices=SourceProtocol.choices, blank=True)
    remote_source_path = models.TextField(blank=True)
    source_path = models.TextField()
    modified_at = models.DateTimeField()
    content_hash = models.CharField(max_length=64, unique=True)
    file_size = models.PositiveBigIntegerField(default=0)
    import_status = models.CharField(max_length=20)
    daily_import_marker = models.GeneratedField(
        expression=models.Case(
            models.When(import_status='imported', then=models.Value(1)),
            default=models.Value(None),
        ),
        output_field=models.PositiveSmallIntegerField(),
        db_persist=True,
    )
    archived_path = models.TextField(blank=True)
    parse_error = models.TextField(blank=True)
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(import_status='imported')
                    | (
                        models.Q(computer__isnull=False)
                        & models.Q(collected_date__isnull=False)
                    )
                ),
                name='net_pc_import_identity_ck',
            ),
            models.UniqueConstraint(
                fields=('computer', 'collected_date', 'daily_import_marker'),
                name='net_pc_imported_log_daily_uniq',
            ),
        ]


class ComputerLogArchive(models.Model):
    """Durable intent for each source copy, including duplicate evidence files."""

    id = models.CharField(primary_key=True, max_length=64)
    log_file = models.ForeignKey(ComputerLogFile, on_delete=models.PROTECT, related_name='archives')
    source_path = models.TextField()
    destination_path = models.TextField()
    identity = models.JSONField()
    status = models.CharField(max_length=20, default='pending', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


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
    log_file = models.ForeignKey(
        ComputerLogFile,
        on_delete=models.CASCADE,
        related_name='analyses',
    )
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
