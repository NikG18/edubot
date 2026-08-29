"""Runtime bridge for legacy handler code objects.

Bot_test.py replaces several already-registered aiogram handler code objects. Those
functions keep Bot_test_legacy's globals, so names introduced by the compatibility
layer need lazy access to the active modules. This bridge is intentionally tiny and
can be removed once the legacy wrapper is folded into a single module.
"""

import builtins
import importlib


class _LazyModule:
    def __init__(self, module_name: str):
        self._module_name = module_name

    def __getattr__(self, name):
        module = importlib.import_module(self._module_name)
        return getattr(module, name)


# Python falls back to builtins when a replaced legacy handler cannot find these
# compatibility module names in Bot_test_legacy.__globals__.
if not hasattr(builtins, "legacy"):
    builtins.legacy = _LazyModule("Bot_test_legacy")
if not hasattr(builtins, "_db"):
    builtins._db = _LazyModule("database")
