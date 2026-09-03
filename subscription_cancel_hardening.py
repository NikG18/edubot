"""Return unused subscription units when a booking is cancelled.

Both database-level cancel paths and already-installed legacy/context wrappers are
preserved. We only add an idempotent release AFTER the existing cancellation logic.
"""

from __future__ import annotations

import database as _db
import subscription_hardening as subs


def install_subscription_cancel_release(app) -> None:
    legacy = app.legacy
    if getattr(_db, "_subscription_cancel_release_installed", False):
        return

    original_db_cancel = _db.cancel_booking_record
    original_db_update = _db.update_booking
    original_legacy_cancel = getattr(legacy, "cancel_booking_record", None)
    original_legacy_update = getattr(legacy, "update_booking", None)

    async def db_cancel_with_release(booking_id, *args, **kwargs):
        changed, booking = await original_db_cancel(booking_id, *args, **kwargs)
        if changed:
            await subs.release_booking_unit(int(booking_id))
        return changed, booking

    async def db_update_with_release(booking_id, **kwargs):
        result = await original_db_update(booking_id, **kwargs)
        if kwargs.get("status") == "cancelled":
            await subs.release_booking_unit(int(booking_id))
        return result

    async def legacy_cancel_with_release(booking_id, *args, **kwargs):
        if original_legacy_cancel is None or original_legacy_cancel is original_db_cancel:
            return await db_cancel_with_release(booking_id, *args, **kwargs)
        changed, booking = await original_legacy_cancel(booking_id, *args, **kwargs)
        if changed:
            await subs.release_booking_unit(int(booking_id))
        return changed, booking

    async def legacy_update_with_release(booking_id, **kwargs):
        if original_legacy_update is None or original_legacy_update is original_db_update:
            return await db_update_with_release(booking_id, **kwargs)
        result = await original_legacy_update(booking_id, **kwargs)
        if kwargs.get("status") == "cancelled":
            await subs.release_booking_unit(int(booking_id))
        return result

    _db.cancel_booking_record = db_cancel_with_release
    _db.update_booking = db_update_with_release
    if original_legacy_cancel is not None:
        legacy.cancel_booking_record = legacy_cancel_with_release
    if original_legacy_update is not None:
        legacy.update_booking = legacy_update_with_release
    _db._subscription_cancel_release_installed = True
