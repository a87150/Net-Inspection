"""Generate daily collectors from explicitly public terminal settings."""
import base64
from dataclasses import dataclass
import json
from pathlib import Path, PureWindowsPath

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
    """Worker origin/credentials never participate in terminal publishing."""
    for instance, label in ((profile, 'ComputerAnalysisProfile'), (source, 'PCLogSourceConfig')):
        state = getattr(instance, '_state', None)
        if state is None or state.adding or getattr(instance, 'pk', None) is None:
            raise ValueError(f'{label} must be saved to the database before generating a script.')
    if platform not in PLATFORM_TEMPLATES:
        raise ValueError('Unsupported platform.')
    destination = getattr(source, f'terminal_{platform}_path', '')
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError(f'Configure terminal_{platform}_path before downloading.')
    destination = destination.strip()
    if any(ord(c) < 32 for c in destination) or '://' in destination:
        raise ValueError('Terminal destination must be an absolute shared-folder path.')
    valid = PureWindowsPath(destination).is_absolute() if platform == 'windows' else (
        destination.startswith('/Volumes/') and '..' not in destination.split('/'))
    if not valid:
        raise ValueError('Terminal destination must be an absolute shared-folder path (macOS: /Volumes/...).')
    config = {'destination': destination}
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
    return GeneratedScript(filename, template.replace('__PC_CONFIG_BASE64__', encoded),
                           encoding='utf-8-sig' if platform == 'windows' else 'utf-8')


def generate_pc_download(profile, source, platform):
    """Windows includes the verified sensor library; macOS remains a shell script."""
    script = generate_pc_script(profile, source, platform)
    if platform != 'windows':
        return script.filename, script.content_type, script.as_bytes()
    import hashlib
    import io
    import zipfile
    dependencies = Path(__file__).resolve().parents[2] / 'agents' / 'pc' / 'windows'
    library = (dependencies / 'OpenHardwareMonitorLib.dll').read_bytes()
    if hashlib.sha256(library).hexdigest() != 'ef02b0991aac678052bb79dfdfd5bfa0b42b1f34b209e35819ba606909655f58':
        raise ValueError('硬件库校验失败，请恢复官方 OpenHardwareMonitor 0.9.6 依赖。')
    from net.scripts.executable import package_collector, prebuilt_host
    executable = package_collector(prebuilt_host(), script.as_bytes(), library)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('PCCollector.exe', executable)
        archive.writestr('Install-PCCollector.ps1', (TEMPLATE_DIRECTORY / 'Install-PCCollector.ps1').read_bytes())
        for name in ('License.html', 'README.txt'):
            archive.writestr(name, (dependencies / name).read_bytes())
    return 'PC-Windows-Collector.zip', 'application/zip', buffer.getvalue()
