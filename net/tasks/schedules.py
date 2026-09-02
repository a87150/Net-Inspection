"""Compatibility alias for inspection scheduling services."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.inspections.schedules')
