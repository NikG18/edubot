"""Single cross-platform tutor-confirmation flow.

Legacy confirmation assumed the student's messenger was the same messenger where
the tutor clicked Confirm. That breaks linked Telegram/VK usage and can even create
an aiogram FSM context for a numeric VK id. This layer always routes student data
from the booking's own ``user_platform``.
"""

from __future__ import annotations

import database as _db
import subscription_booking as subscription_flow
import student_account_hardening as accounts


def _clean_trial_subject(subject: str) -> str:
    return str(subject or "").removeprefix("Пробное: ")


async def _confirm_trial(legacy, booking: dict, actor_id: int) -> bool:
    changed, current = await _db.change_booking_status(
        int(booking["id"]),
        "confirmed",
        event_type="confirmed",
        actor_type="tutor",
        actor_id=int(actor_id),
        reason="Бесплатное пробное подтверждено преподавателем",
        expected_statuses={"pending"},
    )
    if not changed:
        return False
    current = current or booking
    await legacy.send_to_user(
        current["user_id"],
        current.get("user_platform", "telegram"),
        "✅ Бесплатное пробное занятие подтверждено!\n"
        f"📚 {_clean_trial_subject(current['subject'])}\n"
        f"📅 {current['date']} 🕒 {current['time_slot']}\n\n"
        "Оплата не требуется.",
    )
    await _db._sync_booking_record_safely(int(current["id"]))
    return True


async def _request_email(legacy, booking: dict) -> None:
    platform = str(booking.get("user_platform") or "telegram").lower()
    user_id = int(booking["user_id"])
    await accounts.set_pending_email_request(platform, user_id, int(booking["id"]))
    await legacy.send_to_user(
        user_id,
        platform,
        "📧 Для завершения записи и получения чека введите ваш адрес электронной почты "
        "обычным текстовым сообщением.",
    )


async def _confirm_regular(legacy, booking: dict, actor_id: int, *, telegram_bot=None, source=None):
    booking_id = int(booking["id"])

    reserved = await subscription_flow._confirm_from_subscription(legacy, booking_id, int(actor_id))
    if reserved is not None:
        await legacy.send_to_user(
            booking["user_id"],
            booking.get("user_platform", "telegram"),
            "✅ Занятие подтверждено преподавателем и оплачено из абонемента.\n"
            f"📚 {booking['subject']}\n📅 {booking['date']} 🕒 {booking['time_slot']}\n"
            f"Осталось занятий в этом абонементе: {reserved['remaining_lessons']}.",
        )
        await _db._sync_booking_record_safely(booking_id)
        return {"kind": "subscription", "remaining": reserved["remaining_lessons"]}

    platform = str(booking.get("user_platform") or "telegram").lower()
    email = await _db.get_student_email(platform, int(booking["user_id"]))
    if not email:
        await _request_email(legacy, booking)
        return {"kind": "email_required"}

    if telegram_bot is not None:
        delivery = await legacy.create_and_send_payment(source, telegram_bot, booking, email, booking_id)
    else:
        delivery = await legacy.create_and_send_payment(source, booking, email, booking_id)

    if isinstance(delivery, dict):
        if delivery.get("payment_id") and delivery.get("link_sent"):
            return {"kind": "payment_created"}
        if delivery.get("payment_id"):
            return {"kind": "payment_created_link_pending"}
        return {"kind": "payment_failed"}

    # Compatibility fallback for an older payment layer.
    refreshed = await _db.get_booking(booking_id)
    if refreshed and refreshed.get("tinkoff_payment_id"):
        return {"kind": "payment_created_link_pending"}
    return {"kind": "payment_failed"}


async def _telegram_confirm(call, bot, state):
    await safe_answer(call)
    try:
        booking_id = int(call.data.rsplit("_", 1)[1])
    except (AttributeError, TypeError, ValueError, IndexError):
        await call.message.edit_text("Некорректная кнопка подтверждения.")
        return
    booking = await _confirmation_db.get_booking(booking_id)
    if not booking:
        await call.message.edit_text("Заявка не найдена.")
        return
    if not await _require_booking_tutor(call, booking):
        return
    if booking.get("status") != "pending":
        await call.message.edit_text("Заявка уже обработана.")
        return

    if booking.get("booking_type") == "trial":
        changed = await _confirmation_confirm_trial(legacy, booking, call.from_user.id)
        await state.clear()
        await call.message.edit_text(
            "✅ Бесплатное пробное занятие подтверждено. Оплата не требуется."
            if changed else "Заявка уже обработана."
        )
        return

    result = await _confirmation_confirm_regular(
        legacy,
        booking,
        call.from_user.id,
        telegram_bot=bot,
        source=call,
    )
    await state.clear()
    if result["kind"] == "subscription":
        await call.message.edit_text(
            "✅ Занятие подтверждено. Оплата списана из абонемента.\n"
            f"Остаток: {result['remaining']} занятий."
        )
    elif result["kind"] == "email_required":
        await call.message.edit_text("✅ Заявка подтверждена. Ожидаем e-mail ученика для создания платежа.")
    elif result["kind"] == "payment_created":
        await call.message.edit_text("✅ Заявка подтверждена. Ссылка на оплату отправлена ученику.")
    elif result["kind"] == "payment_created_link_pending":
        await call.message.edit_text(
            "⚠️ Платёж создан, но ссылку ученику доставить не удалось. "
            "Новый платёж создавать не нужно: ученик может снова открыть раздел «Оплата» и получить ссылку на тот же PaymentId."
        )
    else:
        await call.message.edit_text(
            "⚠️ Заявка подтверждена, но платёж создать не удалось. "
            "Ученик может повторить оплату из раздела «Оплата»."
        )


def install_telegram_tutor_confirmation(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_cross_platform_tutor_confirmation_tg", False):
        return
    target = legacy.tutor_confirm_booking
    legacy._confirmation_db = _db
    legacy._confirmation_confirm_trial = _confirm_trial
    legacy._confirmation_confirm_regular = _confirm_regular
    legacy.legacy = legacy
    if target.__code__.co_freevars or _telegram_confirm.__code__.co_freevars:
        raise RuntimeError("Telegram confirmation replacement cannot use closures")
    target.__code__ = _telegram_confirm.__code__
    legacy._cross_platform_tutor_confirmation_tg = True


def install_vk_tutor_confirmation(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_cross_platform_tutor_confirmation_vk", False):
        return

    async def vk_confirm(event):
        try:
            booking_id = int(event.payload["cmd"].rsplit("_", 1)[1])
        except (KeyError, TypeError, ValueError, IndexError):
            await legacy.answer_event(event, "Некорректная кнопка подтверждения.", snackbar=True)
            return
        booking = await _db.get_booking(booking_id)
        if not booking:
            await legacy.edit_event_message(event, "Заявка не найдена.")
            return
        tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
        if tutor_id != booking.get("tutor_id"):
            await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
            return
        if booking.get("status") != "pending":
            await legacy.edit_event_message(event, "Заявка уже обработана.")
            return

        if booking.get("booking_type") == "trial":
            changed = await _confirm_trial(legacy, booking, event.user_id)
            await legacy.edit_event_message(
                event,
                "✅ Бесплатное пробное занятие подтверждено. Оплата не требуется."
                if changed else "Заявка уже обработана.",
            )
            return

        result = await _confirm_regular(
            legacy,
            booking,
            event.user_id,
            telegram_bot=None,
            source=event,
        )
        if result["kind"] == "subscription":
            await legacy.edit_event_message(
                event,
                "✅ Занятие подтверждено. Оплата списана из абонемента.\n"
                f"Остаток: {result['remaining']} занятий.",
            )
        elif result["kind"] == "email_required":
            await legacy.edit_event_message(event, "✅ Заявка подтверждена. Ожидаем e-mail ученика для создания платежа.")
        elif result["kind"] == "payment_created":
            await legacy.edit_event_message(event, "✅ Заявка подтверждена. Ссылка на оплату отправлена ученику.")
        elif result["kind"] == "payment_created_link_pending":
            await legacy.edit_event_message(
                event,
                "⚠️ Платёж создан, но ссылку ученику доставить не удалось. "
                "Новый платёж создавать не нужно: ученик может снова открыть раздел «Оплата» и получить ссылку на тот же PaymentId.",
            )
        else:
            await legacy.edit_event_message(
                event,
                "⚠️ Заявка подтверждена, но платёж создать не удалось. "
                "Ученик может повторить оплату из раздела «Оплата».",
            )

    legacy.tutor_confirm_booking = vk_confirm
    app.tutor_confirm_booking = vk_confirm
    legacy._cross_platform_tutor_confirmation_vk = True
