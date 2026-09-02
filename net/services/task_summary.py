"""Compatibility alias for inspection task summaries."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.inspections.task_summary')
