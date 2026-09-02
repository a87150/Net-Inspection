"""Compatibility alias for inspection record summaries."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.inspections.record_summary')
