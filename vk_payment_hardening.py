"""Replace legacy VK phone/card pseudo-payment menu with booking-linked T-Bank flow."""

from __future__ import annotations

import database as _db

_runtime_legacy = None


async def _render_payment_menu(legacy, user_id: int, *, message=None, event=None):
    bookings = await _db.get_bookings_for_account(
        "vk", int(user_id), statuses={"confirmed"}
    )
    rows = [
        (bid, booking) for bid, booking in bookings.items()
        if booking.get("booking_type") != "trial"
    ]
    kb = legacy.Keyboard(inline=True)
    if not rows:
        kb.add(legacy.Callback("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
        text = "У вас нет занятий, ожидающих оплаты."
    else:
        tutors = await legacy.get_all_tutors()
        lines = ["Выберите занятие для оплаты через Т-Банк:"]
        for bid, booking in rows:
            tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Преподаватель")
            lines.append(
                f"\n👨‍🏫 {tutor_name}\n📚 {booking['subject']}\n"
                f"📅 {booking['date']} {booking['time_slot']}"
            )
            kb.add(legacy.Callback(
                f"💳 Оплатить: {tutor_name} {booking['date']}",
                payload={"cmd": "qr", "booking_id": int(bid)},
            ))
            kb.row()
        kb.add(legacy.Callback("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
        text = "\n".join(lines)

    if event is not None:
        await legacy.edit_event_message(event, text, keyboard=kb.get_json())
    else:
        await message.answer(text, keyboard=kb.get_json())


async def _vk_payment_oplata(message):
    """Closure-free code for the already-registered VK payment-menu handler."""
    await _vk_render_payment_menu(message.from_id, message=message)


async def _vk_payment_back_to_pay(event):
    legacy = _runtime_legacy
    if legacy is None:
        return
    await legacy._vk_render_payment_menu(event.user_id, event=event)


async def _vk_payment_qr(event):
    legacy = _runtime_legacy
    if legacy is None:
        return
    raw_bid = event.payload.get("booking_id") if event.payload else None
    try:
        booking_id = int(raw_bid)
    except (TypeError, ValueError):
        # Old cached QR button: return to the safe booking-linked list.
        await legacy._vk_render_payment_menu(event.user_id, event=event)
        return

    booking = await _db.get_booking(booking_id)
    if not booking or booking.get("status") != "confirmed":
        await legacy.answer_event(event, "Запись не найдена или уже оплачена.", snackbar=True)
        return
    if not await _db.account_owns_booking("vk", event.user_id, booking):
        await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
        return

    email = await _db.get_student_email("vk", event.user_id)
    if not email:
        await legacy.set_pending_email_request(event.user_id, booking_id)
        await legacy.edit_event_message(
            event,
            "📧 Для создания чека введите ваш e-mail следующим сообщением.\n"
            "После ввода будет создана ссылка оплаты именно для этого занятия.",
        )
        return

    await legacy.create_and_send_payment(event, booking, email, booking_id)


async def _vk_payment_deprecated_method(event):
    legacy = _runtime_legacy
    if legacy is None:
        return
    await legacy.answer_event(
        event,
        "Старый способ оплаты отключён. Выберите конкретное занятие для оплаты через Т-Банк.",
        snackbar=True,
    )
    await legacy._vk_render_payment_menu(event.user_id, event=event)


def install_vk_payment_hardening(app) -> None:
    global _runtime_legacy
    legacy_module = app.legacy
    if getattr(legacy_module, "_vk_payment_hardening_installed", False):
        return
    _runtime_legacy = legacy_module

    async def render_for_legacy(user_id: int, message=None, event=None):
        return await _render_payment_menu(
            legacy_module,
            user_id,
            message=message,
            event=event,
        )

    # `oplata` was registered by decorator during legacy import, therefore preserve
    # that function object's identity and transplant only closure-free code.
    legacy_module.legacy = legacy_module
    legacy_module._db = _db
    legacy_module._vk_render_payment_menu = render_for_legacy
    if legacy_module.oplata.__code__.co_freevars or _vk_payment_oplata.__code__.co_freevars:
        raise RuntimeError("VK payment menu replacement cannot use closures")
    legacy_module.oplata.__code__ = _vk_payment_oplata.__code__

    # The universal callback dispatcher resolves these module globals dynamically.
    legacy_module.back_to_pay = _vk_payment_back_to_pay
    legacy_module.qr = _vk_payment_qr
    legacy_module.card = _vk_payment_deprecated_method
    legacy_module.sbp = _vk_payment_deprecated_method
    legacy_module._vk_payment_hardening_installed = True
