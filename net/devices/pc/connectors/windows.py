"""UNC filesystem access using the Windows process identity, without SMB login."""
import os
import sys

from .base import PCLogConnectionError
from .smb import SMBPCLogConnector


class WindowsFilesystem:
    """Small filesystem boundary shared with the existing transfer safeguards."""

    @staticmethod
    def register_session(*args, **kwargs):
        # The OS authenticates access to the UNC path using the process identity.
        if sys.platform != 'win32':
            raise PCLogConnectionError('当前账号访问共享仅支持 Windows；请切换为手动指定账号密码。')

    @staticmethod
    def reset_connection_cache(**kwargs):
        # Never disconnect Windows connections owned by other applications.
        pass

    @staticmethod
    def stat(path, *, follow_symlinks=False, **kwargs):
        return os.stat(path, follow_symlinks=follow_symlinks)

    @staticmethod
    def scandir(path, **kwargs):
        with os.scandir(path) as entries:
            return list(entries)

    @staticmethod
    def open_file(path, mode, **kwargs):
        return open(path, mode)

    @staticmethod
    def makedirs(path, *, exist_ok=False, **kwargs):
        os.makedirs(path, exist_ok=exist_ok)

    @staticmethod
    def rename(source, destination, **kwargs):
        # Windows os.rename refuses to overwrite an existing destination.
        os.rename(source, destination)

    @staticmethod
    def remove(path, **kwargs):
        os.remove(path)


class WindowsPCLogConnector(SMBPCLogConnector):
    def __init__(self, source):
        if source.port != 445:
            raise PCLogConnectionError('使用 Windows 运行账号时共享端口必须为 445。')
        super().__init__(source, None, client=WindowsFilesystem())
