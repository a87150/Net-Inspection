"""Encrypted native device configuration versions (never cleartext)."""
import uuid

from django.db import models


class DeviceConfigurationBackup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device_type = models.CharField(max_length=20, choices=[
        ('network_device', 'Network device'), ('monitor', 'Security device'),
    ])
    device_id = models.UUIDField()
    backup_date = models.DateField()
    captured_at = models.DateTimeField()
    filename = models.CharField(max_length=255)
    media_type = models.CharField(max_length=100)
    scope = models.CharField(max_length=100)
    vendor = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveIntegerField()
    ciphertext = models.BinaryField()
    task_target = models.ForeignKey(
        'net.TaskTargetRun', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='configuration_backups',
    )

    class Meta:
        ordering = ['-backup_date', '-captured_at', '-id']
        constraints = [models.UniqueConstraint(
            fields=['device_type', 'device_id', 'backup_date'],
            name='net_config_backup_device_day',
        )]
