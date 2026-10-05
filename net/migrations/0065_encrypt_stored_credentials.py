"""Encrypt the credential columns that used to be stored as plain text.

Run after 0064, which has already swapped the model fields for the encrypted ones.
That ordering is why this reads through a raw cursor: going through the ORM would
apply from_db_value, try to decrypt a plain-text value, and fail on the very rows
this migration exists to fix.

The Fernet call is inlined rather than imported from the application so the migration
keeps producing the same ciphertext even if the helper is later changed.
"""
import json

from django.db import migrations

from cryptography.fernet import Fernet


# (app, model, column) for every field 0064 turned into an encrypted column.
CHAR_COLUMNS = (
    ('net', 'Network_Device', 'password'),
    ('net', 'Network_Device', 'snmp_community'),
    ('net', 'Network_Device', 'snmp_auth_password'),
    ('net', 'Network_Device', 'snmp_priv_password'),
    ('net', 'Network_Device', 'api_shared_secret'),
    ('net', 'Server', 'password'),
    ('net', 'Server', 'api_token'),
    ('net', 'WeakCurrentDevice', 'api_password'),
    ('net', 'WeakCurrentDevice', 'api_token'),
    ('net', 'Domain_Controller_Config', 'bind_password'),
)

# JSON documents whose string leaves are encrypted, matching EncryptedJSONField.
JSON_COLUMNS = (
    ('net', 'AlertChannel', 'settings'),
    ('net', 'PeopleSyncSource', 'credentials'),
)


def _cipher():
    from django.conf import settings

    key = getattr(settings, 'DEVICE_BACKUP_ENCRYPTION_KEY', '')
    if not key:
        raise RuntimeError(
            'DEVICE_BACKUP_ENCRYPTION_KEY must be set before encrypting stored credentials')
    return Fernet(key.encode('ascii'))


def _already_ciphertext(value):
    return isinstance(value, str) and value.startswith('gAAAA')


def _leaves(value, function):
    if isinstance(value, str):
        return function(value)
    if isinstance(value, dict):
        return dict((key, _leaves(item, function)) for key, item in value.items())
    if isinstance(value, list):
        return [_leaves(item, function) for item in value]
    return value


def _reencrypt(cipher):
    return lambda text: cipher.encrypt(text.encode('utf-8')).decode('ascii')


def _reveal(cipher):
    return lambda text: cipher.decrypt(text.encode('ascii')).decode('utf-8')


def _json_transform(value, cipher, function):
    return json.dumps(_leaves(json.loads(value), function), ensure_ascii=False)


def _walk(apps, schema_editor, app, model, column, transform, skip=lambda value: False):
    meta = apps.get_model(app, model)._meta
    table = schema_editor.quote_name(meta.db_table)
    quoted = schema_editor.quote_name(column)
    identifier = schema_editor.quote_name(meta.pk.column)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SELECT %s, %s FROM %s' % (identifier, quoted, table))
        for primary_key, value in cursor.fetchall():
            if value is None or value == '' or skip(value):
                continue
            cursor.execute(
                'UPDATE %s SET %s = %%s WHERE %s = %%s' % (table, quoted, identifier),
                [transform(value), primary_key])


def encrypt_all(apps, schema_editor):
    cipher = _cipher()
    for app, model, column in CHAR_COLUMNS:
        _walk(apps, schema_editor, app, model, column, _reencrypt(cipher), skip=_already_ciphertext)
    for app, model, column in JSON_COLUMNS:
        skip = lambda value: '"gAAAA' in value
        _walk(apps, schema_editor, app, model, column,
              lambda value: _json_transform(value, cipher, _reencrypt(cipher)), skip=skip)


def decrypt_all(apps, schema_editor):
    cipher = _cipher()
    for app, model, column in CHAR_COLUMNS:
        _walk(apps, schema_editor, app, model, column, _reveal(cipher))
    for app, model, column in JSON_COLUMNS:
        _walk(apps, schema_editor, app, model, column,
              lambda value: _json_transform(value, cipher, _reveal(cipher)))


class Migration(migrations.Migration):

    dependencies = [('net', '0064_alter_alertchannel_settings_and_more')]

    operations = [migrations.RunPython(encrypt_all, decrypt_all)]