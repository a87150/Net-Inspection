"""Compatibility alias for dashboard asset summaries."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.dashboard.assets')
