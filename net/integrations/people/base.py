"""Compatibility alias for personnel directory contracts."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.people.directory.base')
