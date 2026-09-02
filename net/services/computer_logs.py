"""Compatibility alias for PC log ingestion."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.devices.pc.logs')
