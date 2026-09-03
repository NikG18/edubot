"""Cross-process deduplication for T-Bank payment status polling."""

from __future__ import annotations

import logging

import database as _db


async def _poll_pending(legacy, *, telegram_bot=None) -> None:
    bookings = await _db.get_all_bookings()
    for booking_id, snapshot in bookings.items():
        if not legacy.booking_needs_payment_poll(snapshot):
            continue
        payment_id = snapshot.get("tinkoff_payment_id")
        if not payment_id:
            continue

        await _db._ensure_pool()
        async with _db._legacy.pool.acquire() as conn:
            async with conn.transaction():
                # Telegram and VK may reach this point from the same DB snapshot.
                # Only one of them may query T-Bank for this PaymentId at a time.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"payment-poll:{payment_id}",
                )
                current_row = await conn.fetchrow(
                    "SELECT * FROM bookings WHERE id=$1",
                    int(booking_id),
                )
                current = dict(current_row) if current_row else None
                if not current or not legacy.booking_needs_payment_poll(current):
                    continue

                payment_state = await legacy.check_payment(str(payment_id))
                if not payment_state.get("Success"):
                    continue
                status = str(payment_state.get("Status") or "").upper()
                changed, booking = await legacy.process_booking_payment_status(
                    int(booking_id), status
                )

        if status in {"CONFIRMED", "AUTHORIZED"}:
            if not changed or not booking:
                continue
            await legacy.send_to_user(
                booking["user_id"],
                booking.get("user_platform", "telegram"),
                "✅ Оплата получена! Занятие подтверждено.",
            )
            await legacy.send_to_tutor(
                booking["tutor_id"],
                f"✅ Оплата за занятие {booking['date']} {booking['time_slot']} получена.",
            )
        elif status in {"REJECTED", "CANCELED"}:
            if changed and booking:
                await legacy.send_to_user(
                    booking["user_id"],
                    booking.get("user_platform", "telegram"),
                    "❌ Платёж не прошёл. Запись отменена.",
                )

    if telegram_bot is not None:
        await _cleanup_telegram_payment_messages(legacy, telegram_bot)


async def _cleanup_telegram_payment_messages(legacy, bot) -> None:
    """Remove stale payment-link messages even when VK/webhook won the status race."""
    bookings = await _db.get_all_bookings()
    for booking_id, booking in bookings.items():
        if booking.get("user_platform") != "telegram":
            continue
        message_id = booking.get("payment_msg_id")
        if not message_id or booking.get("status") not in {"paid", "cancelled", "completed"}:
            continue
        try:
            await bot.delete_message(
                chat_id=int(booking["user_id"]),
                message_id=int(message_id),
            )
        except Exception as exc:
            text = str(exc).lower()
            # If Telegram already considers it absent, stop retrying every 5 min.
            if "message to delete not found" not in text and "message can't be deleted" not in text:
                logging.warning(
                    "Не удалось удалить сообщение оплаты booking=%s: %s",
                    booking_id,
                    exc,
                )
                continue
        await _db.update_booking(int(booking_id), payment_msg_id=None)


def install_telegram_payment_poll_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_payment_poll_tg_hardened", False):
        return

    async def check_pending_payments(bot):
        return await _poll_pending(legacy, telegram_bot=bot)

    legacy.check_pending_payments = check_pending_payments
    if hasattr(app, "check_pending_payments"):
        app.check_pending_payments = check_pending_payments
    legacy._payment_poll_tg_hardened = True


def install_vk_payment_poll_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_payment_poll_vk_hardened", False):
        return

    async def check_pending_payments():
        return await _poll_pending(legacy, telegram_bot=None)

    legacy.check_pending_payments = check_pending_payments
    if hasattr(app, "check_pending_payments"):
        app.check_pending_payments = check_pending_payments
    legacy._payment_poll_vk_hardened = True
