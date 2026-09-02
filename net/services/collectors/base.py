"""Compatibility alias for collection result primitives."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.infrastructure.collection')
