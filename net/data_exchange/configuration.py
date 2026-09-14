"""Offline saved-configuration downloads. No device I/O exists in this service."""
import csv
import re
from dataclasses import dataclass
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.exceptions import ImproperlyConfigured

from net.models import Network_Device, SecurityDevice
from net.infrastructure.sanitization import configuration_secrets, sanitize_configuration
from net.devices import configuration_backups
from net.data_exchange.table_csv import _spreadsheet_safe


@dataclass(frozen=True)
class ConfigurationResult:
    status: str
    filename: str = ''
    media_type: str = 'text/plain'
    content: bytes = b''
    message: str = ''


MESSAGES = {
    'missing': '没有可下载的原始配置备份；旧脱敏巡检记录不能恢复，请重新执行配置备份。',
    'unsupported': '不支持此厂商、配置范围或二进制格式。',
    'failed': '原始配置备份读取失败；请管理员检查备份密钥和完整性。',
}


def _safe_name(value):
    # A single portable basename: never accept paths, extensions or controls.
    name = re.sub(r'[^\w-]+', '_', str(value or ''), flags=re.UNICODE).strip('_-')[:80]
    if not name or name.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        name = 'device'
    return name


def _latest_configuration(asset, secrets):
    if not isinstance(asset, (Network_Device, SecurityDevice)):
        return ConfigurationResult('unsupported', message=MESSAGES['unsupported'])
    backup = configuration_backups.latest_configuration_backup(asset)
    if backup is None:
        message = ('没有可下载的安防设备完整备份；局部配置和旧脱敏记录不能用于完整恢复。'
                   if isinstance(asset, SecurityDevice) else MESSAGES['missing'])
        return ConfigurationResult('missing', message=message)
    try:
        content = configuration_backups.read_configuration_backup(backup)
    except (ValueError, ImproperlyConfigured):
        return ConfigurationResult('failed', message=MESSAGES['failed'])
    # Only metadata is sanitized. Preserve the decrypted bytes exactly.
    name = sanitize_configuration(backup.filename, secrets=secrets)
    base, dot, extension = name.rpartition('.')
    filename = f'{_safe_name(base)}.{_safe_name(extension)}' if dot else _safe_name(name)
    scope = sanitize_configuration(backup.scope, secrets=secrets)
    return ConfigurationResult('success', filename, backup.media_type, content, scope)


def latest_configuration(asset) -> ConfigurationResult:
    return _latest_configuration(asset, configuration_secrets())


def build_configuration_zip(assets) -> bytes:
    secrets = configuration_secrets()
    archive = BytesIO()
    manifest = StringIO(newline='')
    writer = csv.writer(manifest, lineterminator='\r\n')
    writer.writerow(('asset_id', 'name', 'status', 'filename', 'scope_notice'))
    used = {'manifest.csv'}
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as bundle:
        for asset in assets:
            result = _latest_configuration(asset, secrets)
            filename = ''
            if result.status == 'success':
                base, dot, extension = result.filename.rpartition('.')
                if not dot:
                    base = result.filename
                filename = result.filename
                suffix = 2
                while filename.casefold() in used:
                    filename = f'{base}-{suffix}' + (f'.{extension}' if dot else '')
                    suffix += 1
                used.add(filename.casefold())
                bundle.writestr(filename, result.content)
            # Status/scope/filename are already generated from validated metadata
            # and sanitized basenames. Only the raw display name is untrusted.
            values = (str(asset.pk), sanitize_configuration(asset.device_name or asset.ip, secrets=secrets),
                      result.status, filename, result.message)
            writer.writerow([_spreadsheet_safe(value) for value in values])
        bundle.writestr('manifest.csv', '\ufeff' + manifest.getvalue())
    return archive.getvalue()
