import uuid

from django.db import models

from net.services.sanitization import public_connection_url


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
    department = models.CharField(max_length=255, blank=True, null=True)
    leader = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=20, default='manual')
    sync_source = models.ForeignKey(
        'net.PeopleSyncSource',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='people',
    )
    platform_user_id = models.CharField(max_length=255, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    hire_date = models.DateField(null=True, blank=True)
    departure_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f'{self.name or "未命名"} ({self.employee_id or "无工号"})'


class Domain_Account(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    object_guid = models.UUIDField(null=True, blank=True, unique=True)
    distinguished_name = models.CharField(
        max_length=1024,
        null=True,
        blank=True,
        db_index=True,
    )
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
    distinguished_name = models.CharField(
        max_length=1024,
        null=True,
        blank=True,
        db_index=True,
    )
    computer_name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    ou = models.CharField(max_length=255, blank=True, null=True)
    os = models.CharField(max_length=255, blank=True, null=True)
    last_login_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.computer_name


class Computer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    computer_name = models.CharField(max_length=255, unique=True)
    os = models.CharField(max_length=255, blank=True, null=True)
    user_name = models.CharField(max_length=255, blank=True, null=True)
    login_account = models.CharField(max_length=255, blank=True, null=True)
    ip_addresses = models.CharField(max_length=1024, blank=True, null=True)
    mac_addresses = models.CharField(max_length=1024, blank=True, null=True)
    os_version = models.CharField(max_length=255, blank=True, null=True)
    os_build = models.CharField(max_length=255, blank=True, null=True)
    system_installed_at = models.CharField(max_length=255, blank=True, null=True)
    last_report_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    manufacturer = models.CharField(max_length=255, blank=True, null=True)
    model = models.CharField(max_length=255, blank=True, null=True)
    serial_number = models.CharField(max_length=255, blank=True, null=True)
    architecture = models.CharField(max_length=255, blank=True, null=True)
    cpu_model = models.CharField(max_length=255, blank=True, null=True)
    cpu_physical_core_count = models.PositiveIntegerField(null=True, blank=True)
    cpu_logical_processor_count = models.PositiveIntegerField(null=True, blank=True)
    memory_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    disk_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    disk_summary = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.computer_name


class Network_Device(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device_name = models.CharField(max_length=255, blank=True, null=True)
    ip = models.CharField(max_length=255, unique=True)
    device_type = models.CharField(max_length=255, blank=True, null=True)
    model = models.CharField(max_length=255, blank=True, null=True)
    vendor = models.CharField(max_length=255, blank=True, null=True)
    connection_type = models.CharField(max_length=255, blank=True, null=True)
    port = models.PositiveIntegerField(default=22)
    username = models.CharField(max_length=255, blank=True, null=True)
    password = models.CharField(max_length=255, blank=True, null=True)
    cpu_model = models.CharField(max_length=255, blank=True, null=True)
    memory_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    disk_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    port_count = models.PositiveIntegerField(null=True, blank=True)
    active_port_count = models.PositiveIntegerField(null=True, blank=True)
    vlan_count = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return f'{self.device_name or "网络设备"} ({self.ip})'


class Server(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, blank=True, null=True)
    ip = models.CharField(max_length=255, unique=True)
    server_type = models.CharField(
        max_length=20,
        choices=[('linux', 'Linux'), ('windows', 'Windows')],
        default='linux',
    )
    os = models.CharField(max_length=255, blank=True, null=True)
    port = models.PositiveIntegerField(default=22)
    username = models.CharField(max_length=255, blank=True, null=True)
    password = models.CharField(max_length=255, blank=True, null=True)
    api_url = models.URLField(max_length=500, blank=True, null=True)
    api_token = models.CharField(max_length=500, blank=True, null=True)
    verify_ssl = models.BooleanField(default=True)
    cpu_model = models.CharField(max_length=255, blank=True, null=True)
    memory_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    disk_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    @property
    def public_api_url(self):
        return public_connection_url(self.api_url)

    def __str__(self):
        return f'{self.name or self.get_server_type_display()} ({self.ip})'


class Monitor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device_name = models.CharField(max_length=255, blank=True, null=True)
    ip = models.CharField(max_length=255, unique=True)
    device_type = models.CharField(max_length=100, blank=True, null=True)
    model = models.CharField(max_length=255, blank=True, null=True)
    vendor = models.CharField(max_length=255, blank=True, null=True)
    api_url = models.URLField(max_length=500, blank=True, null=True)
    api_username = models.CharField(max_length=255, blank=True, null=True)
    api_password = models.CharField(max_length=255, blank=True, null=True)
    api_token = models.CharField(max_length=500, blank=True, null=True)
    verify_ssl = models.BooleanField(default=True)
    cpu_model = models.CharField(max_length=255, blank=True, null=True)
    memory_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    disk_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    @property
    def public_api_url(self):
        return public_connection_url(self.api_url)

    def __str__(self):
        return f'{self.device_name or "安防设备"} ({self.ip})'


class Domain_Controller_Config(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    name = models.CharField(max_length=100, default='主域控')
    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(default=389)
    use_ssl = models.BooleanField(default=False)
    base_dn = models.CharField(max_length=500, blank=True, help_text='例如：DC=example,DC=com')
    bind_username = models.CharField(max_length=255, blank=True)
    bind_password = models.CharField(max_length=255, blank=True)
    user_filter = models.CharField(
        max_length=500,
        default='(&(objectCategory=person)(objectClass=user))',
    )
    computer_filter = models.CharField(max_length=500, default='(objectCategory=computer)')
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
