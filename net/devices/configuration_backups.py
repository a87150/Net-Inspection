"""Offline raw backup storage. Callers own collection and task lease fencing.

Supported scopes are native active CLI configurations, not firmware,
certificates or separate startup files. No plaintext is persisted here.
"""
import hashlib
import hmac
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import router, transaction
from django.db.models import QuerySet
from django.utils import timezone

from net.data_exchange.adapters import MAX_CONFIG_BYTES, UnsupportedConfiguration
from net.devices.network.configuration import adapt, network_vendor
from net.models import DeviceConfigurationBackup, Network_Device, SecurityDevice


BACKUP_TIMEZONE = ZoneInfo('Asia/Shanghai')


def _identity(asset):
    if isinstance(asset, Network_Device):
        device_type = 'network_device'
    elif isinstance(asset, SecurityDevice):
        device_type = 'monitor'
    else:
        raise UnsupportedConfiguration('Unsupported device configuration backup type')
    if asset._state.adding or asset.pk is None:
        raise ValueError('Configuration backups require a saved device')
    return dict(device_type=device_type, device_id=asset.pk)


def _timestamp(at):
    at = at if at is not None else timezone.now()
    if timezone.is_naive(at):
        at = timezone.make_aware(at, BACKUP_TIMEZONE)
    return at


def _day(at):
    return timezone.localtime(_timestamp(at), BACKUP_TIMEZONE).date()


def _fernet():
    key = getattr(settings, 'DEVICE_BACKUP_ENCRYPTION_KEY', None)
    if not key:
        raise ImproperlyConfigured('DEVICE_BACKUP_ENCRYPTION_KEY is required for configuration backups')
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise ImproperlyConfigured('DEVICE_BACKUP_ENCRYPTION_KEY must be a valid Fernet key') from None


def list_configuration_backups(asset) -> QuerySet:
    """Metadata only, newest first; ciphertext loads only on explicit access."""
    return DeviceConfigurationBackup.objects.using(asset._state.db or 'default').filter(
        **_identity(asset)).defer('ciphertext').order_by('-backup_date', '-captured_at', '-id')


def latest_configuration_backup(asset) -> DeviceConfigurationBackup | None:
    return list_configuration_backups(asset).first()


def backup_due(asset, at=None) -> bool:
    """Advisory daily check; store serializes writers independently."""
    return not list_configuration_backups(asset).filter(backup_date=_day(at)).exists()


def _native_bytes(item):
    if not isinstance(item, dict) or item.get('status') != 'success':
        raise ValueError('A successful configuration capture is required')
    content = item.get('content')
    encoding = item.get('encoding') or 'utf-8'
    try:
        if isinstance(content, bytes):
            raw, text = content, content.decode(encoding, errors='strict')
        elif isinstance(content, str):
            raw, text = content.encode(encoding, errors='strict'), content
        else:
            raise ValueError('Native text configuration is required')
    except (UnicodeError, LookupError, TypeError):
        raise ValueError('Invalid native configuration encoding') from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError('Native configuration exceeds size limit')
    # Validate a decoded copy only: never normalize, redact or prefix raw bytes.
    _, media_type, extension, scope = adapt({**item, 'content': text})
    return raw, media_type, extension, scope, network_vendor(item.get('vendor'))


def store_configuration_backup(asset, item, *, task_target=None,
                               captured_at=None) -> DeviceConfigurationBackup:
    identity = _identity(asset)
    if identity['device_type'] == 'monitor':
        raise UnsupportedConfiguration(
            'Security configuration is a partial Network section, not a complete device backup')
    raw, media_type, extension, scope, vendor = _native_bytes(item)
    captured_at = _timestamp(captured_at)
    day = _day(captured_at)
    database = router.db_for_write(type(asset), instance=asset)
    with transaction.atomic(using=database):
        # Lock the persistent asset even when no backup row exists yet.
        type(asset)._default_manager.using(database).select_for_update().only('pk').get(pk=asset.pk)
        versions = DeviceConfigurationBackup.objects.using(database).filter(**identity)
        existing = versions.filter(backup_date=day).defer('ciphertext').first()
        if existing is not None:
            return existing
        ciphertext = _fernet().encrypt(raw)
        backup = DeviceConfigurationBackup.objects.using(database).create(
            **identity, backup_date=day, captured_at=captured_at,
            filename=f'{identity["device_type"]}-{asset.pk}-{day.isoformat()}.{extension}',
            media_type=media_type, scope=scope, vendor=vendor,
            sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw),
            ciphertext=ciphertext, task_target=task_target,
        )
        stale = list(versions.order_by('-backup_date', '-captured_at', '-id')
                     .values_list('pk', flat=True)[10:])
        if stale:
            versions.filter(pk__in=stale).delete()
        return backup


def read_configuration_backup(backup) -> bytes:
    cipher = _fernet()
    try:
        raw = cipher.decrypt(bytes(backup.ciphertext))
    except (InvalidToken, TypeError, ValueError):
        raise ValueError('Configuration backup cannot be decrypted') from None
    if (len(raw) != backup.byte_size
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), backup.sha256)):
        raise ValueError('Configuration backup integrity verification failed')
    return raw
