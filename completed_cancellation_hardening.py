"""Prevent ordinary cancellation from rewriting an already completed lesson.

A completed lesson may already have statistics, a consumed subscription unit and a
closing fiscal receipt. Reversing it therefore requires an explicit correction flow;
the generic cancellation/update helpers must fail closed even for stale UI buttons.
"""

from __future__ import annotations

import logging

import database as _db


def install_completed_cancellation_guard(app) -> None:
    legacy = app.legacy
    if getattr(_db, "_completed_cancellation_guard_installed", False):
        # Each bot process has its own module graph, but keep legacy aliases aligned
        # if an installer is invoked twice inside the same process.
        if hasattr(_db, "_guarded_update_booking"):
            legacy.update_booking = _db._guarded_update_booking
        return

    original_change = _db.change_booking_status
    original_db_update = _db.update_booking
    original_legacy_update = getattr(legacy, "update_booking", None)

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
        legacy.update_booking = guarded_legacy_update

    # Functions such as admin_cancel_booking/cancel_booking_record are defined in
    # database.py and resolve change_booking_status dynamically from that module, so
    # the central guard above also protects stale admin cancellation callbacks.
    _db._completed_cancellation_guard_installed = True
