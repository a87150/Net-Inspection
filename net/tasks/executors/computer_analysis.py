"""Compatibility alias for PC analysis task execution."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.devices.pc.executor')
