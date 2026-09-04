"""Compatibility alias for archived Telegram legacy implementation.

The implementation lives in archive/Bot_test_legacy.py. Remove this alias only
after active compatibility layers stop importing Bot_test_legacy by its old name.
"""
import sys as _sys
from archive import Bot_test_legacy as _impl

_sys.modules[__name__] = _impl
