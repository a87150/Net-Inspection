"""Compatibility alias for device configuration export views."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.devices.configuration')
