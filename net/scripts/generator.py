"""Generate daily collectors from explicitly public terminal settings."""
import base64
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlsplit

TEMPLATE_DIRECTORY = Path(__file__).with_name('templates')
PLATFORM_TEMPLATES = {
    'windows': ('GetInfo_Upload.ps1', 'GetInfo_Upload.ps1'),
    'macos': ('getinfo_upload_macos.sh', 'getinfo_upload_macos.sh'),
}

@dataclass(frozen=True)
class GeneratedScript:
    filename: str
    content: str
    content_type: str = 'text/plain; charset=utf-8'
    encoding: str = 'utf-8'

    def as_bytes(self) -> bytes:
        return self.content.encode(self.encoding)

def generate_pc_script(profile, source, platform: str) -> GeneratedScript:
    """Embed only the saved terminal API address and its write-only bearer token."""
    for instance, label in ((profile, 'ComputerAnalysisProfile'), (source, 'PCUploadConfig')):
        state = getattr(instance, '_state', None)
        if state is None or state.adding or getattr(instance, 'pk', None) is None:
            raise ValueError(f'{label} must be saved to the database before generating a script.')
    if platform not in PLATFORM_TEMPLATES:
        raise ValueError('Unsupported platform.')
    endpoint_url = getattr(source, 'endpoint_url', '')
    if not isinstance(endpoint_url, str) or not endpoint_url.strip() or any(ord(c) < 32 for c in endpoint_url):
        raise ValueError('Configure endpoint_url before downloading.')
    endpoint_url = endpoint_url.strip()
    parsed = urlsplit(endpoint_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('endpoint_url must be a complete HTTP(S) API address without credentials.')
    try:
        token = source.get_token()
    except Exception as exc:
        raise ValueError('PC upload token is unavailable; save a valid token before downloading.') from exc
    if not isinstance(token, str) or not token or any(char.isspace() for char in token):
        raise ValueError('PC upload token is invalid.')
    config = {'endpoint_url': endpoint_url, 'token': token}
    if platform == 'windows':
        targets = getattr(profile, 'kms_servers', []) or []
        if not isinstance(targets, list) or any(
            not isinstance(target, str) or not target.strip() or
            any(ord(char) < 32 for char in target) for target in targets
        ):
            raise ValueError('KMS targets must be a list of host names.')
        config['kms_servers'] = [target.strip() for target in targets]
    encoded = base64.b64encode(json.dumps(config, ensure_ascii=False).encode('utf-8')).decode('ascii')
    filename, template_name = PLATFORM_TEMPLATES[platform]
    template = (TEMPLATE_DIRECTORY / template_name).read_text(encoding='utf-8-sig')
    if platform == 'windows':
        from net.scripts.sensors import manifest
        hashes = base64.b64encode(json.dumps(manifest()['files']).encode()).decode()
        template = template.replace('__PC_SENSOR_HASHES_BASE64__', hashes)
    return GeneratedScript(filename, template.replace('__PC_CONFIG_BASE64__', encoded),
                           encoding='utf-8-sig' if platform == 'windows' else 'utf-8')


def generate_pc_download(profile, source, platform):
    """Windows includes the verified sensor library; macOS remains a shell script."""
    script = generate_pc_script(profile, source, platform)
    if platform != 'windows':
        return script.filename, script.content_type, script.as_bytes()
    import io
    import zipfile
    dependencies = Path(__file__).resolve().parents[2] / 'agents' / 'pc' / 'windows'
    from net.scripts.sensors import sensor_bundle, manifest, verified_bytes
    from net.scripts.executable import package_collector, prebuilt_host
    executable = package_collector(prebuilt_host(), script.as_bytes(), sensor_bundle())
    driver = verified_bytes(dependencies / 'PawnIO_setup.exe', manifest()['driver']['sha256'])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('PCCollector.exe', executable)
        archive.writestr('Install-PCCollector.ps1', (TEMPLATE_DIRECTORY / 'Install-PCCollector.ps1').read_bytes())
        archive.writestr('PawnIO_setup.exe', driver)
        for license_file in sorted((dependencies / 'licenses').iterdir()):
            if license_file.is_file():
                archive.writestr('licenses/' + license_file.name, license_file.read_bytes())
        for name in ('README.txt',):
            archive.writestr(name, (dependencies / name).read_bytes())
    return 'PC-Windows-Collector.zip', 'application/zip', buffer.getvalue()
