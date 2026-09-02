"""Compatibility alias for Active Directory forms."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.domain.forms')
