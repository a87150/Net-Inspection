"""Compatibility alias for personnel integration views."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.people.integrations')
