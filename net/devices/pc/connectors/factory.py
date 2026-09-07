from net.devices.pc.credentials import load_pc_source_secret
from .ftp import FTPPCLogConnector


def build_connector(source):
    password = load_pc_source_secret(source)
    if source.source_type == 'ftp':
        return FTPPCLogConnector(source, password)
    if source.source_type == 'smb':
        from .smb import SMBPCLogConnector
        return SMBPCLogConnector(source, password)
    raise ValueError('不支持的 PC 日志来源类型。')
