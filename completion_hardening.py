"""Manual completion gate for paid lessons.

Regular paid lessons no longer become `completed` merely because the time slot ended.
Only Telegram admin confirmation performs the final transition, updates statistics,
consumes a subscription unit when applicable and submits the appropriate closing
receipt. A paid lesson may be finalized either as actually held or as counted under
the late student cancellation/no-show rule; both outcomes remain explicit in the
audit log. Trial lessons may still auto-complete after their slot for trial tracking.
"""

from __future__ import annotations

import logging
from datetime import datetime

import database as _db
import fiscal_receipts
import subscription_hardening as subs


async def _trial_only_cleanup(legacy):
    """Auto-complete only free trials; leave regular paid lessons awaiting confirmation."""
    await _db._ensure_pool()
    now = legacy.now_msk_naive()
    completed = []
    async with _db._legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM bookings WHERE status='confirmed' AND booking_type='trial'"
        )
        for row in rows:
            try:
                end_part = str(row["time_slot"]).split("-")[-1].replace(".", ":")
                end_dt = datetime.strptime(f"{row['date']} {end_part}", "%d.%m.%Y %H:%M")
            except (ValueError, TypeError, AttributeError):
                logging.warning("Invalid trial date/time booking=%s", row["id"])
                continue
            if end_dt >= now:
                continue
            async with conn.transaction():
                current = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", row["id"])
                if not current or current["status"] != "confirmed" or current["booking_type"] != "trial":
                    continue
                await conn.execute(
                    """
                    UPDATE bookings
                    SET status='completed',trial_consumed=TRUE,stats_counted=TRUE,updated_at=NOW()
                    WHERE id=$1
                    """,
                    row["id"],
                )
                try:
                    await _db._add_booking_event(
                        conn, row["id"], "completed", "confirmed", "completed",
                        "system", None, {"reason": "Пробное занятие завершилось"},
                    )
                except Exception:
                    logging.exception("Could not record trial completion event %s", row["id"])
                completed.append(int(row["id"]))
    for booking_id in completed:
        await _db._sync_booking_record_safely(booking_id)
    return completed


def _booking_has_ended(legacy, booking: dict) -> bool:
    try:
        end_part = str(booking["time_slot"]).split("-")[-1].replace(".", ":")
        end_dt = datetime.strptime(f"{booking['date']} {end_part}", "%d.%m.%Y %H:%M")
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return end_dt <= legacy.now_msk_naive()


def completion_outcome_details(outcome: str) -> tuple[str, str]:
    """Return stable audit event type and human-readable reason for finalization."""
    normalized = str(outcome or "held").strip().lower()
    if normalized == "late_student_cancel":
        return (
            "late_student_cancel_counted",
            "Поздняя отмена или неявка ученика: занятие засчитано по правилу менее 24 часов",
        )
    if normalized == "held":
        return "completed", "Факт проведения подтверждён администратором"
    raise ValueError("unsupported completion outcome")


async def _mark_completed(
    booking_id: int,
    admin_id: int,
    *,
    outcome: str = "held",
) -> tuple[bool, dict | None]:
    event_type, reason = completion_outcome_details(outcome)
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", int(booking_id))
            if not row:
                return False, None
            if row["status"] == "completed":
                return False, dict(row)
            if row["status"] != "paid":
                return False, dict(row)
            updated = await conn.fetchrow(
                "UPDATE bookings SET status='completed',stats_counted=TRUE,updated_at=NOW() WHERE id=$1 RETURNING *",
                int(booking_id),
            )
            try:
                await _db._add_booking_event(
                    conn, int(booking_id), event_type, "paid", "completed",
                    "admin", int(admin_id), {"reason": reason, "outcome": outcome},
                )
            except Exception:
                logging.exception("Could not record manual completion event %s", booking_id)
    return True, dict(updated)


async def _finalize_completed_booking(booking_id: int) -> dict:
    usage = await subs.get_booking_usage(int(booking_id))
    if usage:
        consumed = await subs.consume_booking_unit(int(booking_id))
        if not consumed:
            return {"ok": False, "reason": "subscription_unit_not_consumed"}
        receipt = await subs.send_subscription_closing_receipt(int(booking_id))
    else:
        receipt = await fiscal_receipts.send_booking_closing_receipt(int(booking_id))

    booking = await _db.get_booking(int(booking_id))
    if booking:
        await _db._recalculate_booking_stats(booking)
        await _db._sync_booking_record_safely(int(booking_id))
    return receipt


async def _render_admin_booking_with_completion(legacy, call, booking_id: int):
    """Render the original admin information/actions plus finalization controls."""
    booking = await _db.get_booking(int(booking_id))
    if not booking:
        await call.message.edit_text("Занятие не найдено.")
        return
    tutors = await _db.get_all_tutors()
    tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Неизвестный")
    amount = (booking.get("amount") or 0) / 100
    cancelled_line = ""
    if booking.get("cancelled_by"):
        cancelled_line = (
            f"\n❌ Кем отменено: {legacy.html.quote(str(booking['cancelled_by']))}"
            f"\n🕒 Отмена: {legacy.format_dt(booking.get('cancelled_at'))}"
        )
    subscription_line = ""
    if booking.get("subscription_id"):
        subscription_line = (
            f"\n🎟 Абонемент: #{booking['subscription_id']}"
            f" · занятие {booking.get('subscription_unit_index') or '—'}"
        )
    text = (
        f"📚 <b>Занятие #{booking_id}</b>\n\n"
        f"Статус: <b>{legacy.html.quote(str(booking['status']))}</b>\n"
        f"👤 {legacy.html.quote(str(booking['username']))}\n"
        f"👨‍🏫 {legacy.html.quote(str(tutor_name))}\n"
        f"📚 {legacy.html.quote(str(booking['subject']))}\n"
        f"📅 {legacy.html.quote(str(booking['date']))}\n"
        f"🕒 {legacy.html.quote(str(booking['time_slot']))}\n"
        f"🌐 {legacy.html.quote(str(booking.get('user_platform') or 'telegram'))}\n"
        f"💳 {amount:.2f} ₽\n"
        f"↩️ Возврат: {legacy.html.quote(str(booking.get('refund_status') or 'none'))}\n"
        f"🕘 Обновлено: {legacy.format_dt(booking.get('updated_at'))}"
        f"{subscription_line}{cancelled_line}"
    )
    buttons = []
    if booking["status"] == "paid":
        buttons.append([legacy.InlineKeyboardButton(
            text="✅ Подтвердить, что занятие проведено",
            callback_data=f"admin_booking_complete_{booking_id}",
        )])
        buttons.append([legacy.InlineKeyboardButton(
            text="⚠️ Засчитать: поздняя отмена / неявка",
            callback_data=f"admin_booking_late_cancel_{booking_id}",
        )])
    elif booking["status"] == "completed":
        buttons.append([legacy.InlineKeyboardButton(
            text="🧾 Проверить/повторить закрывающий чек",
            callback_data=f"admin_booking_complete_{booking_id}",
        )])
    # Database cancellation permits only pending/confirmed/paid. Completed lessons
    # require a separate correction/refund procedure and must not expose a dead button.
    if booking["status"] in {"pending", "confirmed", "paid"}:
        buttons.append([legacy.InlineKeyboardButton(
            text="❌ Отменить занятие",
            callback_data=f"admin_booking_cancel_{booking_id}",
        )])
    if booking.get("refund_status") in {"required", "pending"}:
        buttons.append([legacy.InlineKeyboardButton(
            text="↩️ Вернуть деньги клиенту",
            callback_data=f"admin_booking_refund_confirm_{booking_id}",
        )])
    buttons.append([legacy.InlineKeyboardButton(
        text="📜 История", callback_data=f"admin_booking_history_{booking_id}"
    )])
    buttons.append([legacy.InlineKeyboardButton(text="🔙 К разделам", callback_data="admin_bookings")])
    await call.message.edit_text(text, reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons))


async def _finalize_admin_outcome(app, legacy, call, booking_id: int, outcome: str) -> None:
    booking = await _db.get_booking(booking_id)
    if not booking:
        await call.message.edit_text("Занятие не найдено.")
        return
    if booking.get("status") == "paid" and not _booking_has_ended(legacy, booking):
        await legacy.safe_answer(call, "Занятие ещё не закончилось.", show_alert=True)
        return
    if booking.get("status") not in {"paid", "completed"}:
        await legacy.safe_answer(call, "Этот статус нельзя финализировать.", show_alert=True)
        return

    if booking.get("status") == "paid":
        await _mark_completed(booking_id, call.from_user.id, outcome=outcome)
    result = await _finalize_completed_booking(booking_id)

    if outcome == "late_student_cancel":
        prefix = "✅ Поздняя отмена/неявка засчитана как использованное занятие."
    else:
        prefix = "✅ Проведение подтверждено."
    if result.get("ok"):
        note = f"{prefix} Закрывающий чек принят банком."
    elif result.get("already_sent"):
        note = f"{prefix} Закрывающий чек уже был отправлен."
    else:
        note = (
            f"{prefix} Закрывающий чек требует проверки: "
            f"{result.get('reason') or result.get('status') or 'неизвестный статус'}."
        )
    await call.message.answer(note)
    await app._show_admin_booking(call, booking_id)


def install_telegram_completion_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_manual_completion_installed", False):
        return

    # Bot_test._cleanup_with_unpaid_autocancel reads this module global at runtime.
    async def trial_cleanup_for_app():
        return await _trial_only_cleanup(legacy)

    if hasattr(app, "_original_cleanup_old_bookings"):
        app._original_cleanup_old_bookings = trial_cleanup_for_app

    # Helpers used by our renderer live in Bot_test, expose them to legacy namespace
    # because other compatibility code also resolves these names there.
    if hasattr(app, "format_dt"):
        legacy.format_dt = app.format_dt

    async def show_with_complete(call, booking_id: int):
        return await _render_admin_booking_with_completion(legacy, call, booking_id)

    app._show_admin_booking = show_with_complete

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^admin_booking_complete_\d+$"))
    async def admin_booking_complete(call: legacy.CallbackQuery):
        await legacy.safe_answer(call)
        if call.from_user.id != legacy.ADMING_ID:
            await legacy.safe_answer(call, "⛔ Только администратор", show_alert=True)
            return
        booking_id = int(call.data.rsplit("_", 1)[1])
        await _finalize_admin_outcome(app, legacy, call, booking_id, "held")

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^admin_booking_late_cancel_\d+$"))
    async def admin_booking_late_cancel(call: legacy.CallbackQuery):
        await legacy.safe_answer(call)
        if call.from_user.id != legacy.ADMING_ID:
            await legacy.safe_answer(call, "⛔ Только администратор", show_alert=True)
            return
        booking_id = int(call.data.rsplit("_", 1)[1])
        await _finalize_admin_outcome(app, legacy, call, booking_id, "late_student_cancel")

    legacy._manual_completion_installed = True


def install_vk_completion_hardening(app) -> None:
    """VK has no admin completion UI; only disable regular time-based completion."""
    legacy = app.legacy
    if getattr(legacy, "_manual_completion_vk_installed", False):
        return

    async def trial_cleanup_for_vk():
        return await _trial_only_cleanup(legacy)

    legacy.cleanup_old_bookings = trial_cleanup_for_vk
    legacy._manual_completion_vk_installed = True
