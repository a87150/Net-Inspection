"""Compatibility alias for inspection task forms."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.inspections.forms')
