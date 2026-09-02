"""Generate platform-specific scripts for the public inspection upload API."""
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import quote, urlsplit


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
        """Return download bytes while keeping ``content`` string-compatible."""
        return self.content.encode(self.encoding)


def _upload_url(public_base_url: str) -> str:
    if not isinstance(public_base_url, str):
        raise ValueError('public_base_url must be an HTTP(S) URL.')
    parsed = urlsplit(public_base_url.strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('public_base_url must be an HTTP(S) URL.')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('public_base_url must not contain userinfo.')
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError('public_base_url must use a valid port.') from exc
    if not hostname or parsed.netloc.endswith(':') or (port is not None and not 1 <= port <= 65535):
        raise ValueError('public_base_url must use a valid host and port.')
    host = f'[{hostname}]' if ':' in hostname else hostname
    port_suffix = f':{port}' if port is not None else ''
    return f'{parsed.scheme}://{host}{port_suffix}/api/computer_inspection/'


def _profile_settings(profile, upload_url: str) -> dict[str, object]:
    raw_directories = getattr(profile, 'scan_directories', None)
    if not isinstance(raw_directories, list):
        raise ValueError('At least one scan directory is required.')
    scan_directories = [directory.strip() for directory in raw_directories if isinstance(directory, str) and directory.strip()]
    if not scan_directories:
        raise ValueError('At least one scan directory is required.')

    file_time_mode = getattr(profile, 'file_time_mode', 'recent_days')
    settings = {
        'scan_directories': scan_directories,
        'recursive': bool(getattr(profile, 'recursive', False)),
        'file_time_mode': file_time_mode,
        'analysis_items': list(getattr(profile, 'analysis_items', [])),
        'upload_url': f'{upload_url}?profile_id={quote(str(profile.pk), safe="")}',
    }
    if file_time_mode == 'recent_days':
        recent_days = getattr(profile, 'recent_days', None)
        if not isinstance(recent_days, int) or isinstance(recent_days, bool) or recent_days < 1:
            raise ValueError('recent_days must be a positive integer.')
        settings['recent_days'] = recent_days
    elif file_time_mode == 'date_range':
        start_date = getattr(profile, 'range_start_date', None)
        end_date = getattr(profile, 'range_end_date', None)
        if not start_date or not end_date:
            raise ValueError('date_range requires start and end dates.')
        settings['range_start_date'] = start_date.isoformat()
        settings['range_end_date'] = end_date.isoformat()
    else:
        raise ValueError('Unsupported file time mode.')
    return settings


def generate_pc_script(profile, platform: str, public_base_url: str) -> GeneratedScript:
    """Return a standalone upload script without serializing unrelated profile data."""
    profile_state = getattr(profile, '_state', None)
    if profile_state is None or profile_state.adding or getattr(profile, 'pk', None) is None:
        raise ValueError('ComputerAnalysisProfile must be saved to the database before generating a script.')
    if platform not in PLATFORM_TEMPLATES:
        raise ValueError('Unsupported platform.')
    filename, template_name = PLATFORM_TEMPLATES[platform]
    settings_json = json.dumps(
        _profile_settings(profile, _upload_url(public_base_url)),
        ensure_ascii=False,
        separators=(',', ':'),
    )
    template = (TEMPLATE_DIRECTORY / template_name).read_text(encoding='utf-8')
    return GeneratedScript(
        filename=filename,
        content=template.replace('__PROFILE_CONFIG_JSON__', settings_json),
        encoding='utf-8-sig' if platform == 'windows' else 'utf-8',
    )
