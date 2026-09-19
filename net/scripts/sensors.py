"""Pinned official CPU sensor dependencies; no downloads during web requests."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

DIRECTORY = Path(__file__).resolve().parents[2] / 'agents' / 'pc' / 'windows'

def manifest():
    return json.loads((DIRECTORY / 'sensors.json').read_text(encoding='utf-8'))

def verified_bytes(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected.lower():
        raise ValueError('采集依赖校验失败：' + path.name)
    return data

def sensor_bundle():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, digest in sorted(manifest()['files'].items()):
            if Path(name).name != name or not name.endswith('.dll'):
                raise ValueError('硬件依赖文件名无效')
            entry = zipfile.ZipInfo(name, (2026, 2, 14, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, verified_bytes(DIRECTORY / 'sensors' / name, digest))
    return buffer.getvalue()
