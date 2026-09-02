"""Compatibility alias for inspection execution."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.inspections.executor')
