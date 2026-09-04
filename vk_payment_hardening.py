"""Booking-linked T-Bank payments and subscription purchases for VK."""

from __future__ import annotations

import database as _db
from pricing import SUBSCRIPTION_PACKAGES, subscription_total_rub
from subscription_purchase_hardening import create_or_reuse_subscription_payment

_runtime_legacy = None


def _subject_by_index(tutor: dict | None, index: int) -> str | None:
    if not tutor:
        return None
    names = list((tutor.get("subjects") or {}).keys())
    return names[index] if 0 <= int(index) < len(names) else None


async def _render_payment_menu(legacy, user_id: int, *, message=None, event=None):
    bookings = await _db.get_bookings_for_account(
        "vk", int(user_id), statuses={"confirmed"}
    )
    rows = [
        (bid, booking) for bid, booking in bookings.items()
        if booking.get("booking_type") != "trial"
    ]
    kb = legacy.Keyboard(inline=True)
    lines = []
    if rows:
        tutors = await legacy.get_all_tutors()
        lines.append("Выберите занятие для оплаты через Т-Банк:")
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
    else:
        lines.append("У вас нет занятий, ожидающих отдельной оплаты.")

    kb.add(legacy.Callback(
        "🎟 Купить абонемент",
        payload={"cmd": "qr", "action": "subscription_start"},
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


async def _subscription_start(legacy, event):
    email = await _db.get_student_email("vk", int(event.user_id))
    if not email:
        await legacy.set_pending_email_request(
            int(event.user_id),
            int(getattr(legacy, "SUBSCRIPTION_EMAIL_MARKER", -2)),
        )
        await legacy.edit_event_message(
            event,
            "📧 Сначала отправьте e-mail для кассового чека следующим сообщением.\n"
            "После сохранения снова откройте «💳 Оплата» → «🎟 Купить абонемент»."
        )
        return

    tutors = await legacy.get_all_tutors()
    kb = legacy.Keyboard(inline=True)
    for tutor_id, tutor in tutors.items():
        if not (tutor.get("subjects") or {}):
            continue
        kb.add(legacy.Callback(
            tutor["name"],
            payload={"cmd": "qr", "action": "subscription_tutor", "tutor_id": int(tutor_id)},
        ))
        kb.row()
    kb.add(legacy.Callback("🔙 К оплате", payload={"cmd": "back_to_pay"}))
    await legacy.edit_event_message(event, "Выберите преподавателя для абонемента:", keyboard=kb.get_json())


async def _subscription_tutor(legacy, event):
    try:
        tutor_id = int(event.payload.get("tutor_id"))
    except (TypeError, ValueError):
        await legacy.answer_event(event, "Кнопка устарела.", snackbar=True)
        return
    tutors = await legacy.get_all_tutors()
    tutor = tutors.get(tutor_id)
    if not tutor:
        await legacy.answer_event(event, "Преподаватель не найден.", snackbar=True)
        return
    subjects = list((tutor.get("subjects") or {}).items())
    if not subjects:
        await legacy.answer_event(event, "У преподавателя нет доступных предметов.", snackbar=True)
        return

    kb = legacy.Keyboard(inline=True)
    for index, (subject, price) in enumerate(subjects):
        kb.add(legacy.Callback(
            f"{subject} ({price} ₽)",
            payload={
                "cmd": "qr", "action": "subscription_subject",
                "tutor_id": tutor_id, "subject_index": index,
            },
        ))
        kb.row()
    kb.add(legacy.Callback(
        "🔙 К преподавателям",
        payload={"cmd": "qr", "action": "subscription_start"},
    ))
    await legacy.edit_event_message(
        event,
        f"Выберите предмет у {tutor['name']}:",
        keyboard=kb.get_json(),
    )


async def _subscription_subject(legacy, event):
    try:
        tutor_id = int(event.payload.get("tutor_id"))
        subject_index = int(event.payload.get("subject_index"))
    except (TypeError, ValueError):
        await legacy.answer_event(event, "Кнопка устарела.", snackbar=True)
        return
    tutors = await legacy.get_all_tutors()
    tutor = tutors.get(tutor_id)
    subject = _subject_by_index(tutor, subject_index)
    if not subject:
        await legacy.answer_event(event, "Предмет больше недоступен.", snackbar=True)
        return
    price = tutor["subjects"][subject]

    kb = legacy.Keyboard(inline=True)
    lines = [f"Абонемент по предмету «{subject}»:"]
    for count, discount in SUBSCRIPTION_PACKAGES:
        total = subscription_total_rub(price, count)
        lines.append(f"• {count} занятий — скидка {discount}% — {total} ₽")
        kb.add(legacy.Callback(
            f"{count} занятий · −{discount}%",
            payload={
                "cmd": "qr", "action": "subscription_package",
                "tutor_id": tutor_id, "subject_index": subject_index, "count": count,
            },
        ))
        kb.row()
    kb.add(legacy.Callback(
        "🔙 К предметам",
        payload={"cmd": "qr", "action": "subscription_tutor", "tutor_id": tutor_id},
    ))
    await legacy.edit_event_message(event, "\n".join(lines), keyboard=kb.get_json())


async def _subscription_package(legacy, event):
    try:
        tutor_id = int(event.payload.get("tutor_id"))
        subject_index = int(event.payload.get("subject_index"))
        count = int(event.payload.get("count"))
    except (TypeError, ValueError):
        await legacy.answer_event(event, "Кнопка устарела.", snackbar=True)
        return
    package = dict(SUBSCRIPTION_PACKAGES)
    if count not in package:
        await legacy.answer_event(event, "Такого пакета нет.", snackbar=True)
        return
    tutors = await legacy.get_all_tutors()
    tutor = tutors.get(tutor_id)
    subject = _subject_by_index(tutor, subject_index)
    if not tutor or not subject:
        await legacy.answer_event(event, "Данные покупки устарели.", snackbar=True)
        return
    total = subscription_total_rub(tutor["subjects"][subject], count)
    discount = package[count]

    kb = legacy.Keyboard(inline=True)
    kb.add(legacy.Callback(
        "✅ Создать оплату",
        payload={
            "cmd": "qr", "action": "subscription_confirm",
            "tutor_id": tutor_id, "subject_index": subject_index, "count": count,
        },
    ))
    kb.row()
    kb.add(legacy.Callback(
        "🔙 К пакетам",
        payload={
            "cmd": "qr", "action": "subscription_subject",
            "tutor_id": tutor_id, "subject_index": subject_index,
        },
    ))
    await legacy.edit_event_message(
        event,
        f"Проверьте покупку:\n👨‍🏫 {tutor['name']}\n📚 {subject}\n"
        f"🎟 {count} занятий\n💸 Скидка {discount}%\n💰 К оплате {total} ₽",
        keyboard=kb.get_json(),
    )


async def _subscription_confirm(legacy, event):
    try:
        tutor_id = int(event.payload.get("tutor_id"))
        subject_index = int(event.payload.get("subject_index"))
        count = int(event.payload.get("count"))
    except (TypeError, ValueError):
        await legacy.answer_event(event, "Кнопка устарела.", snackbar=True)
        return

    tutors = await legacy.get_all_tutors()
    tutor = tutors.get(tutor_id)
    subject = _subject_by_index(tutor, subject_index)
    if not tutor or not subject:
        await legacy.answer_event(event, "Данные покупки устарели.", snackbar=True)
        return
    email = await _db.get_student_email("vk", int(event.user_id))
    if not email:
        await legacy.set_pending_email_request(
            int(event.user_id),
            int(getattr(legacy, "SUBSCRIPTION_EMAIL_MARKER", -2)),
        )
        await legacy.edit_event_message(
            event,
            "📧 E-mail для чека не найден. Отправьте его следующим сообщением, "
            "затем снова откройте покупку абонемента.",
        )
        return

    result = await create_or_reuse_subscription_payment(
        platform="vk",
        platform_user_id=int(event.user_id),
        tutor_id=tutor_id,
        subject=subject,
        lessons_count=count,
        customer_email=email,
    )
    if not result.get("ok"):
        reason_messages = {
            "tutor_phone_missing": "У преподавателя не заполнен телефон для кассового чека.",
            "tutor_phone_unavailable": "Не удалось проверить телефон преподавателя для кассового чека.",
            "tutor_inn_missing": "У преподавателя не заполнен ИНН.",
            "payment_init_failed": "Т-Банк не создал платёж.",
            "duplicate_pending_payments": "Найдены дубли ожидающих платежей; требуется проверка администратора.",
        }
        await legacy.edit_event_message(
            event,
            "⚠️ Не удалось создать оплату абонемента. "
            f"{reason_messages.get(result.get('reason'), 'Проверьте данные покупки.')} "
            "Обратитесь в поддержку.",
        )
        return
    if result.get("activated"):
        await legacy.edit_event_message(event, "✅ Этот абонемент уже оплачен и активирован.")
        return
    payment_url = result.get("payment_url")
    if not payment_url:
        await legacy.edit_event_message(
            event,
            "Платёж уже создан, но Т-Банк временно не вернул ссылку СБП. "
            "Повторите этот шаг позже — новый платёж создан не будет.",
        )
        return

    kb = legacy.Keyboard(inline=True)
    kb.add(legacy.OpenLink("💳 Оплатить через СБП", payment_url))
    kb.row()
    kb.add(legacy.Callback("🔙 К оплате", payload={"cmd": "back_to_pay"}))
    await legacy.edit_event_message(
        event,
        f"💳 Абонемент на {result['lessons_count']} занятий · {result['total_rub']} ₽\n"
        "Нажмите кнопку ниже для оплаты через СБП.",
        keyboard=kb.get_json(),
    )


async def _vk_payment_qr(event):
    legacy = _runtime_legacy
    if legacy is None:
        return
    payload = event.payload or {}
    action = payload.get("action")
    if action == "subscription_start":
        return await _subscription_start(legacy, event)
    if action == "subscription_tutor":
        return await _subscription_tutor(legacy, event)
    if action == "subscription_subject":
        return await _subscription_subject(legacy, event)
    if action == "subscription_package":
        return await _subscription_package(legacy, event)
    if action == "subscription_confirm":
        return await _subscription_confirm(legacy, event)

    raw_bid = payload.get("booking_id")
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
        "Старый способ оплаты отключён. Используйте оплату конкретного занятия или покупку абонемента.",
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
