"""Compatibility alias for downloadable PC script views."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.devices.pc.scripts')
