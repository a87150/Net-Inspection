"""Compatibility alias for device SSH collectors."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.infrastructure.ssh_collectors')
