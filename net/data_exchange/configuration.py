"""Offline saved-configuration downloads. No device I/O exists in this service."""
import csv
import json
import re
from dataclasses import dataclass
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile

from net.models import Monitor, Network_Device
from net.infrastructure.sanitization import configuration_secrets, sanitize_configuration
from net.devices.network import configuration as network
from net.devices.security import configuration as security
from net.exports.adapters import NOTICE, UnsupportedConfiguration
from net.data_exchange.table_csv import _spreadsheet_safe


@dataclass(frozen=True)
class ConfigurationResult:
    status: str
    filename: str = ''
    media_type: str = 'text/plain'
    content: bytes = b''
    message: str = ''


MESSAGES = {
    'missing': '没有成功的配置快照；请先选择“设备配置”执行巡检。',
    'unsupported': '不支持此厂商、配置范围或二进制格式。',
    'failed': '配置采集失败或快照不完整；请重新执行配置巡检。',
}


def _safe_name(value):
    # A single portable basename: never accept paths, extensions or controls.
    name = re.sub(r'[^\w-]+', '_', str(value or ''), flags=re.UNICODE).strip('_-')[:80]
    if not name or name.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        name = 'device'
    return name


def _latest_configuration(asset, secrets):
    adapter = network.adapt if isinstance(asset, Network_Device) else security.adapt if isinstance(asset, Monitor) else None
    if adapter is None:
        return ConfigurationResult('unsupported', message=MESSAGES['unsupported'])
    evidence = None
    # Item success, not parent success, defines an exportable snapshot. Do not
    # stop at a newer failed item or an inspection that did not select config.
    for record in asset.inspections.order_by('-created_at', '-pk').only('details', 'raw_output').iterator():
        item = None
        for source in (record.details, record.raw_output):
            if isinstance(source, dict) and 'config_info' in source:
                item = source['config_info']
                break
        if item is None:
            continue
        status = item.get('status') if isinstance(item, dict) else 'failed'
        status = status if status in ('success', 'unsupported', 'failed') else 'failed'
        if status == 'success':
            try:
                content, media, extension, scope = adapter(item)
                content = sanitize_configuration(content, secrets=secrets)
                if extension == 'json':
                    content = json.dumps({'scope': scope, 'full_backup': False, 'notice': NOTICE,
                                          'configuration': content}, ensure_ascii=False, indent=2, allow_nan=False)
                else:
                    content = f'# {NOTICE}\n# scope: {scope}\n' + content
                name = _safe_name(sanitize_configuration(asset.device_name or asset.ip, secrets=secrets))
                return ConfigurationResult('success', f'{name}.{extension}', media,
                                           content.encode('utf-8'), f'{scope} — {NOTICE}')
            except UnsupportedConfiguration:
                status = 'unsupported'
            except (ValueError, TypeError):
                status = 'failed'
        if evidence is None:
            evidence = status
    status = evidence or 'missing'
    return ConfigurationResult(status, message=MESSAGES[status])


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
                base, extension = result.filename.rsplit('.', 1)
                filename = result.filename
                suffix = 2
                while filename.casefold() in used:
                    filename = f'{base}-{suffix}.{extension}'
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
