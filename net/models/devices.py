import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from net.infrastructure.sanitization import public_connection_url


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

    def __str__(self):
        return self.computer_name


class Network_Device(models.Model):
    CONNECTION_TYPE_CHOICES = [
        ('ssh', 'SSH'),
        ('snmp', 'SNMP'),
        ('hybrid', 'SSH + SNMP'),
        ('auto', '自动'),
        ('sangfor_api', '深信服 AC Open API'),
    ]
    SNMP_VERSION_CHOICES = [('v2c', 'SNMPv2c'), ('v3', 'SNMPv3')]
    SNMP_SECURITY_LEVEL_CHOICES = [
        ('noAuthNoPriv', 'noAuthNoPriv'),
        ('authNoPriv', 'authNoPriv'),
        ('authPriv', 'authPriv'),
    ]
    SNMP_AUTH_PROTOCOL_CHOICES = [
        ('md5', 'MD5'),
        ('sha1', 'SHA-1'),
        ('sha224', 'SHA-224'),
        ('sha256', 'SHA-256'),
        ('sha384', 'SHA-384'),
        ('sha512', 'SHA-512'),
    ]
    SNMP_PRIV_PROTOCOL_CHOICES = [
        ('des', 'DES'),
        ('aes128', 'AES-128'),
        ('aes192', 'AES-192'),
        ('aes256', 'AES-256'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device_name = models.CharField(max_length=255, blank=True, null=True)
    ip = models.CharField(max_length=255, unique=True)
    device_type = models.CharField(max_length=255, blank=True, null=True)
    model = models.CharField(max_length=255, blank=True, null=True)
    vendor = models.CharField(max_length=255, blank=True, null=True)
    connection_type = models.CharField(
        max_length=255,
        choices=CONNECTION_TYPE_CHOICES,
        default='auto',
        blank=True,
        null=True,
    )
    port = models.PositiveIntegerField(default=22)
    username = models.CharField(max_length=255, blank=True, null=True)
    password = models.CharField(max_length=255, blank=True, null=True)
    snmp_version = models.CharField(
        max_length=3, choices=SNMP_VERSION_CHOICES, default='v2c'
    )
    snmp_port = models.PositiveIntegerField(
        default=161, validators=[MinValueValidator(1), MaxValueValidator(65535)]
    )
    snmp_community = models.CharField(max_length=255, blank=True, default='')
    snmp_security_level = models.CharField(
        max_length=12,
        choices=SNMP_SECURITY_LEVEL_CHOICES,
        default='noAuthNoPriv',
    )
    snmp_username = models.CharField(max_length=255, blank=True, default='')
    snmp_auth_protocol = models.CharField(
        max_length=6, choices=SNMP_AUTH_PROTOCOL_CHOICES, blank=True, default=''
    )
    snmp_auth_password = models.CharField(max_length=255, blank=True, default='')
    snmp_priv_protocol = models.CharField(
        max_length=6, choices=SNMP_PRIV_PROTOCOL_CHOICES, blank=True, default=''
    )
    snmp_priv_password = models.CharField(max_length=255, blank=True, default='')
    snmp_context_name = models.CharField(max_length=255, blank=True, default='')
    snmp_retries = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(0), MaxValueValidator(5)]
    )
    api_url = models.URLField(max_length=500, blank=True, default='')
    api_shared_secret = models.CharField(max_length=500, blank=True, default='')
    verify_ssl = models.BooleanField(default=True)
    cpu_model = models.CharField(max_length=255, blank=True, null=True)
    memory_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    disk_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    port_count = models.PositiveIntegerField(null=True, blank=True)
    vlan_count = models.PositiveIntegerField(null=True, blank=True)

    @property
    def effective_connection_type(self):
        return self.connection_type if self.connection_type in {'ssh', 'snmp', 'hybrid', 'auto', 'sangfor_api'} else 'ssh'

    @property
    def uses_snmp(self):
        return self.effective_connection_type in {'snmp', 'hybrid', 'auto'}

    def clean(self):
        super().clean()
        if self.effective_connection_type == 'sangfor_api':
            errors = {}
            if not self.api_url:
                errors['api_url'] = '深信服 AC Open API 必须配置 API 地址。'
            if not self.api_shared_secret:
                errors['api_shared_secret'] = '深信服 AC Open API 必须配置共享密钥。'
            if self.api_url:
                from urllib.parse import urlsplit
                try:
                    address = urlsplit(self.api_url)
                    address.port
                    valid = (address.scheme in {'http', 'https'} and address.hostname
                             and not address.username and not address.password
                             and not address.query and not address.fragment)
                except ValueError:
                    valid = False
                if not valid:
                    errors['api_url'] = '请填写不含账号、密码或查询参数的 HTTP/HTTPS API 根地址，共享密钥请单独填写。'
            if errors:
                raise ValidationError(errors)
            return
        if not self.uses_snmp:
            return
        if self.effective_connection_type == 'auto' and not self.snmp_community and not self.snmp_username:
            return
        if self.snmp_version == 'v2c' and not self.snmp_community:
            raise ValidationError({'snmp_community': 'SNMPv2c 必须配置 Community。'})
        if self.snmp_version != 'v3':
            return
        errors = {}
        if not self.snmp_username:
            errors['snmp_username'] = 'SNMPv3 必须配置用户名。'
        if self.snmp_security_level in {'authNoPriv', 'authPriv'}:
            if not self.snmp_auth_protocol:
                errors['snmp_auth_protocol'] = 'SNMPv3 认证必须配置认证协议。'
            if not self.snmp_auth_password:
                errors['snmp_auth_password'] = 'SNMPv3 认证必须配置认证密码。'
        if self.snmp_security_level == 'authPriv':
            if not self.snmp_priv_protocol:
                errors['snmp_priv_protocol'] = 'SNMPv3 加密必须配置隐私协议。'
            if not self.snmp_priv_password:
                errors['snmp_priv_password'] = 'SNMPv3 加密必须配置隐私密码。'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.device_name or "网络设备"} ({self.ip})'


class Server(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, blank=True, null=True)
    ip = models.CharField(max_length=255, unique=True)
    server_type = models.CharField(
        max_length=20, choices=[('linux', 'Linux'), ('windows', 'Windows')], default='linux',
    )
    os = models.CharField(max_length=255, blank=True, null=True)
    port = models.PositiveIntegerField(default=22)
    username = models.CharField(max_length=255, blank=True, null=True)
    password = models.CharField(max_length=255, blank=True, null=True)
    api_url = models.URLField(max_length=500, blank=True, null=True)
    api_token = models.CharField(max_length=500, blank=True, null=True)
    verify_ssl = models.BooleanField(default=True)
    os_version = models.CharField(max_length=255, blank=True, null=True)
    os_build = models.CharField(max_length=255, blank=True, null=True)
    system_installed_at = models.CharField(max_length=255, blank=True, null=True)
    manufacturer = models.CharField(max_length=255, blank=True, null=True)
    model = models.CharField(max_length=255, blank=True, null=True)
    serial_number = models.CharField(max_length=255, blank=True, null=True)
    architecture = models.CharField(max_length=255, blank=True, null=True)
    cpu_model = models.CharField(max_length=255, blank=True, null=True)
    cpu_physical_core_count = models.PositiveIntegerField(null=True, blank=True)
    cpu_logical_processor_count = models.PositiveIntegerField(null=True, blank=True)
    memory_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    disk_total_gb = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    @property
    def public_api_url(self):
        return public_connection_url(self.api_url)

    def __str__(self):
        return f'{self.name or self.get_server_type_display()} ({self.ip})'


class SecurityDevice(models.Model):
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
