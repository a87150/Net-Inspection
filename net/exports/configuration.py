"""Compatibility alias for saved device configuration export."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.data_exchange.configuration')
