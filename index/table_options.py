"""Compatibility alias for shared table option helpers."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.common.table_options')
