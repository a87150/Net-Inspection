"""Compatibility alias for inventory CSV exchange."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.data_exchange.inventory_csv')
