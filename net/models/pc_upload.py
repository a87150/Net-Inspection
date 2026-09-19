"""Terminal write-only API configuration (independent of analysis profiles)."""
import base64
import hashlib
import hmac
from urllib.parse import urlsplit
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

RETENTION_CHOICES = (('daily_latest', '每天仅保留最新版本（往日保留）'), ('all', '保留全部版本'))

class PCUploadConfig(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    endpoint_url = models.URLField(max_length=1000)
    is_enabled = models.BooleanField(default=True)
    log_retention = models.CharField(max_length=20, choices=RETENTION_CHOICES, default='daily_latest')
    encrypted_token = models.BinaryField(blank=True, default=bytes, editable=False)
    token_hash = models.CharField(max_length=64, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='net_pc_upload_singleton_ck')]

    @classmethod
    def load(cls):
        return cls.objects.filter(pk=1).first()

    @staticmethod
    def _cipher():
        key = getattr(settings, 'PC_LOG_SOURCE_ENCRYPTION_KEY', '')
        if not key:
            raise ValidationError('请配置 PC_LOG_SOURCE_ENCRYPTION_KEY 后保存采集设置。')
        try:
            return Fernet(key.encode('ascii'))
        except (ValueError, UnicodeError):
            raise ValidationError('PC 采集凭据加密密钥无效。') from None

    def set_token(self, token):
        if not isinstance(token, str) or len(token) < 32 or len(token) > 512 or any(c.isspace() for c in token):
            raise ValidationError('采集令牌应为 32 至 512 个无空白字符。')
        self.encrypted_token = self._cipher().encrypt(token.encode())
        self.token_hash = hashlib.sha256(token.encode()).hexdigest()

    def get_token(self):
        try:
            return self._cipher().decrypt(bytes(self.encrypted_token)).decode()
        except (InvalidToken, UnicodeError):
            raise ValidationError('采集令牌无法解密，请检查加密密钥或重新生成采集令牌。') from None

    def accepts_token(self, token):
        return bool(self.is_enabled and self.token_hash and isinstance(token, str) and
                    hmac.compare_digest(self.token_hash, hashlib.sha256(token.encode()).hexdigest()))

    def clean(self):
        super().clean()
        parsed = urlsplit(self.endpoint_url)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment):
            raise ValidationError({'endpoint_url': '请填写不含账号、密码或查询参数的 HTTP(S) 上报地址。'})
