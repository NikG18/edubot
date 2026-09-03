"""Cross-platform booking hooks for paid subscription lessons."""

from __future__ import annotations

import logging
from types import FunctionType

import database as _db
import payments
import subscription_hardening as subs


async def _commission_percent(legacy, booking: dict) -> int:
    tutors = await legacy.get_all_tutors()
    tutor = tutors.get(int(booking["tutor_id"]))
    if not tutor:
        return 25
    if payments.is_operator_tutor(tutor.get("inn")):
        return 0
    if tutor.get("commission_mode") == "auto":
        now = legacy.now_msk_naive()
        percent, _ = await legacy.calculate_auto_commission(
            int(booking["tutor_id"]), now.year, now.month
        )
        return int(percent)
    return int(tutor.get("commission_percent", 25))


async def _confirm_from_subscription(legacy, booking_id: int, actor_id: int) -> dict | None:
    """Atomically reserve one package unit and mark the booking paid.

    Returns None when no matching paid subscription exists, so the caller can run
    the unchanged legacy payment flow.
    """
    await subs.ensure_subscription_schema()
    booking = await _db.get_booking(int(booking_id))
    if not booking or booking.get("booking_type") == "trial" or booking.get("status") != "pending":
        return None
    percent = await _commission_percent(legacy, booking)

    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", int(booking_id))
            if not current or current["status"] != "pending" or current["booking_type"] == "trial":
                return None

            existing = await conn.fetchrow(
                "SELECT * FROM subscription_usages WHERE booking_id=$1 FOR UPDATE", int(booking_id)
            )
            if existing and existing["status"] in {"reserved", "consumed"}:
                subscription = await conn.fetchrow(
                    "SELECT remaining_lessons FROM subscriptions WHERE id=$1",
                    existing["subscription_id"],
                )
                await conn.execute(
                    "UPDATE bookings SET status='paid',commission_percent=$1,updated_at=NOW() WHERE id=$2",
                    percent, int(booking_id),
                )
                return {
                    **dict(existing),
                    "remaining_lessons": int(subscription["remaining_lessons"] or 0) if subscription else 0,
                    "already_reserved": True,
                }

            subscription = await conn.fetchrow(
                """
                SELECT * FROM subscriptions
                WHERE student_id=$1 AND tutor_id=$2 AND subject=$3
                  AND active=1 AND remaining_lessons>0 AND payment_id IS NOT NULL
                ORDER BY activated_at NULLS LAST, id
                LIMIT 1 FOR UPDATE
                """,
                current["student_id"], current["tutor_id"], current["subject"],
            )
            if not subscription:
                return None

            used_count = int(await conn.fetchval(
                "SELECT COUNT(*) FROM subscription_usages WHERE subscription_id=$1",
                subscription["id"],
            ) or 0)
            unit_index = used_count + 1
            amount_kop = subs.allocated_unit_amount(
                subs._rub_to_kop(subscription["total_price"]),
                int(subscription["total_lessons"]),
                unit_index,
            )
            usage = await conn.fetchrow(
                """
                INSERT INTO subscription_usages
                    (subscription_id,booking_id,unit_index,amount_kop,status)
                VALUES($1,$2,$3,$4,'reserved')
                RETURNING *
                """,
                subscription["id"], int(booking_id), unit_index, amount_kop,
            )
            remaining = int(subscription["remaining_lessons"]) - 1
            await conn.execute(
                "UPDATE subscriptions SET remaining_lessons=$1,active=$2 WHERE id=$3",
                remaining, 1 if remaining > 0 else 0, subscription["id"],
            )
            await conn.execute(
                """
                UPDATE bookings
                SET status='paid',subscription_id=$1,subscription_unit_index=$2,
                    subscription_unit_amount=$3,amount=$3,commission_percent=$4,
                    reminded=0,updated_at=NOW()
                WHERE id=$5
                """,
                subscription["id"], unit_index, amount_kop, percent, int(booking_id),
            )
            try:
                await _db._add_booking_event(
                    conn, int(booking_id), "subscription_reserved", "pending", "paid",
                    "tutor", actor_id,
                    {
                        "subscription_id": int(subscription["id"]),
                        "unit_index": unit_index,
                        "amount_kop": amount_kop,
                        "remaining_lessons": remaining,
                    },
                )
            except Exception:
                logging.exception("Could not record subscription booking event %s", booking_id)
            return {**dict(usage), "remaining_lessons": remaining, "already_reserved": False}


async def release_if_cancelled(booking_id: int) -> bool:
    return await subs.release_booking_unit(int(booking_id))


def _clone_function(fn):
    return FunctionType(
        fn.__code__, fn.__globals__, name=fn.__name__,
        argdefs=fn.__defaults__, closure=fn.__closure__,
    )


def install_telegram_subscription_booking(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_subscription_booking_tg_installed", False):
        return
    subs.install_subscription_database_aliases(app)
    original_confirm = _clone_function(legacy.tutor_confirm_booking)

    async def wrapped_confirm(call, bot, state):
        try:
            bid = int(call.data.rsplit("_", 1)[1])
        except (AttributeError, TypeError, ValueError):
            return await legacy._subscription_original_confirm(call, bot, state)
        booking = await _db.get_booking(bid)
        if not booking or booking.get("booking_type") == "trial":
            return await legacy._subscription_original_confirm(call, bot, state)
        if not await legacy._require_booking_tutor(call, booking):
            return
        result = await _confirm_from_subscription(legacy, bid, call.from_user.id)
        if result is None:
            return await legacy._subscription_original_confirm(call, bot, state)

        await legacy.send_to_user(
            booking["user_id"], booking.get("user_platform", "telegram"),
            "✅ Занятие подтверждено преподавателем и оплачено из абонемента.\n"
            f"📚 {booking['subject']}\n📅 {booking['date']} 🕒 {booking['time_slot']}\n"
            f"Осталось занятий в этом абонементе: {result['remaining_lessons']}."
        )
        await state.clear()
        await call.message.edit_text(
            "✅ Занятие подтверждено. Оплата списана из абонемента.\n"
            f"Остаток: {result['remaining_lessons']} занятий."
        )
        await _db._sync_booking_record_safely(bid)

    legacy._db = _db
    legacy._confirm_from_subscription = _confirm_from_subscription
    legacy._subscription_original_confirm = original_confirm
    legacy.tutor_confirm_booking.__code__ = wrapped_confirm.__code__
    legacy._subscription_booking_tg_installed = True


def install_vk_subscription_booking(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_subscription_booking_vk_installed", False):
        return
    subs.install_subscription_database_aliases(app)
    original_confirm = legacy.tutor_confirm_booking

    async def wrapped_confirm(event):
        try:
            bid = int(event.payload["cmd"].rsplit("_", 1)[1])
        except (KeyError, TypeError, ValueError):
            return await original_confirm(event)
        booking = await _db.get_booking(bid)
        if not booking or booking.get("booking_type") == "trial":
            return await original_confirm(event)
        tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
        if tutor_id != booking.get("tutor_id"):
            await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
            return
        result = await _confirm_from_subscription(legacy, bid, event.user_id)
        if result is None:
            return await original_confirm(event)
        await legacy.send_to_user(
            booking["user_id"], booking.get("user_platform", "vk"),
            "✅ Занятие подтверждено преподавателем и оплачено из абонемента.\n"
            f"📚 {booking['subject']}\n📅 {booking['date']} 🕒 {booking['time_slot']}\n"
            f"Осталось занятий в этом абонементе: {result['remaining_lessons']}."
        )
        await legacy.edit_event_message(
            event,
            "✅ Занятие подтверждено. Оплата списана из абонемента.\n"
            f"Остаток: {result['remaining_lessons']} занятий.",
        )
        await _db._sync_booking_record_safely(bid)

    legacy.tutor_confirm_booking = wrapped_confirm
    legacy._subscription_booking_vk_installed = True
