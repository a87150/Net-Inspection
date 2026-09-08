from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class _SanitizedErrorQuerySet(models.QuerySet):
    error_field = ''
    sanitizer_method = ''

    def update(self, **kwargs):
        if self.error_field in kwargs:
            raise ValidationError(
                f'{self.error_field} 必须通过模型实例保存以执行凭据脱敏。'
            )
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        for obj in objs:
            getattr(obj, self.sanitizer_method)()
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if self.error_field in fields:
            raise ValidationError(
                f'{self.error_field} 不支持批量更新，请逐条保存以执行凭据脱敏。'
            )
        return super().bulk_update(objs, fields, **kwargs)


class PCLogSourceQuerySet(_SanitizedErrorQuerySet):
    error_field = 'last_test_error'
    sanitizer_method = '_sanitize_last_test_error'


class ComputerLogTransferQuerySet(_SanitizedErrorQuerySet):
    error_field = 'error_message'
    sanitizer_method = '_sanitize_error_message'


class PCLogSourceConfig(models.Model):
    class SourceType(models.TextChoices):
        SMB = 'smb', 'SMB'
        FTP = 'ftp', 'FTP/FTPS'

    class FileTimeMode(models.TextChoices):
        RECENT_DAYS = 'recent_days', '最近 N 天'
        DATE_RANGE = 'date_range', '指定起止日期'

    objects = PCLogSourceQuerySet.as_manager()

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    source_type = models.CharField(max_length=8, choices=SourceType.choices)
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField()
    username = models.CharField(max_length=255, blank=True)
    smb_auth_mode = models.CharField(max_length=16, default='system', choices=(
        ('system', '使用当前 Windows 运行账号（默认）'),
        ('credentials', '手动指定账号密码'),
    ))
    domain = models.CharField(max_length=255, blank=True)
    share_name = models.CharField(max_length=255, blank=True)
    remote_root_directory = models.TextField(blank=True)
    remote_incoming_directory = models.TextField()
    remote_processed_directory = models.TextField(default='processed')
    remote_failed_directory = models.TextField(default='failed')
    local_staging_directory = models.TextField()
    terminal_windows_path = models.TextField()
    terminal_macos_path = models.TextField(blank=True)
    recursive = models.BooleanField(default=False)
    file_time_mode = models.CharField(
        max_length=20,
        choices=FileTimeMode.choices,
    )
    recent_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(3650)],
    )
    range_start_date = models.DateField(null=True, blank=True)
    range_end_date = models.DateField(null=True, blank=True)
    ftp_passive = models.BooleanField(default=True)
    ftp_use_tls = models.BooleanField(default=False)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_error = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1),
                name='net_pc_log_source_singleton_ck',
            ),
            models.CheckConstraint(
                condition=models.Q(source_type__in=('smb', 'ftp')),
                name='net_pc_log_source_type_ck',
            ),
        ]

    @classmethod
    def load(cls):
        return cls.objects.filter(pk=1).first()

    def clean(self):
        super().clean()
        errors = {}
        if self.pk != 1:
            errors['id'] = 'PC 日志来源只能保存一条单例配置。'
        if self.source_type == self.SourceType.SMB:
            if not self.share_name:
                errors['share_name'] = 'SMB 来源必须设置共享名称。'
        elif self.source_type == self.SourceType.FTP:
            if self.domain:
                errors['domain'] = 'FTP/FTPS 来源不能设置域。'
            if self.share_name:
                errors['share_name'] = 'FTP/FTPS 来源不能设置共享名称。'

        if self.file_time_mode == self.FileTimeMode.RECENT_DAYS:
            if self.recent_days is None:
                errors['recent_days'] = '最近天数模式必须设置天数。'
            if self.range_start_date is not None:
                errors['range_start_date'] = '最近天数模式不能设置开始日期。'
            if self.range_end_date is not None:
                errors['range_end_date'] = '最近天数模式不能设置结束日期。'
        elif self.file_time_mode == self.FileTimeMode.DATE_RANGE:
            if self.recent_days is not None:
                errors['recent_days'] = '指定日期模式不能设置最近天数。'
            if self.range_start_date is None:
                errors['range_start_date'] = '指定日期模式必须设置开始日期。'
            if self.range_end_date is None:
                errors['range_end_date'] = '指定日期模式必须设置结束日期。'
            if (
                self.range_start_date is not None
                and self.range_end_date is not None
                and self.range_start_date > self.range_end_date
            ):
                errors['range_start_date'] = '开始日期不能晚于结束日期。'
                errors['range_end_date'] = '结束日期不能早于开始日期。'
        if errors:
            raise ValidationError(errors)

    def _sanitize_last_test_error(self):
        from net.devices.pc.credentials import sanitize_pc_source_error
        self.last_test_error = sanitize_pc_source_error(self, self.last_test_error)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if self.last_test_error and (
            update_fields is None or 'last_test_error' in update_fields
        ):
            self._sanitize_last_test_error()
        super().save(*args, **kwargs)

    def public_data(self):
        return {
            'id': self.pk,
            'source_type': self.source_type,
            'host': self.host,
            'port': self.port,
            'username': self.username,
            'smb_auth_mode': self.smb_auth_mode,
            'domain': self.domain,
            'share_name': self.share_name,
            'remote_root_directory': self.remote_root_directory,
            'remote_incoming_directory': self.remote_incoming_directory,
            'remote_processed_directory': self.remote_processed_directory,
            'remote_failed_directory': self.remote_failed_directory,
            'local_staging_directory': self.local_staging_directory,
            'terminal_windows_path': self.terminal_windows_path,
            'terminal_macos_path': self.terminal_macos_path,
            'recursive': self.recursive,
            'file_time_mode': self.file_time_mode,
            'recent_days': self.recent_days,
            'range_start_date': self.range_start_date.isoformat() if self.range_start_date else None,
            'range_end_date': self.range_end_date.isoformat() if self.range_end_date else None,
            'ftp_passive': self.ftp_passive,
            'ftp_use_tls': self.ftp_use_tls,
            'last_tested_at': self.last_tested_at.isoformat() if self.last_tested_at else None,
            'last_test_error': self.public_last_test_error(),
        }

    def public_last_test_error(self):
        from net.devices.pc.credentials import sanitize_pc_source_error
        return sanitize_pc_source_error(self, self.last_test_error)

    public_last_test_error.short_description = '最近连接错误（已脱敏）'

    def __str__(self):
        return f'{self.get_source_type_display()} {self.host}:{self.port}'


class PCLogSourceCredential(models.Model):
    source = models.OneToOneField(
        PCLogSourceConfig,
        on_delete=models.CASCADE,
        related_name='credential',
    )
    encrypted_payload = models.BinaryField()
    purpose_fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ComputerLogTransfer(models.Model):
    objects = ComputerLogTransferQuerySet.as_manager()

    class Stage(models.TextChoices):
        DISCOVERED = 'discovered', '已发现'
        DOWNLOADED = 'downloaded', '已下载'
        IMPORTED = 'imported', '已导入'
        ARCHIVE_PENDING = 'archive_pending', '等待归档'
        COMPLETED = 'completed', '已完成'
        FAILED = 'failed', '失败'

    ACTIVE_STAGES = frozenset({
        Stage.DISCOVERED,
        Stage.DOWNLOADED,
        Stage.IMPORTED,
        Stage.ARCHIVE_PENDING,
    })

    source = models.ForeignKey(
        PCLogSourceConfig,
        on_delete=models.PROTECT,
        related_name='transfers',
    )
    task_target = models.ForeignKey(
        'net.TaskTargetRun',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='log_transfers',
    )
    log_file = models.ForeignKey(
        'net.ComputerLogFile',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='transfers',
    )
    remote_source_path = models.CharField(max_length=512)
    source_snapshot = models.JSONField(default=dict, blank=True)
    remote_archive_path = models.TextField(blank=True)
    local_staging_path = models.TextField(blank=True)
    remote_size = models.PositiveBigIntegerField(default=0)
    observed_mtime = models.DateTimeField()
    content_hash = models.CharField(max_length=64, blank=True)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.DISCOVERED)
    active_identity_marker = models.GeneratedField(
        expression=models.Case(
            models.When(
                stage__in=(
                    'discovered',
                    'downloaded',
                    'imported',
                    'archive_pending',
                ),
                then=models.Value(1),
            ),
            default=models.Value(None),
        ),
        output_field=models.PositiveSmallIntegerField(),
        db_persist=True,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=(
                    'source',
                    'remote_source_path',
                    'observed_mtime',
                    'active_identity_marker',
                ),
                name='net_pc_transfer_active_identity_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=('source', 'stage'), name='net_pc_xfer_source_stage_idx'),
        ]

    def _sanitize_error_message(self):
        from net.devices.pc.credentials import sanitize_pc_source_error
        source = self.source if self.source_id else None
        self.error_message = sanitize_pc_source_error(source, self.error_message)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if self.error_message and (
            update_fields is None or 'error_message' in update_fields
        ):
            self._sanitize_error_message()
        super().save(*args, **kwargs)

    def public_error_message(self):
        from net.devices.pc.credentials import sanitize_pc_source_error
        source = self.source if self.source_id else None
        return sanitize_pc_source_error(source, self.error_message)

    public_error_message.short_description = '错误信息（已脱敏）'
