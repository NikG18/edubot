"""Replace legacy VK phone/card pseudo-payment menu with booking-linked T-Bank flow."""

from __future__ import annotations

import database as _db


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


def install_vk_payment_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_vk_payment_hardening_installed", False):
        return

    async def patched_oplata(message):
        await legacy._vk_render_payment_menu(message.from_id, message=message)

    async def patched_back_to_pay(event):
        await legacy._vk_render_payment_menu(event.user_id, event=event)

    async def patched_qr(event):
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
            # Preserve the existing VK free-text e-mail handler. It will store the
            # address in both old compatibility storage and the shared student profile.
            await legacy.set_pending_email_request(event.user_id, booking_id)
            await legacy.edit_event_message(
                event,
                "📧 Для создания чека введите ваш e-mail следующим сообщением.\n"
                "После ввода будет создана ссылка оплаты именно для этого занятия.",
            )
            return

        await legacy.create_and_send_payment(event, booking, email, booking_id)

    async def deprecated_method(event):
        await legacy.answer_event(
            event,
            "Старый способ оплаты отключён. Выберите конкретное занятие для оплаты через Т-Банк.",
            snackbar=True,
        )
        await legacy._vk_render_payment_menu(event.user_id, event=event)

    # `oplata` was registered by decorator during legacy import, therefore change its
    # code object. Callback dispatcher resolves back_to_pay/qr/card/sbp globals at run time.
    legacy.legacy = legacy
    legacy._db = _db
    legacy._vk_render_payment_menu = lambda user_id, message=None, event=None: _render_payment_menu(
        legacy, user_id, message=message, event=event
    )
    legacy.oplata.__code__ = patched_oplata.__code__
    legacy.back_to_pay = patched_back_to_pay
    legacy.qr = patched_qr
    legacy.card = deprecated_method
    legacy.sbp = deprecated_method
    legacy._vk_payment_hardening_installed = True
