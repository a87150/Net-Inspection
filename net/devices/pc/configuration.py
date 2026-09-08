"""Public source snapshots shared by queue, forms and transfer recovery."""
from datetime import date
from django.core.exceptions import ValidationError
from net.models import PCLogSourceConfig


SOURCE_FIELDS = (
    'source_type', 'host', 'port', 'username', 'domain', 'share_name', 'smb_auth_mode',
    'remote_root_directory', 'remote_incoming_directory', 'remote_processed_directory',
    'remote_failed_directory', 'local_staging_directory', 'terminal_windows_path',
    'terminal_macos_path', 'recursive', 'file_time_mode', 'recent_days',
    'range_start_date', 'range_end_date', 'ftp_passive', 'ftp_use_tls',
)
ORIGIN_FIELDS = ('source_type', 'host', 'port', 'username', 'domain', 'share_name',
                 'remote_root_directory', 'ftp_use_tls', 'smb_auth_mode')


def source_snapshot(source):
    return {name: value.isoformat() if isinstance(value := getattr(source, name), date) else value
            for name in SOURCE_FIELDS}


def source_from_snapshot(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != set(SOURCE_FIELDS):
        raise ValidationError('日志来源快照无效，请重新创建获取任务。')
    values = dict(snapshot)
    for key in ('range_start_date', 'range_end_date'):
        if isinstance(values[key], str):
            values[key] = date.fromisoformat(values[key])
    source = PCLogSourceConfig(pk=1, **values)
    source.clean()
    return source
