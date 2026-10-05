"""Fernet encryption for credential columns.

These values used to be stored as plain text, so a single database read -- a copied
backup, an over-broad grant, one SQL injection -- handed over every SSH password,
SNMPv3 key, domain bind password and SMTP password in the estate. Five other places
in this project already store secrets through Fernet; this makes the rest match.

The key is DEVICE_BACKUP_ENCRYPTION_KEY, already kept in the environment rather than
in the database and already required by this project. Sharing it means one key to
back up, and the cost is that rotating it also invalidates configuration backups and
collection-profile credentials.

There is deliberately no silent plaintext fallback. A value that fails to decrypt is
an error, because the alternative is handing back ciphertext where a password belongs
and failing later, somewhere unrelated, as an authentication error.
"""
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet():
    key = getattr(settings, 'DEVICE_BACKUP_ENCRYPTION_KEY', '')
    if not key:
        raise ImproperlyConfigured(
            'DEVICE_BACKUP_ENCRYPTION_KEY is required to read or write stored credentials.')
    try:
        return Fernet(key.encode('ascii') if isinstance(key, str) else key)
    except (ValueError, TypeError):
        raise ImproperlyConfigured(
            'DEVICE_BACKUP_ENCRYPTION_KEY must be a valid Fernet key.') from None


def looks_encrypted(value):
    """Fernet tokens start with a version byte 0x80, base64url of it is "gA".

    Used only by the data migration to tell a migrated row from a fresh one.
    """
    return isinstance(value, str) and value.startswith('gAAAA')


def encrypt(value):
    if value is None or value == '':
        return value
    if looks_encrypted(value):
        return value
    return _fernet().encrypt(str(value).encode('utf-8')).decode('ascii')


def decrypt(value):
    if value is None or value == '':
        return value
    try:
        return _fernet().decrypt(str(value).encode('ascii')).decode('utf-8')
    except InvalidToken:
        raise ValueError(
            'stored credential could not be decrypted with the current key') from None


class EncryptedCharField(models.CharField):
    """A CharField whose column holds Fernet ciphertext.

    Reads decrypt through from_db_value and writes encrypt through get_prep_value,
    so callers never see the difference. A filter on the column still matches, because
    get_prep_value encrypts the lookup value the same way.
    """

    def __init__(self, *args, **kwargs):
        # Ciphertext is roughly 1.4x the plaintext plus a token header; the previous
        # 255/500 character limits cannot hold it.
        kwargs['max_length'] = 1024
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        return decrypt(value)

    def get_prep_value(self, value):
        return encrypt(super().get_prep_value(value))


class EncryptedJSONField(models.JSONField):
    """A JSONField whose string leaves are stored encrypted.

    Only the leaves are encrypted, not the whole document. MariaDB gives a JSON column
    an implicit json_valid() CHECK constraint named after the column, so a bare
    ciphertext string is rejected as not a JSON document. Encrypting per leaf keeps
    the document shape, which is also what the calling code expects -- settings['password']
    stays a string rather than becoming a nested object.

    Non-string leaves (numbers, booleans, null) are left alone; they are not secrets and
    encrypting them would break the settings documents.
    """

    def _transform(self, value, function):
        if isinstance(value, str):
            return function(value)
        if isinstance(value, dict):
            return {key: self._transform(item, function) for key, item in value.items()}
        if isinstance(value, list):
            return [self._transform(item, function) for item in value]
        return value

    def from_db_value(self, value, expression, connection):
        if value is None or value == '':
            return value
        if isinstance(value, (str, bytes)):
            value = json.loads(value, cls=self.decoder)
        return self._transform(value, decrypt)

    def get_prep_value(self, value):
        if value is None or value == '':
            return value
        # Return the object, not a JSON string: Django's JSONField serialises it
        # again in get_db_prep_save, and pre-encoding here would double-encode.
        return self._transform(value, encrypt)