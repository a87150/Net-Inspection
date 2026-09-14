"""Durable, credential-safe access-control platform records."""

import json
import uuid

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models


def _credentials_cipher():
    key = getattr(settings, 'DEVICE_BACKUP_ENCRYPTION_KEY', '')
    if not key:
        raise ImproperlyConfigured('DEVICE_BACKUP_ENCRYPTION_KEY is required for access platforms')
    try:
        return Fernet(key)
    except (TypeError, ValueError):
        raise ImproperlyConfigured('DEVICE_BACKUP_ENCRYPTION_KEY must be a valid Fernet key') from None


class AccessRecordSource(models.Model):
    """One access-control management platform, optionally associated with an asset."""

    class AuthenticationMode(models.TextChoices):
        QUERY_TOKEN = 'query_token', '查询参数令牌'
        HEADER_TOKEN = 'header_token', '请求头令牌'

    class Platform(models.TextChoices):
        ZKTECO_V6600 = 'zkteco_v6600', '中控万傲瑞达 V6600'
        ISecure_CENTER = 'isecure_center', '海康 iSecure Center（待接入）'
        DSS = 'dss', '大华 DSS（待接入）'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=32, choices=Platform.choices)
    api_version = models.CharField(max_length=128)
    base_url = models.URLField(max_length=500)
    event_path = models.CharField(max_length=500, blank=True)
    authentication_mode = models.CharField(max_length=32, choices=AuthenticationMode.choices, default=AuthenticationMode.QUERY_TOKEN)
    authentication_name = models.CharField(max_length=128, default='access_token')
    security_device = models.ForeignKey(
        'net.SecurityDevice', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='access_record_sources',
    )
    username = models.CharField(max_length=255, blank=True)
    credential_ciphertext = models.BinaryField(blank=True, editable=False)
    verify_ssl = models.BooleanField(default=True)
    is_enabled = models.BooleanField(default=True)
    cursor_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    default_lookback_minutes = models.PositiveIntegerField(default=60)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=('platform', 'is_enabled'), name='net_access_source_platform_idx')]

    def clean(self):
        super().clean()
        from urllib.parse import urlsplit
        url = urlsplit(self.base_url)
        if url.username or url.password or url.query or url.fragment:
            raise ValidationError({'base_url': 'Use a platform URL without credentials, query parameters or fragments.'})

        if self.event_path and (not self.event_path.startswith('/') or '://' in self.event_path or '?' in self.event_path or '#' in self.event_path):
            raise ValidationError({'event_path': '事件路径必须是以 / 开头、且不含查询参数的相对路径。'})
        if not self.authentication_name.strip() or not self.authentication_name.replace('_', '').replace('-', '').isalnum():
            raise ValidationError({'authentication_name': '认证参数或请求头名称无效。'})
        if not self.api_version.strip():
            raise ValidationError({'api_version': '必须填写平台 API 版本。'})
        if self.default_lookback_minutes > 10080:
            raise ValidationError({'default_lookback_minutes': '默认回溯范围不能超过 7 天。'})

    def set_credentials(self, credentials):
        if not isinstance(credentials, dict):
            raise ValidationError('平台凭据必须是 JSON 对象。')
        cleaned = {str(key): str(value) for key, value in credentials.items() if value not in (None, '')}
        self.credential_ciphertext = _credentials_cipher().encrypt(
            json.dumps(cleaned, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        )

    def get_credentials(self):
        if not self.credential_ciphertext:
            return {}
        try:
            value = json.loads(_credentials_cipher().decrypt(bytes(self.credential_ciphertext)).decode('utf-8'))
        except (InvalidToken, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
            raise ValidationError('平台凭据无法解密，请重新保存。') from None
        if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(item, str)
                                              for key, item in value.items()):
            raise ValidationError('平台凭据格式无效。')
        return value

    def public_data(self):
        return {
            'id': str(self.pk), 'name': self.name, 'platform': self.platform,
            'api_version': self.api_version, 'base_url': self.base_url, 'event_path': self.event_path,
            'authentication_mode': self.authentication_mode, 'authentication_name': self.authentication_name,
            'security_device_id': str(self.security_device_id) if self.security_device_id else '',
            'verify_ssl': self.verify_ssl, 'is_enabled': self.is_enabled,
        }

    def __str__(self):
        return self.name


class AccessRecord(models.Model):
    class Direction(models.TextChoices):
        IN = 'in', '进入'
        OUT = 'out', '离开'
        UNKNOWN = 'unknown', '未知'

    class Result(models.TextChoices):
        PASSED = 'passed', '通过'
        DENIED = 'denied', '拒绝'
        UNKNOWN = 'unknown', '未知'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(AccessRecordSource, on_delete=models.PROTECT, related_name='records')
    source_event_id = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()
    employee_number = models.CharField(max_length=255, blank=True)
    person_name = models.CharField(max_length=255, blank=True)
    door_name = models.CharField(max_length=255, blank=True)
    direction = models.CharField(max_length=16, choices=Direction.choices, default=Direction.UNKNOWN)
    result = models.CharField(max_length=16, choices=Result.choices, default=Result.UNKNOWN)
    card_number = models.CharField(max_length=255, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('source', 'source_event_id'), name='net_access_event_source_uniq')]
        indexes = [
            models.Index(fields=('source', '-occurred_at', '-id'), name='net_access_source_time_idx'),
            models.Index(fields=('-occurred_at', '-id'), name='net_access_time_idx'),
        ]

    def __str__(self):
        return f'{self.source} {self.occurred_at:%Y-%m-%d %H:%M:%S}'
