"""Application runtime boundary (includes pathlib junction protection)."""
import sys
from pathlib import Path


def _read_version():
    """版本号的唯一事实源是仓库根目录的 VERSION 文件。

    以前这里有硬编码的 __version__，README 和 VERSION 各写一份，三处指三个
    版本。现在只读文件，改版本只改一处。
    """
    try:
        return (Path(__file__).resolve().parent.parent / 'VERSION').read_text(encoding='utf-8').strip()
    except OSError:
        return '0.0.0'


__version__ = _read_version()


def require_runtime():
    if sys.version_info < (3, 12):
        raise RuntimeError('Network Inspection requires Python 3.12 or newer.')


require_runtime()
