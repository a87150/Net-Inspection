"""Compatibility alias for personnel directory synchronization."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.people.directory.sync')
