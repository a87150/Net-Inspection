"""Compatibility alias for shared sanitization services."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.infrastructure.sanitization')
