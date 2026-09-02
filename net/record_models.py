import uuid

from django.db import models

from .asset_models import Computer, Monitor, Network_Device, Server


class RecordStatus(models.TextChoices):
    SUCCESS = 'success', '成功'
    PARTIAL = 'partial', '部分成功'
    FAILED = 'failed', '失败'


class DynamicRecord(models.Model):
    @property
    def execution_status(self):
        return self.get_status_display()

    @property
    def task_source(self):
        return self.task_target.task.get_source_display() if self.task_target_id else '未关联任务'

    @property
    def error_count(self):
        return self.errors.count() or (0 if self.status == 'success' else 1)

    @property
    def key_metrics(self):
        from .services.record_summary import key_metrics
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
    upload_task = models.OneToOneField('net.TaskRun', null=True, blank=True, on_delete=models.PROTECT,
                                      related_name='uploaded_log')
    source_path = models.TextField()
    modified_at = models.DateTimeField()
    content_hash = models.CharField(max_length=64, unique=True)
    file_size = models.PositiveBigIntegerField(default=0)
    import_status = models.CharField(max_length=20)
    archived_path = models.TextField(blank=True)
    parse_error = models.TextField(blank=True)
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


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
        Monitor,
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
