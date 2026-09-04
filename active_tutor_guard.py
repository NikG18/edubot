"""Final server-side guard against new obligations for archived tutors."""

from __future__ import annotations

import logging

import database as _db
from tutor_archive_hardening import ensure_tutor_archive_schema

_runtime_legacy = None
_original_db_add_booking = None
_original_subscription_payment = None


async def is_tutor_active(tutor_id: int) -> bool:
    await ensure_tutor_archive_schema()
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT active FROM tutors WHERE id=$1",
            int(tutor_id),
        )
    return value is True


async def _guarded_add_booking(
    tutor_id,
    user_id,
    username,
    subject,
    date,
    time_slot,
    channel_msg_id=None,
    user_platform="telegram",
    booking_type="regular",
    trial_email=None,
):
    if not await is_tutor_active(int(tutor_id)):
        logging.warning(
            "Blocked new %s booking for archived/missing tutor=%s",
            booking_type,
            tutor_id,
        )
        return None
    return await _original_db_add_booking(
        tutor_id,
        user_id,
        username,
        subject,
        date,
        time_slot,
        channel_msg_id=channel_msg_id,
        user_platform=user_platform,
        booking_type=booking_type,
        trial_email=trial_email,
    )


async def _guarded_subscription_payment(
    source,
    bot,
    user_id,
    tutor_id,
    subject,
    count,
    total,
    discount,
    email,
    user_platform,
):
    legacy = _runtime_legacy
    if legacy is None:
        raise RuntimeError("Active tutor guard is not installed")
    if not await is_tutor_active(int(tutor_id)):
        text = (
            "Этот преподаватель больше не принимает новые занятия. "
            "Платёж не создан; выберите другого преподавателя."
        )
        try:
            if isinstance(source, legacy.types.Message):
                await source.answer(text)
            elif isinstance(source, legacy.types.CallbackQuery):
                try:
                    await source.message.edit_text(text)
                except Exception:
                    await source.message.answer(text)
            else:
                await legacy.send_to_user(int(user_id), str(user_platform), text)
        except Exception:
            logging.exception("Could not report archived tutor package-payment block")
        return False
    return await _original_subscription_payment(
        source,
        bot,
        user_id,
        tutor_id,
        subject,
        count,
        total,
        discount,
        email,
        user_platform,
    )


def install_active_tutor_guard(app, platform: str) -> None:
    global _runtime_legacy, _original_db_add_booking, _original_subscription_payment
    legacy = app.legacy

    if not getattr(_db, "_active_tutor_booking_guard_installed", False):
        _original_db_add_booking = _db.add_booking
        _db.add_booking = _guarded_add_booking
        _db._active_tutor_booking_guard_installed = True

    # Bot_test/vk_bot and their legacy modules imported database.add_booking before
    # installers ran. Update every reachable alias, not just database.py itself.
    legacy.add_booking = _guarded_add_booking
    if hasattr(app, "add_booking"):
        app.add_booking = _guarded_add_booking

    if str(platform).lower() == "telegram" and hasattr(legacy, "create_subscription_payment"):
        if not getattr(legacy, "_active_tutor_subscription_payment_guard_installed", False):
            _runtime_legacy = legacy
            _original_subscription_payment = legacy.create_subscription_payment
            legacy.create_subscription_payment = _guarded_subscription_payment
            if hasattr(app, "create_subscription_payment"):
                app.create_subscription_payment = _guarded_subscription_payment
            legacy._active_tutor_subscription_payment_guard_installed = True

    legacy.is_tutor_active = is_tutor_active
    if hasattr(app, "is_tutor_active"):
        app.is_tutor_active = is_tutor_active
    legacy._active_tutor_guard_installed = True
