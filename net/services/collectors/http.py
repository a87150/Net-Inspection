"""Compatibility alias for device HTTP collectors."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.infrastructure.http_collectors')
