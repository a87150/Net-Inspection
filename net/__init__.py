__version__ = "1.0.0"

"""Application runtime boundary (includes pathlib junction protection)."""
import sys


def require_runtime():
    if sys.version_info < (3, 14):
        raise RuntimeError('Network Inspection requires Python 3.14 or newer.')


require_runtime()
