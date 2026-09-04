"""Prevent ordinary cancellation from rewriting an already completed lesson.

A completed lesson may already have statistics, a consumed subscription unit and a
closing fiscal receipt. Reversing it therefore requires an explicit correction flow;
the generic cancellation/update helpers must fail closed even for stale UI buttons.
"""

from __future__ import annotations

import logging
from types import FunctionType

import database as _db


async def _admin_completed_cancel_confirm_guard(call):
    """Closure-free replacement for Telegram's already-registered cancel prompt."""
    try:
        booking_id = int(call.data.rsplit("_", 1)[1])
    except (AttributeError, TypeError, ValueError):
        return await _completed_cancel_confirm_original(call)

    if call.from_user.id == legacy.ADMING_ID:
        booking = await _db.get_booking(booking_id)
        if booking and booking.get("status") == "completed":
            await legacy.safe_answer(
                call,
                "Проведённое занятие нельзя отменить обычной отменой. Нужна отдельная корректировка.",
                show_alert=True,
            )
            await _show_admin_booking(call, booking_id)
            return
    return await _completed_cancel_confirm_original(call)


def _clone(fn):
    return FunctionType(
        fn.__code__, fn.__globals__, name=fn.__name__,
        argdefs=fn.__defaults__, closure=fn.__closure__,
    )


def install_completed_cancellation_guard(app) -> None:
    legacy_module = app.legacy
    if getattr(_db, "_completed_cancellation_guard_installed", False):
        if hasattr(_db, "_guarded_update_booking"):
            legacy_module.update_booking = _db._guarded_update_booking
        return

    original_change = _db.change_booking_status
    original_db_update = _db.update_booking
    original_legacy_update = getattr(legacy_module, "update_booking", None)

    async def guarded_change_booking_status(
        booking_id: int,
        new_status: str,
        *args,
        **kwargs,
    ):
        if str(new_status) == "cancelled":
            booking = await _db.get_booking(int(booking_id))
            if booking and booking.get("status") == "completed":
                logging.warning(
                    "Blocked ordinary completed->cancelled transition for booking=%s; use a dedicated correction flow",
                    booking_id,
                )
                return False, booking
        return await original_change(booking_id, new_status, *args, **kwargs)

    async def guarded_db_update(booking_id: int, **kwargs):
        if kwargs.get("status") == "cancelled":
            booking = await _db.get_booking(int(booking_id))
            if booking and booking.get("status") == "completed":
                logging.warning(
                    "Blocked direct update completed->cancelled for booking=%s",
                    booking_id,
                )
                return booking
        return await original_db_update(booking_id, **kwargs)

    async def guarded_legacy_update(booking_id: int, **kwargs):
        if kwargs.get("status") == "cancelled":
            booking = await _db.get_booking(int(booking_id))
            if booking and booking.get("status") == "completed":
                logging.warning(
                    "Blocked legacy update completed->cancelled for booking=%s",
                    booking_id,
                )
                return booking
        if original_legacy_update is None:
            return await guarded_db_update(booking_id, **kwargs)
        return await original_legacy_update(booking_id, **kwargs)

    _db.change_booking_status = guarded_change_booking_status
    _db.update_booking = guarded_db_update
    _db._guarded_update_booking = guarded_db_update
    if original_legacy_update is not None:
        legacy_module.update_booking = guarded_legacy_update

    # Telegram's callback handler was already registered by Bot_test import. Preserve
    # its identity but replace the code so old cached completed-cancel buttons fail
    # before they can even display a misleading confirmation screen.
    cancel_prompt = getattr(app, "admin_booking_cancel_confirm", None)
    if cancel_prompt is not None:
        if cancel_prompt.__code__.co_freevars or _admin_completed_cancel_confirm_guard.__code__.co_freevars:
            raise RuntimeError("completed cancellation callback replacement cannot use closures")
        app._completed_cancel_confirm_original = _clone(cancel_prompt)
        cancel_prompt.__code__ = _admin_completed_cancel_confirm_guard.__code__

    # The transplanted callback code resolves names in Bot_test's globals.
    app.legacy = legacy_module
    app._db = _db

    # Functions such as admin_cancel_booking/cancel_booking_record are defined in
    # database.py and resolve change_booking_status dynamically from that module, so
    # the central guard above also protects direct/stale cancellation callbacks.
    _db._completed_cancellation_guard_installed = True
