"""Compatibility alias for cancellable native HTTP collection."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.infrastructure.native_http')
