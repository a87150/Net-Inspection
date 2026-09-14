from pathlib import Path
from datetime import datetime, timezone
from net.devices.pc.connectors.base import PCLogConnectionError, RemoteLogEntry, normalize_path


class MemoryConnector:
    def __init__(self, files):
        self.files = dict(files)
        self.downloads = []
        self.moves = []
        self.fail_moves = False
        self.after_download = None
        self.modified_at = datetime(2026, 9, 7, 4, tzinfo=timezone.utc)

    @property
    def paths(self):
        return list(self.files)

    def list_json(self):
        return [self.stat(path) for path in self.paths if path.endswith('.json')]

    def stat(self, path):
        path = normalize_path(path)
        if path not in self.files:
            raise PCLogConnectionError('文件不可用。')
        raw = self.files[path]
        return RemoteLogEntry(path, len(raw) if isinstance(raw, bytes) else 2, self.modified_at)

    def download(self, path, destination):
        row = self.stat(path)
        raw = self.files[path]
        if isinstance(raw, Exception):
            raise raw
        with Path(destination).open('xb') as output:
            output.write(raw)
        self.downloads.append(path)
        if self.after_download:
            self.after_download()
        return row

    def move(self, source, destination):
        if self.fail_moves:
            raise PCLogConnectionError('归档不可用。')
        if destination in self.files:
            raise PCLogConnectionError('归档已存在。')
        self.files[destination] = self.files.pop(source)
        self.moves.append((source, destination))

    def mkdirs(self, path): pass
    def probe(self): pass
    def close(self): pass


def memory_connector(files):
    return MemoryConnector(files)
