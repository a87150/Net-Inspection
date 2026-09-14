from contextlib import contextmanager
from datetime import datetime, timezone
from ftplib import FTP, FTP_TLS, error_perm, all_errors
from io import BytesIO
import posixpath
import ssl
from uuid import uuid4

from .base import (
    PCLogConnectionError, PCLogPermissionError, RemoteLogEntry, StableDownloadMixin,
    excluded, normalize_path, select_entries,
)


@contextmanager
def ftp_errors():
    try:
        yield
    except error_perm:
        raise PCLogPermissionError('FTP 操作被拒绝，请检查账号权限和目录是否存在。') from None
    except all_errors:
        raise PCLogConnectionError('FTP 连接或传输失败，请检查地址、端口和 TLS 设置。') from None
    except (ValueError, TypeError):
        raise PCLogConnectionError('FTP 返回的文件元数据无效。') from None


def parse_timestamp(value):
    format_string = '%Y%m%d%H%M%S.%f' if '.' in value else '%Y%m%d%H%M%S'
    return datetime.strptime(value, format_string).replace(tzinfo=timezone.utc)


class FTPPCLogConnector(StableDownloadMixin):
    def __init__(self, source, password, *, client=None):
        self.source = source
        root = str(source.remote_root_directory or '').replace('\\', '/')
        self.root = '/' + normalize_path(root.lstrip('/'))
        self.client = client
        self.password = password
        self.connected = False

    def _connect(self):
        if self.connected:
            return
        try:
            with ftp_errors():
                if self.client is None:
                    self.client = FTP_TLS(context=ssl.create_default_context()) if self.source.ftp_use_tls else FTP()
                self.client.connect(self.source.host, self.source.port, timeout=30)
                self.client.login(self.source.username, self.password)
                if self.source.ftp_use_tls:
                    self.client.prot_p()
                self.client.set_pasv(self.source.ftp_passive)
                self.connected = True
        except Exception:
            self.close()
            raise

    def _path(self, path):
        return posixpath.join(self.root, normalize_path(path))

    def list_json(self):
        self._connect()
        rows = []
        pending = [normalize_path(self.source.remote_incoming_directory)]
        with ftp_errors():
            while pending:
                directory = pending.pop()
                if excluded(self.source, directory):
                    continue
                for name, facts in self.client.mlsd(self._path(directory), facts=['type', 'size', 'modify']):
                    if name in ('.', '..'):
                        continue
                    if '/' in name or '\\' in name:
                        raise PCLogConnectionError('FTP 目录返回了无效文件名。')
                    path = normalize_path(posixpath.join(directory, name))
                    if excluded(self.source, path):
                        continue
                    if facts.get('type') == 'dir' and self.source.recursive:
                        pending.append(path)
                    elif facts.get('type') == 'file' and name.lower().endswith('.json'):
                        rows.append(RemoteLogEntry(path, int(facts['size']), parse_timestamp(facts['modify'])))
        return select_entries(self.source, rows)

    def stat(self, path):
        path = normalize_path(path)
        self._connect()
        with ftp_errors():
            self.client.voidcmd('TYPE I')
            size = self.client.size(self._path(path))
            modified = self.client.sendcmd('MDTM ' + self._path(path))
            if not modified.startswith('213 '):
                raise PCLogConnectionError('FTP 服务器未返回文件修改时间。')
            return RemoteLogEntry(path, int(size), parse_timestamp(modified[4:]))

    def _read(self, path, callback):
        self._connect()
        with ftp_errors():
            self.client.retrbinary('RETR ' + self._path(path), callback, blocksize=65536)

    def mkdirs(self, path):
        self._connect()
        parts = normalize_path(path).split('/')
        with ftp_errors():
            for index in range(1, len(parts) + 1):
                directory = self._path('/'.join(parts[:index]))
                try:
                    self.client.mkd(directory)
                except error_perm:
                    # 550 can mean either "exists" or "permission denied"; verify it.
                    previous = self.client.pwd()
                    try:
                        self.client.cwd(directory)
                    finally:
                        self.client.cwd(previous)

    def move(self, source, destination):
        src, dst = self._path(source), self._path(destination)
        self.mkdirs(posixpath.dirname(normalize_path(destination)))
        with ftp_errors():
            # FTP has no portable atomic NOREPLACE. Use unique archive identities
            # upstream, and refuse any destination already visible here.
            for name, _facts in self.client.mlsd(posixpath.dirname(dst)):
                if name == posixpath.basename(dst):
                    raise PCLogPermissionError('FTP 归档目标已存在，保留两份文件并停止移动。')
            self.client.rename(src, dst)

    def probe(self):
        self._connect()
        token = '.pc-log-probe-' + uuid4().hex
        original = posixpath.join(normalize_path(self.source.remote_incoming_directory), token)
        targets = [original]
        payload = b'pc-log-source-probe'
        try:
            with ftp_errors():
                # Exhaust the listing without downloading any user logs.
                list(self.client.mlsd(self._path(self.source.remote_incoming_directory)))
                self.client.storbinary('STOR ' + self._path(original), BytesIO(payload))
                received = bytearray()
                self._read(original, received.extend)
                if bytes(received) != payload:
                    raise PCLogConnectionError('FTP 测试文件读取校验失败。')
                current = original
                for directory in (self.source.remote_processed_directory, self.source.remote_failed_directory):
                    destination = posixpath.join(normalize_path(directory), token)
                    targets.append(destination)
                    self.move(current, destination)
                    current = destination
                self.client.delete(self._path(current))
                targets.clear()
        finally:
            for path in targets:
                try:
                    self.client.delete(self._path(path))
                except all_errors:
                    pass

    def close(self):
        self.connected = False
        if self.client is not None:
            try:
                self.client.quit()
            except all_errors:
                self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
