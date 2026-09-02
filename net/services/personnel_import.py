"""Compatibility alias for the personnel import feature."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.people.importing')
