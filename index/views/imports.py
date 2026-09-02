"""Compatibility alias for shared import views."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.common.imports')
