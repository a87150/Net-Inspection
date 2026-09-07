from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol
from datetime import datetime
import re

from django.utils import timezone


class PCLogConnectionError(Exception):
    pass


class PCLogPermissionError(PCLogConnectionError):
    pass


class RemoteFileChanged(PCLogConnectionError):
    pass


@dataclass(frozen=True)
class RemoteLogEntry:
    path: str
    size: int
    modified_at: datetime

    def __post_init__(self):
        normalize_path(self.path)
        if self.size < 0 or timezone.is_naive(self.modified_at):
            raise ValueError('远程文件元数据无效。')


class PCLogConnector(Protocol):
    def list_json(self) -> list[RemoteLogEntry]: ...
    def stat(self, path: str) -> RemoteLogEntry: ...
    def download(self, path: str, destination: Path) -> RemoteLogEntry: ...
    def move(self, source: str, destination: str) -> None: ...
    def mkdirs(self, path: str) -> None: ...
    def probe(self) -> None: ...
    def close(self) -> None: ...


def normalize_path(path):
    value = str(path).replace('\\', '/')
    if value.startswith('/') or re.search(r'[:\x00-\x1f\x7f]', value):
        raise ValueError('远程路径必须是根目录内的相对路径。')
    parts = value.split('/')
    if '..' in parts:
        raise ValueError('远程路径不能包含上级目录。')
    return '/'.join(part for part in parts if part not in ('', '.'))


def within(path, directory):
    return not directory or path == directory or path.startswith(directory + '/')


def excluded(source, path):
    return any(within(path, normalize_path(directory))
               for directory in (source.remote_processed_directory, source.remote_failed_directory)
               if directory)


def select_entries(source, rows, *, now=None):
    now = now or timezone.now()
    incoming = normalize_path(source.remote_incoming_directory)
    today = timezone.localdate(now)
    if source.file_time_mode == 'date_range':
        start, end = source.range_start_date, source.range_end_date
    else:
        start, end = today - timedelta(days=(source.recent_days or 7) - 1), today
    result = []
    for row in rows:
        path = normalize_path(row.path)
        if not within(path, incoming) or excluded(source, path) or not path.lower().endswith('.json'):
            continue
        relative = path[len(incoming):].lstrip('/')
        if not source.recursive and '/' in relative:
            continue
        day = timezone.localdate(row.modified_at)
        if start <= day <= end:
            result.append(row)
    return sorted(result, key=lambda row: row.path)


class StableDownloadMixin:
    max_file_bytes = 128 * 1024 * 1024

    def download(self, path, destination):
        before = self.stat(path)
        if before.size > self.max_file_bytes:
            raise PCLogConnectionError('日志文件超过允许的大小。')
        destination = Path(destination)
        created = False
        try:
            # Exclusive creation never overwrites a prior transfer or follows a local symlink.
            with destination.open('xb') as output:
                created = True
                count = 0
                def write(chunk):
                    nonlocal count
                    count += len(chunk)
                    if count > before.size or count > self.max_file_bytes:
                        raise RemoteFileChanged('下载期间日志发生变化，请稍后重试。')
                    output.write(chunk)
                self._read(path, write)
            after = self.stat(path)
            if before != after or count != before.size:
                raise RemoteFileChanged('下载期间日志发生变化，请稍后重试。')
            return after
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
