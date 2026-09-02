"""Compatibility alias for filtered table CSV export."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.data_exchange.table_csv')
