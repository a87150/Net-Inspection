"""One recursive sanitizer for persisted collector output and error surfaces."""

import re
from urllib.parse import quote, quote_plus, unquote, urlsplit, urlunsplit


REDACTED = '[REDACTED]'
_SECRET_KEY = re.compile(r'password|passwd|pwd|secret|token|authorization|credential|api[_-]?key|cookie|signature|^(?:auth|key|sig)$', re.I)
_AUTH = re.compile(r'\b(Bearer|Basic)\s+(?!\[REDACTED\])[^\s,;\"\'<>]+', re.I)
_ASSIGNMENT = re.compile(
    r'''(?ix)(\b(?:[\w%-]*(?:password|passwd|pwd|secret|token|authorization|credential|api[_-]?key|cookie|signature)[\w%-]*|auth|key|sig)["']?\s*[:=]\s*)
    ("[^"\r\n]*"|'[^'\r\n]*'|\[REDACTED\]|[^\s,;&<>]+)'''
)
_URL = re.compile(r'https?://[^\s<>"\']+', re.I)


def public_connection_url(value):
    """Read-only endpoint label, never a usable copy of connection credentials.

    Drop ALL query parameters and fragments: provider-specific token names are
    not enumerable. Do not use this for storage, snapshots or collector input.
    """
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
            return REDACTED
        # Validate the port before publishing an otherwise malformed authority.
        parsed.port
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path, '', ''))
    except (ValueError, TypeError):
        return REDACTED


def sanitize(value, *, secrets=()):
    """Keep data shape, redact sensitive keys and text, including resolved secrets.

    The secret set exists only during execution and is never added to snapshots.
    """
    replacements = set()
    for secret in secrets:
        if isinstance(secret, str) and secret:
            replacements.update((secret, quote(secret, safe=''), quote_plus(secret)))

    def scrub_url(match):
        return public_connection_url(match.group())

    def text(raw):
        raw = _URL.sub(scrub_url, raw)
        raw = _AUTH.sub(lambda match: match.group(1) + ' ' + REDACTED, raw)
        raw = _ASSIGNMENT.sub(lambda match: match.group(1) + REDACTED, raw)
        for secret in sorted(replacements, key=len, reverse=True):
            raw = raw.replace(secret, REDACTED)
        return raw

    def visit(item):
        if isinstance(item, dict):
            return {text(str(key)): REDACTED if _SECRET_KEY.search(unquote(str(key))) else visit(child)
                    for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(child) for child in item]
        if isinstance(item, str):
            return text(item)
        return item

    return visit(value)


def configuration_secrets():
    """Execution/download-only secret registry; never place it in a snapshot.

    Configuration can quote credentials belonging to another application module.
    Read local stores only, once per ZIP/execution. Do not resolve remote secrets.
    """
    from django.conf import settings
    from net.models import (AlertChannel, Domain_Controller_Config, SecurityDevice,
                            Network_Device, PeopleSyncSource, Server)

    values = []
    for model, fields in (
        (Network_Device, (
            'username', 'password', 'snmp_community',
            'snmp_auth_password', 'snmp_priv_password', 'api_shared_secret',
        )),
        (Server, ('username', 'password', 'api_token', 'api_url')),
        (SecurityDevice, ('api_username', 'api_password', 'api_token', 'api_url')),
        (Domain_Controller_Config, ('bind_username', 'bind_password')),
    ):
        for row in model.objects.values_list(*fields).iterator():
            values.extend(row)
    for credentials in PeopleSyncSource.objects.values_list('credentials', flat=True).iterator():
        if isinstance(credentials, dict):
            values.extend(credentials.values())
    for config in AlertChannel.objects.values_list('settings', flat=True).iterator():
        if isinstance(config, dict):
            values.extend(config.get(key) for key in ('username', 'password', 'secret', 'webhook_url'))
    for name in dir(settings):
        if name.isupper() and (_SECRET_KEY.search(name) or name in ('EMAIL_HOST_USER',)):
            value = getattr(settings, name)
            values.extend(value if isinstance(value, (list, tuple)) else [value])
    for config in settings.DATABASES.values():
        values.extend(config.get(key) for key in ('USER', 'PASSWORD'))
    # Include embedded URL credentials/query tokens as well as the complete URL.
    for value in tuple(values):
        if isinstance(value, str) and value.startswith(('http://', 'https://')):
            try:
                from urllib.parse import parse_qsl
                parsed = urlsplit(value)
                values.extend((parsed.username, parsed.password))
                values.extend(content for key, content in parse_qsl(parsed.query) if _SECRET_KEY.search(key))
            except ValueError:
                pass
    return tuple(value for value in values if isinstance(value, str) and value)


def sanitize_configuration(value, *, secrets=()):
    """Preserve native shape/text, removing known app and native CLI credentials."""
    def visit(item):
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, str):
            return item
        item = re.sub(r'(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', REDACTED, item)
        item = re.sub(
            r'(?im)^(\s*(?:(?:username|local-user)\s+\S+\s+.*?\b(?:password|secret)|'
            r'(?:enable\s+)?(?:password|secret)|snmp(?:-server|-agent)\s+community|'
            r'(?:radius|tacacs)[^\r\n]*?\bkey)\s+)[^\r\n]+',
            lambda match: match[1] + REDACTED, item)
        item = re.sub(r'(?i)https?://[^\s<>"\']*(?:webhook|/hook/|/bot/)[^\s<>"\']*', REDACTED, item)
        return item

    return visit(sanitize(value, secrets=secrets))


def sanitize_configuration_items(items, *, secrets=()):
    """Sanitize selected items, reconstructing only validated config metadata.

    This boundary is for collector item maps, NOT arbitrary vendor payloads.
    Payload keys/values and errors still use the ordinary recursive sanitizer;
    canonical routing values are application constants, not credential echoes.
    """
    from net.data_exchange.adapters import UnsupportedConfiguration
    from net.devices.network import configuration as network
    from net.devices.security import configuration as security

    if not isinstance(items, dict):
        # Issue findings are a list, not an item map. Preserve their structure.
        return sanitize_configuration(items, secrets=secrets)
    result = sanitize_configuration({key: value for key, value in items.items()
                                     if key != 'config_info'}, secrets=secrets)
    if 'config_info' not in items:
        return result
    item = items['config_info']
    safe_item = {'status': 'failed', 'message': '配置项结构无效。'}
    if isinstance(item, dict):
        if item.get('status') in ('failed', 'unsupported'):
            safe_item = {'status': item['status'],
                         'message': sanitize_configuration(str(item.get('message', '')), secrets=secrets)}
        elif item.get('status') == 'success' and 'backup_id' in item:
            try:
                from uuid import UUID
                from datetime import datetime
                backup_id = str(UUID(str(item['backup_id'])))
                captured_at = datetime.fromisoformat(item['captured_at']).isoformat()
                checksum = item['sha256']
                size = int(item['byte_size'])
                if not re.fullmatch(r'[0-9a-f]{64}', checksum) or not 0 < size <= 4 * 1024 * 1024:
                    raise ValueError('invalid backup metadata')
                if item['scope'] not in ('running-config', 'current-configuration'):
                    raise ValueError('invalid backup scope')
                safe_item = {'status': 'success', 'backup_id': backup_id,
                             'captured_at': captured_at, 'sha256': checksum,
                             'byte_size': size, 'scope': item['scope'],
                             'message': '原始配置已独立备份，仅管理员可以下载。'}
            except (ValueError, TypeError, KeyError):
                pass
        elif item.get('status') == 'success':
            try:
                vendor = item.get('vendor')
                # Strict canonical values only; never copy arbitrary aliases,
                # URLs or extra metadata from an untrusted body into an envelope.
                if vendor not in ('cisco', 'h3c', 'dahua') or item.get('full_backup') is not False:
                    raise ValueError('invalid configuration envelope')
                content, _, extension, scope = (security.adapt if vendor == 'dahua' else network.adapt)(item)
                safe_item = {'status': 'success', 'vendor': vendor, 'scope': scope,
                             'format': 'json' if extension == 'json' else 'text',
                             'complete': True, 'full_backup': False,
                             'content': sanitize_configuration(content, secrets=secrets)}
            except (ValueError, TypeError, UnsupportedConfiguration):
                pass
    result['config_info'] = safe_item
    return result
