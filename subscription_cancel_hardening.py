"""Return unused subscription units when a booking is cancelled.

Both new cancel_booking_record paths and older update_booking(status='cancelled') paths
are covered. Consumed units are never restored, and release_booking_unit itself is
idempotent.
"""

from __future__ import annotations

import database as _db
import subscription_hardening as subs


def install_subscription_cancel_release(app) -> None:
    legacy = app.legacy
    if getattr(_db, "_subscription_cancel_release_installed", False):
        return

    original_cancel = _db.cancel_booking_record
    original_update = _db.update_booking

    async def cancel_with_release(booking_id, *args, **kwargs):
        changed, booking = await original_cancel(booking_id, *args, **kwargs)
        if changed:
            await subs.release_booking_unit(int(booking_id))
        return changed, booking

    async def update_with_release(booking_id, **kwargs):
        result = await original_update(booking_id, **kwargs)
        if kwargs.get("status") == "cancelled":
            await subs.release_booking_unit(int(booking_id))
        return result

    _db.cancel_booking_record = cancel_with_release
    _db.update_booking = update_with_release
    # New wrapper code often resolves functions through database aliases at runtime.
    legacy.cancel_booking_record = cancel_with_release
    legacy.update_booking = update_with_release
    _db._subscription_cancel_release_installed = True
