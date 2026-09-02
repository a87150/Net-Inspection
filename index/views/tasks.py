"""Compatibility alias for inspection task views."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.inspections.tasks')
