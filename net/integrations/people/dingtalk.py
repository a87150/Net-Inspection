"""Compatibility alias for the DingTalk personnel directory adapter."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.people.directory.dingtalk')
