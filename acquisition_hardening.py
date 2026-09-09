"""Record first- and last-touch student acquisition data on bot entry."""

from __future__ import annotations

import logging
from types import FunctionType

import database as _db
from student_acquisition import telegram_start_source, vk_start_source


def _clone(fn):
    return FunctionType(
        fn.__code__, fn.__globals__, name=fn.__name__,
        argdefs=fn.__defaults__, closure=fn.__closure__,
    )


async def _telegram_start_with_acquisition(message):
    source, payload = telegram_start_source(message.text)
    try:
        await record_student_acquisition(
            "telegram", message.from_user.id, source, payload
        )
    except Exception:
        logging.exception("Could not record Telegram acquisition source")
    return await _acquisition_original_telegram_start(message)


async def _vk_start_with_acquisition(message):
    source, payload = vk_start_source(message)
    try:
        await record_student_acquisition("vk", message.from_id, source, payload)
    except Exception:
        logging.exception("Could not record VK acquisition source")
    return await _acquisition_original_vk_start(message)


def _install(app, platform: str) -> None:
    legacy = app.legacy
    marker = f"_student_acquisition_{platform}_installed"
    if getattr(legacy, marker, False):
        return

    target_name = "Start" if platform == "telegram" else "start_handler"
    replacement = (
        _telegram_start_with_acquisition
        if platform == "telegram"
        else _vk_start_with_acquisition
    )
    target = getattr(legacy, target_name)
    original_name = f"_acquisition_original_{platform}_start"

    if target.__code__.co_freevars or replacement.__code__.co_freevars:
        raise RuntimeError("Acquisition start replacement cannot use closures")
    setattr(legacy, original_name, _clone(target))
    legacy.record_student_acquisition = _db.record_student_acquisition
    legacy.telegram_start_source = telegram_start_source
    legacy.vk_start_source = vk_start_source
    legacy.logging = logging
    target.__code__ = replacement.__code__
    setattr(legacy, marker, True)


def install_telegram_acquisition_hardening(app) -> None:
    _install(app, "telegram")


def install_vk_acquisition_hardening(app) -> None:
    _install(app, "vk")

