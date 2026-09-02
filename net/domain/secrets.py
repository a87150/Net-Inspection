import hashlib
import json
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from net.models.domain import DomainOperationSecret


PASSWORD_ACTIONS = frozenset({'create_user', 'reset_password'})
DEFAULT_SECRET_LIFETIME = timedelta(minutes=15)


def _secret_fernet():
    key = getattr(settings, 'DOMAIN_OPERATION_ENCRYPTION_KEY', '')
    if not isinstance(key, str) or not key.strip():
        raise ValidationError('密码操作未配置加密密钥。')
    try:
        return Fernet(key.encode('ascii'))
    except (ValueError, UnicodeEncodeError):
        raise ValidationError('密码操作加密密钥无效。') from None


def _validate_password_payload(operation, payload):
    if operation.action not in PASSWORD_ACTIONS:
        raise ValidationError('此操作不能保存密码载荷。')
    if not isinstance(payload, dict) or set(payload) != {'password'}:
        raise ValidationError('密码载荷格式无效。')
    password = payload.get('password')
    if not isinstance(password, str) or not password:
        raise ValidationError('密码载荷格式无效。')


def _purpose_fingerprint(operation):
    purpose = f'{operation.pk}:{operation.object_type}:{operation.action}'
    return hashlib.sha256(purpose.encode('utf-8')).hexdigest()


def store_operation_secret(operation, payload, *, expires_at=None):
    """Encrypt a password payload for exactly one later Worker consumption."""
    _validate_password_payload(operation, payload)
    fernet = _secret_fernet()
    expiry = expires_at or timezone.now() + DEFAULT_SECRET_LIFETIME
    if timezone.is_naive(expiry):
        raise ValidationError('密码载荷过期时间无效。')
    encrypted_payload = fernet.encrypt(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
    )
    with transaction.atomic():
        DomainOperationSecret.objects.update_or_create(
            operation=operation,
            defaults={
                'encrypted_payload': encrypted_payload,
                'purpose_fingerprint': _purpose_fingerprint(operation),
                'expires_at': expiry,
            },
        )


def consume_operation_secret(operation_id):
    """Delete an encrypted password row inside the lock before returning plaintext."""
    error_message = None
    payload = None
    with transaction.atomic():
        secret = (
            DomainOperationSecret.objects.select_for_update()
            .select_related('operation')
            .filter(operation_id=operation_id)
            .first()
        )
        if secret is None:
            error_message = '密码载荷不存在或已被使用。'
        elif secret.expires_at <= timezone.now():
            secret.delete()
            error_message = '密码载荷已过期。'
        elif secret.purpose_fingerprint != _purpose_fingerprint(secret.operation):
            secret.delete()
            error_message = '密码载荷无效。'
        else:
            try:
                plaintext = _secret_fernet().decrypt(bytes(secret.encrypted_payload))
                payload = json.loads(plaintext.decode('utf-8'))
                _validate_password_payload(secret.operation, payload)
            except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
                secret.delete()
                error_message = '密码载荷无法使用。'
            else:
                secret.delete()
    if error_message:
        raise ValidationError(error_message)
    return payload
