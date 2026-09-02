"""Compatibility alias for personnel integration forms."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('index.people.forms')
