"""Last-line protection for ordinary lesson payments started from email handlers.

The normal payment menu and tutor-confirmation flow already resolve trials and
subscription coverage before creating a T-Bank payment. Email collection has two
Telegram paths (FSM + durable fallback) and one VK path; historically those paths
could call ``create_and_send_payment`` directly after the user supplied an email.

This layer wraps the final idempotent payment creator and repeats the trial/package
check for message-originated calls. Callback/event-originated flows keep their
existing dedicated guards. This prevents a package that was bought/activated while
an email prompt was outstanding from being bypassed by a full-price lesson payment.
"""

from __future__ import annotations

import database as db
from booking_visibility_rules import is_trial_booking
from subscription_resolution import PENDING_PACKAGE_ERRORS, resolve_booking_subscription


def _blocked_text(reason: str | None, booking_id: int) -> str:
    if reason in PENDING_PACKAGE_ERRORS:
        return (
            "🎟 Для этого занятия уже оформляется абонемент, но его платёж ещё не "
            "подтверждён. Отдельная оплата полной стоимости не создаётся. После "
            "подтверждения абонемента откройте раздел «Оплата» ещё раз."
        )
    if reason == "ordinary_payment_already_created":
        return (
            "⚠️ Для этого занятия ранее уже был создан отдельный платёж T-Банк, а "
            "теперь найден подходящий абонемент. Автоматически переключать занятие "
            "на абонемент нельзя: старая платёжная ссылка может оставаться действующей. "
            f"Чтобы исключить двойную оплату, занятие #{booking_id} заблокировано до "
            "проверки администратором."
        )
    return (
        "⚠️ Для этого занятия найден абонемент, требующий проверки. Чтобы исключить "
        f"двойную оплату, отдельный платёж за занятие #{booking_id} не создан. "
        "Обратитесь в поддержку."
    )


async def _telegram_message_preflight(legacy, source, booking_id: int):
    if not isinstance(source, legacy.types.Message):
        return None
    current = await db.get_booking(int(booking_id))
    if not current:
        return None
    if is_trial_booking(current):
        await source.answer("🎓 Пробное занятие бесплатно и не требует оплаты.")
        return {"blocked": True, "reason": "trial_booking"}
    if current.get("status") not in {"pending", "confirmed"}:
        return None

    # Always ask the package resolver, even when an individual PaymentId already
    # exists. It returns None when there is no package conflict (allowing the
    # idempotent payment layer to reissue the same PaymentId), and fails closed if
    # a package now also exists for this booking.
    resolved = await resolve_booking_subscription(
        legacy,
        int(booking_id),
        int(source.from_user.id),
        actor_type="student",
        allowed_statuses={str(current["status"])},
    )
    if resolved is None:
        return None
    if resolved.get("error"):
        await source.answer(_blocked_text(resolved.get("error"), int(booking_id)))
        return {
            "blocked": True,
            "reason": str(resolved.get("error") or "subscription_check_failed"),
            "subscription_blocked": True,
        }

    await source.answer(
        "✅ Это занятие оплачено из вашего абонемента.\n"
        f"Осталось занятий: {resolved.get('remaining_lessons', 0)}."
    )
    return {
        "blocked": True,
        "reason": "subscription_reserved",
        "subscription_reserved": True,
        "remaining_lessons": int(resolved.get("remaining_lessons", 0)),
    }


async def _vk_message_preflight(legacy, source, booking_id: int):
    if not isinstance(source, legacy.Message):
        return None
    current = await db.get_booking(int(booking_id))
    if not current:
        return None
    if is_trial_booking(current):
        await source.answer("🎓 Пробное занятие бесплатно и не требует оплаты.")
        return {"blocked": True, "reason": "trial_booking"}
    if current.get("status") not in {"pending", "confirmed"}:
        return None

    actor_id = int(source.from_id)
    resolved = await resolve_booking_subscription(
        legacy,
        int(booking_id),
        actor_id,
        actor_type="student",
        allowed_statuses={str(current["status"])},
    )
    if resolved is None:
        return None
    if resolved.get("error"):
        await source.answer(_blocked_text(resolved.get("error"), int(booking_id)))
        return {
            "blocked": True,
            "reason": str(resolved.get("error") or "subscription_check_failed"),
            "subscription_blocked": True,
        }

    await source.answer(
        "✅ Это занятие оплачено из вашего абонемента.\n"
        f"Осталось занятий: {resolved.get('remaining_lessons', 0)}."
    )
    return {
        "blocked": True,
        "reason": "subscription_reserved",
        "subscription_reserved": True,
        "remaining_lessons": int(resolved.get("remaining_lessons", 0)),
    }


def install_telegram_email_payment_safety(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_telegram_email_payment_safety_installed", False):
        return
    original = legacy.create_and_send_payment

    async def guarded(source, bot, booking, email, booking_id):
        result = await _telegram_message_preflight(legacy, source, int(booking_id))
        if result is not None:
            return result
        return await original(source, bot, booking, email, booking_id)

    legacy.create_and_send_payment = guarded
    if hasattr(app, "create_and_send_payment"):
        app.create_and_send_payment = guarded
    legacy._telegram_email_payment_safety_installed = True


def install_vk_email_payment_safety(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_vk_email_payment_safety_installed", False):
        return
    original = legacy.create_and_send_payment

    async def guarded(source, booking, email, booking_id):
        result = await _vk_message_preflight(legacy, source, int(booking_id))
        if result is not None:
            return result
        return await original(source, booking, email, booking_id)

    legacy.create_and_send_payment = guarded
    if hasattr(app, "create_and_send_payment"):
        app.create_and_send_payment = guarded
    legacy._vk_email_payment_safety_installed = True
