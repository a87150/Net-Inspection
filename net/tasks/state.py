"""Compatibility alias for inspection task state services."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.inspections.state')
