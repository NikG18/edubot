"""Подключает юридический слой после полной загрузки legacy-модуля бота.

Python импортирует sitecustomize автоматически при обычном запуске. Хук не трогает
остальные модули и срабатывает только после успешного импорта Telegram/VK legacy.
"""

import builtins
import importlib

_original_import = builtins.__import__
_loading_legal = False


def _import_with_legal(name, globals=None, locals=None, fromlist=(), level=0):
    global _loading_legal
    module = _original_import(name, globals, locals, fromlist, level)
    if _loading_legal or level != 0:
        return module

    target = None
    if name == "Bot_test_legacy":
        target = "legal_telegram"
    elif name == "vk_bot_legacy":
        target = "legal_vk"

    if target:
        _loading_legal = True
        try:
            importlib.import_module(target)
        finally:
            _loading_legal = False
    return module


builtins.__import__ = _import_with_legal
