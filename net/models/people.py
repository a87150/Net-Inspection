import uuid

from django.db import models


class People(models.Model):
    class Source(models.TextChoices):
        MANUAL = 'manual', '手工维护'
        CSV = 'csv', 'CSV 导入'
        FEISHU = 'feishu', '飞书'
        DINGTALK = 'dingtalk', '钉钉'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, blank=True, null=True)
    employee_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    email = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField('手机号', max_length=64, blank=True, default='')
    department = models.CharField(max_length=255, blank=True, null=True)
    leader = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=20, default='manual')
    sync_source = models.ForeignKey(
        'net.PeopleSyncSource', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='people',
    )
    platform_user_id = models.CharField(max_length=255, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    hire_date = models.DateField(null=True, blank=True)
    departure_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f'{self.name or "未命名"} ({self.employee_id or "无工号"})'
