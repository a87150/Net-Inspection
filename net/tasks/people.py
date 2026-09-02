"""Compatibility alias for personnel task orchestration."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.people.tasks')
