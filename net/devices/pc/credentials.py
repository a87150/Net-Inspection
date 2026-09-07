import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from net.infrastructure.sanitization import sanitize
from net.models.pc_sources import PCLogSourceCredential


def _secret_fernet():
    key = getattr(settings, 'PC_LOG_SOURCE_ENCRYPTION_KEY', '')
    if not isinstance(key, str) or not key.strip():
        raise ValidationError('PC 日志来源未配置加密密钥。')
    try:
        return Fernet(key.encode('ascii'))
    except (ValueError, UnicodeEncodeError):
        raise ValidationError('PC 日志来源加密密钥无效。') from None


def _purpose_fingerprint(source):
    purpose = f'pc-log-source:{source.pk}:{source.source_type}:{source.host}'
    return hashlib.sha256(purpose.encode('utf-8')).hexdigest()


def store_pc_source_secret(source, password):
    if not isinstance(password, str) or not password:
        raise ValidationError('PC 日志来源密码不能为空。')
    encrypted_payload = _secret_fernet().encrypt(password.encode('utf-8'))
    with transaction.atomic():
        PCLogSourceCredential.objects.update_or_create(
            source=source,
            defaults={
                'encrypted_payload': encrypted_payload,
                'purpose_fingerprint': _purpose_fingerprint(source),
            },
        )


def load_pc_source_secret(source):
    credential = PCLogSourceCredential.objects.filter(source=source).first()
    if credential is None or credential.purpose_fingerprint != _purpose_fingerprint(source):
        raise ValidationError('PC 日志来源密码不可用。')
    try:
        return _secret_fernet().decrypt(bytes(credential.encrypted_payload)).decode('utf-8')
    except (InvalidToken, UnicodeDecodeError, ValidationError):
        raise ValidationError('PC 日志来源密码不可用。') from None

def sanitize_pc_source_error(source, value):
    """Redact both labeled credentials and the exact saved source password."""
    secrets = ()
    if source is not None:
        try:
            secrets = (load_pc_source_secret(source),)
        except ValidationError:
            pass
    return sanitize(str(value or ''), secrets=secrets)
