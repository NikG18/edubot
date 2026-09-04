"""Compatibility alias for archived VK legacy implementation.

The implementation lives in archive/vk_bot_legacy.py. Remove this alias only
after active compatibility layers stop importing vk_bot_legacy by its old name.
"""
import sys as _sys
from archive import vk_bot_legacy as _impl

_sys.modules[__name__] = _impl
