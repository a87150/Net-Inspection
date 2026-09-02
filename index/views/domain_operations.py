"""Compatibility alias for Active Directory operation views."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.domain.operations')
