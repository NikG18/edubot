"""Compatibility alias for archived messaging legacy implementation.

The implementation lives in archive/messaging_legacy.py. Remove this alias only
after messaging.py no longer depends on the legacy compatibility layer.
"""
import sys as _sys
from archive import messaging_legacy as _impl

_sys.modules[__name__] = _impl
