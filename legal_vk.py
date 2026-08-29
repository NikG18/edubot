import json

import vk_bot_legacy as legacy

from legal_common import (
    DOCS,
    STUDENT_DOC_TYPES,
    TUTOR_DOC_TYPES,
    install_payment_acceptance_hook,
    install_subscription_acceptance_hook,
    record_student_docs_presented,
    record_student_privacy_continued,
    record_student_privacy_presented,
    student_privacy_notice_completed,
)


def _docs_keyboard(doc_types):
    kb = legacy.Keyboard(inline=True)
    for doc_type in doc_types:
        doc = DOCS[doc_type]
        kb.add(legacy.OpenLink(doc["title"], doc["url"]))
        kb.row()
    return kb.get_json()


def _privacy_keyboard(continue_label: str, continue_payload: dict, back_cmd: str = "back_to_menu"):
    kb = legacy.Keyboard(inline=True)
    kb.add(legacy.OpenLink("📄 Политика обработки ПД", DOCS["privacy_policy"]["url"]))
    kb.row()
    kb.add(legacy.Callback(continue_label, payload=continue_payload))
    kb.row()
    kb.add(legacy.Callback("🔙 Назад", payload={"cmd": back_cmd}))
    return kb.get_json()


async def _send_student_legal_before_payment(user_id: int, booking_id=None):
    await record_student_docs_presented(user_id, "vk", booking_id)
    await legacy.send_to_user(
        user_id,
        "vk",
        "Перед оплатой ознакомьтесь с публичной офертой и Политикой обработки персональных данных. "
        "Оплата конкретного заказа является акцептом оферты в указанной редакции.",
        keyboard_vk=_docs_keyboard(("student_offer", "privacy_policy")),
    )


_original_get_main_menu = legacy.get_main_menu


async def _legal_get_main_menu(user_id: int) -> str:
    raw = await _original_get_main_menu(user_id)
    data = json.loads(raw)
    buttons = data.setdefault("buttons", [])
    if not any(
        button.get("action", {}).get("label") == "📄 Документы"
        for row in buttons
        for button in row
    ):
        buttons.append([{
            "action": {"type": "text", "label": "📄 Документы"},
            "color": "primary",
        }])
    return json.dumps(data, ensure_ascii=False)


legacy.get_main_menu = _legal_get_main_menu


async def _legal_documents_reply(message):
    tutor_id = await legacy.get_tutor_by_vk_id(message.from_id)
    is_tutor = tutor_id is not None and message.from_id != legacy.ADMIN_VK_ID
    doc_types = TUTOR_DOC_TYPES if is_tutor else STUDENT_DOC_TYPES
    intro = (
        "📄 Документы репетитора\n\n"
        "Первичный акцепт агентского договора и отдельные согласия репетитора оформляются "
        "через Яндекс Форму. Здесь доступны актуальные редакции."
        if is_tutor else
        "📄 Юридические документы\n\nЗдесь доступны актуальные документы пользователя."
    )
    await message.answer(intro, keyboard=_docs_keyboard(doc_types))


# Отдельный handler оставляем для новых конфигураций роутера.
@legacy.bot.on.private_message(text="📄 Документы")
async def legal_documents_menu(message):
    await _legal_documents_reply(message)


# В текущем legacy-роутере общий private_message handler обработки e-mail
# зарегистрирован раньше legal_documents_menu и перехватывает текст. Поэтому
# расширяем уже зарегистрированную функцию: документы обрабатываем первыми,
# остальную e-mail-логику сохраняем без изменений.
async def _legal_process_payment_email(message):
    if message.text == "📄 Документы":
        await _legal_documents_reply(message)
        return

    booking_id = await get_pending_email_request(message.from_id)
    if not booking_id:
        return
    email = message.text.strip()
    if not valid_email(email):
        await message.answer("Введите корректный email, например name@example.com")
        return

    await set_user_email(message.from_id, email)
    bookings = await get_all_bookings()
    booking = bookings.get(booking_id)
    if not booking:
        await message.answer("Ошибка: запись не найдена.")
        await delete_pending_email_request(message.from_id)
        return

    await create_and_send_payment(message, booking, email, booking_id)
    await delete_pending_email_request(message.from_id)


legacy._legal_documents_reply = _legal_documents_reply
legacy.process_payment_email.__code__ = _legal_process_payment_email.__code__


# -------------------- Ранний privacy-gate: обычная запись --------------------
async def _regular_booking_after_privacy(message):
    await message.answer(
        "Кто из репетиторов вас интересует?",
        keyboard=await legacy.make_tutors_keyboard("tutor_booking", back_callback="back_to_menu"),
    )
    await legacy.state_dispenser.delete(message.from_id)


async def _legal_zapis(message):
    if not await student_privacy_notice_completed(message.from_id, "vk"):
        await legacy.state_dispenser.delete(message.from_id)
        await legacy.state_dispenser.update(
            message.from_id,
            legal_privacy_context="regular_booking",
        )
        await record_student_privacy_presented(message.from_id, "vk", "regular_booking")
        await message.answer(
            "До начала записи ознакомьтесь с Политикой обработки персональных данных. "
            "Для оформления записи Сервис использует необходимые идентификаторы VK и сведения о выбранном занятии.\n\n"
            "Нажмите «Продолжить», чтобы перейти к записи.",
            keyboard=_privacy_keyboard(
                "▶️ Продолжить",
                {"cmd": "back_to_tutors_booking"},
            ),
        )
        return
    await _regular_booking_after_privacy(message)


# Зарегистрированный vkbottle-handler уже хранит исходную функцию, поэтому меняем
# только её code object и публикуем новые имена в globals legacy-модуля.
legacy.student_privacy_notice_completed = student_privacy_notice_completed
legacy.record_student_privacy_presented = record_student_privacy_presented
legacy._privacy_keyboard = _privacy_keyboard
legacy._regular_booking_after_privacy = _regular_booking_after_privacy
legacy.zapis.__code__ = _legal_zapis.__code__


_original_back_to_tutors_booking = legacy.back_to_tutors_booking


async def _legal_back_to_tutors_booking(event):
    data = await legacy.state_dispenser.get_data(event.user_id)
    if data.get("legal_privacy_context") == "regular_booking":
        await record_student_privacy_continued(event.user_id, "vk", "regular_booking")
        await legacy.state_dispenser.delete(event.user_id)
    return await _original_back_to_tutors_booking(event)


legacy.back_to_tutors_booking = _legal_back_to_tutors_booking


# -------------------- Ранний privacy-gate: пробное занятие --------------------
_original_start_trials_booking = legacy.start_trials_booking


async def _legal_start_trials_booking(event):
    user_id = event.user_id
    tid = event.payload.get("tutor_id")
    if await student_privacy_notice_completed(user_id, "vk"):
        return await _original_start_trials_booking(event)

    data = await legacy.state_dispenser.get_data(user_id)
    if (
        data.get("legal_privacy_context") == "trial_booking"
        and data.get("legal_trial_tutor_id") == tid
    ):
        await record_student_privacy_continued(user_id, "vk", "trial_booking")
        await legacy.state_dispenser.delete(user_id)
        return await _original_start_trials_booking(event)

    await legacy.state_dispenser.update(
        user_id,
        legal_privacy_context="trial_booking",
        legal_trial_tutor_id=tid,
    )
    await record_student_privacy_presented(user_id, "vk", "trial_booking")
    await legacy.edit_event_message(
        event,
        "До начала записи на пробное занятие ознакомьтесь с Политикой обработки персональных данных. "
        "Для оформления записи Сервис использует необходимые идентификаторы VK и сведения о выбранном занятии.\n\n"
        "Нажмите «Продолжить пробную запись».",
        keyboard=_privacy_keyboard(
            "▶️ Продолжить пробную запись",
            {"cmd": "trials", "tutor_id": tid},
            back_cmd="back_to_tutors",
        ),
    )


legacy.start_trials_booking = _legal_start_trials_booking


# -------------------- Документы перед оплатой --------------------
_original_set_pending_email_request = legacy.set_pending_email_request


async def _legal_set_pending_email_request(user_id: int, booking_id: int):
    try:
        await _send_student_legal_before_payment(
            user_id,
            booking_id if booking_id and booking_id > 0 else None,
        )
    except Exception:
        legacy.logging.exception("Не удалось показать документы перед запросом e-mail")
    return await _original_set_pending_email_request(user_id, booking_id)


legacy.set_pending_email_request = _legal_set_pending_email_request


_original_create_and_send_payment = legacy.create_and_send_payment


async def _legal_create_and_send_payment(source, booking, email, booking_id):
    try:
        await _send_student_legal_before_payment(booking["user_id"], booking_id)
    except Exception:
        legacy.logging.exception("Не удалось показать документы перед оплатой booking=%s", booking_id)
    return await _original_create_and_send_payment(source, booking, email, booking_id)


legacy.create_and_send_payment = _legal_create_and_send_payment


# Дополнительная страховка на точке подтверждения преподавателем. В VK старый
# callback-handler может идти через совместимый tutor_confirm_booking из vk_bot.py.
# Перед запуском обычного платёжного сценария показываем документы напрямую.
_original_tutor_confirm_booking = legacy.tutor_confirm_booking


async def _legal_tutor_confirm_booking(event):
    try:
        bid = int(event.payload["cmd"].split("_")[2])
    except (KeyError, ValueError, TypeError, IndexError):
        return await _original_tutor_confirm_booking(event)

    bookings = await legacy.get_all_bookings()
    booking = bookings.get(bid)
    is_trial = bool(booking and str(booking.get("subject") or "").startswith("Пробное: "))
    tutor_id = await legacy.get_tutor_by_vk_id(event.user_id) if booking else None

    if (
        booking
        and not is_trial
        and booking.get("status") == "pending"
        and tutor_id == booking.get("tutor_id")
    ):
        try:
            await _send_student_legal_before_payment(booking["user_id"], bid)
        except Exception:
            legacy.logging.exception("Не удалось показать документы перед подтверждением booking=%s", bid)

    return await _original_tutor_confirm_booking(event)


legacy.tutor_confirm_booking = _legal_tutor_confirm_booking


_paid_hook = install_payment_acceptance_hook()
legacy.mark_booking_paid_once = _paid_hook
_subscription_hook = install_subscription_acceptance_hook()
legacy.activate_subscription = _subscription_hook
