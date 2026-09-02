"""Compatibility alias for the shared table registry."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.common.table_registry')
