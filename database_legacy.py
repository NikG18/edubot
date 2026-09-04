"""Compatibility alias for archived database legacy implementation.

The implementation lives in archive/database_legacy.py. Remove this alias only
after database.py no longer depends on the legacy compatibility layer.
"""
import sys as _sys
from archive import database_legacy as _impl

_sys.modules[__name__] = _impl
