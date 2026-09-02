"""Compatibility alias for inspection item selection."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.inspections.selection')
