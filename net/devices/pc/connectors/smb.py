from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import ntpath
import posixpath
import stat as stat_module
from uuid import uuid4

import smbclient
from smbprotocol.exceptions import SMBException
from spnego.exceptions import SpnegoError

from .base import (
    PCLogConnectionError, PCLogPermissionError, RemoteLogEntry, StableDownloadMixin,
    excluded, normalize_path, select_entries,
)


@contextmanager
def smb_errors():
    try:
        yield
    except PermissionError:
        raise PCLogPermissionError('SMB 操作被拒绝，请检查共享和文件系统权限。') from None
    except (OSError, SMBException, SpnegoError):
        raise PCLogConnectionError('SMB 连接或传输失败，请检查地址、共享名称和账号设置。') from None


class SMBPCLogConnector(StableDownloadMixin):
    def __init__(self, source, password, *, client=None):
        self.source = source
        self.client = client if client is not None else smbclient
        if any(c in source.host + source.share_name for c in '/\\\r\n\x00'):
            raise ValueError('SMB 主机和共享名称不能包含路径。')
        if not source.host or not source.share_name:
            raise ValueError('SMB 主机和共享名称不能为空。')
        root = normalize_path(source.remote_root_directory or '')
        self.share_root = '\\\\' + source.host + '\\' + source.share_name
        self.root_relative = root
        self.root = ntpath.join(self.share_root, root.replace('/', '\\'))
        self.cache = {}
        self.password = password
        self.connected = False

    @property
    def options(self):
        return {'connection_cache': self.cache, 'port': self.source.port, 'connection_timeout': 30}

    def _connect(self):
        if self.connected:
            return
        username = self.source.username
        if self.source.domain and '\\' not in username and '@' not in username:
            username = self.source.domain + '\\' + username
        try:
            with smb_errors():
                self.client.register_session(
                    self.source.host, username=username, password=self.password, **self.options)
                self.connected = True
        except Exception:
            self.close()
            raise

    def _path(self, path):
        return ntpath.join(self.root, normalize_path(path).replace('/', '\\'))

    def _validate_directories(self, path, *, include_leaf=False, allow_missing=False):
        relative = normalize_path(posixpath.join(self.root_relative, normalize_path(path)))
        parts = relative.split('/') if relative else []
        if not include_leaf and parts:
            parts = parts[:-1]
        current = self.share_root
        for component in [None, *parts]:
            if component is not None:
                current = ntpath.join(current, component)
            try:
                metadata = self.client.stat(current, follow_symlinks=False, **self.options)
            except OSError as exc:
                if allow_missing and exc.errno == errno.ENOENT:
                    return
                raise
            if getattr(metadata, 'st_reparse_tag', 0) or not stat_module.S_ISDIR(metadata.st_mode):
                raise PCLogPermissionError('SMB 日志目录不能包含链接、目录联接或非目录组件。')

    def list_json(self):
        self._connect()
        rows = []
        pending = [normalize_path(self.source.remote_incoming_directory)]
        with smb_errors():
            while pending:
                directory = pending.pop()
                if excluded(self.source, directory):
                    continue
                self._validate_directories(directory, include_leaf=True)
                for item in self.client.scandir(self._path(directory), **self.options):
                    if item.name in ('.', '..'):
                        continue
                    if '/' in item.name or '\\' in item.name:
                        raise PCLogConnectionError('SMB 目录返回了无效文件名。')
                    path = normalize_path(posixpath.join(directory, item.name))
                    if excluded(self.source, path):
                        continue
                    metadata = item.stat(follow_symlinks=False)
                    if getattr(metadata, 'st_reparse_tag', 0) or stat_module.S_ISLNK(metadata.st_mode):
                        continue
                    if stat_module.S_ISDIR(metadata.st_mode) and self.source.recursive:
                        pending.append(path)
                    elif stat_module.S_ISREG(metadata.st_mode) and path.lower().endswith('.json'):
                        rows.append(RemoteLogEntry(
                            path, metadata.st_size, datetime.fromtimestamp(metadata.st_mtime, timezone.utc)))
        return select_entries(self.source, rows)

    def stat(self, path):
        remote = self._path(path)
        self._connect()
        with smb_errors():
            self._validate_directories(path)
            metadata = self.client.stat(remote, follow_symlinks=False, **self.options)
            if getattr(metadata, 'st_reparse_tag', 0) or not stat_module.S_ISREG(metadata.st_mode):
                raise PCLogPermissionError('日志来源只允许普通文件，不允许链接或目录。')
            return RemoteLogEntry(
                normalize_path(path), metadata.st_size, datetime.fromtimestamp(metadata.st_mtime, timezone.utc))

    def _read(self, path, callback):
        self._connect()
        with smb_errors():
            self._validate_directories(path)
            with self.client.open_file(self._path(path), 'rb', share_access='r', **self.options) as stream:
                while chunk := stream.read(65536):
                    callback(chunk)

    def mkdirs(self, path):
        remote = self._path(path)
        self._connect()
        with smb_errors():
            self._validate_directories(path, include_leaf=True, allow_missing=True)
            self.client.makedirs(remote, exist_ok=True, **self.options)

    def move(self, source, destination):
        src, dst = self._path(source), self._path(destination)
        self.mkdirs(posixpath.dirname(normalize_path(destination)))
        with smb_errors():
            self._validate_directories(source)
            self._validate_directories(destination)
            # smbclient.rename refuses to replace existing archive files.
            self.client.rename(src, dst, **self.options)

    def probe(self):
        self._connect()
        token = '.pc-log-probe-' + uuid4().hex
        original = posixpath.join(normalize_path(self.source.remote_incoming_directory), token)
        targets = [original]
        payload = b'pc-log-source-probe'
        try:
            with smb_errors():
                self._validate_directories(self.source.remote_incoming_directory, include_leaf=True)
                list(self.client.scandir(self._path(self.source.remote_incoming_directory), **self.options))
                with self.client.open_file(self._path(original), 'xb', **self.options) as stream:
                    stream.write(payload)
                received = bytearray()
                self._read(original, received.extend)
                if bytes(received) != payload:
                    raise PCLogConnectionError('SMB 测试文件读取校验失败。')
                current = original
                for directory in (self.source.remote_processed_directory, self.source.remote_failed_directory):
                    destination = posixpath.join(normalize_path(directory), token)
                    targets.append(destination)
                    self.move(current, destination)
                    current = destination
                self.client.remove(self._path(current), **self.options)
                targets.clear()
        finally:
            for path in targets:
                try:
                    self.client.remove(self._path(path), **self.options)
                except (OSError, SMBException):
                    pass

    def close(self):
        self.connected = False
        self.client.reset_connection_cache(fail_on_error=False, connection_cache=self.cache)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
