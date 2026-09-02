"""Compatibility alias for Active Directory synchronization."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.domain.sync')
