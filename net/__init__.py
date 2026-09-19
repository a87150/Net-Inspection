__version__ = "0.9.1"

"""Application runtime boundary (includes pathlib junction protection)."""
import sys


def require_runtime():
    if sys.version_info < (3, 12):
        raise RuntimeError('Network Inspection requires Python 3.12 or newer.')


require_runtime()
