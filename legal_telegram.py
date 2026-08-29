import database as _db
import Bot_test_legacy as legacy

from legal_common import (
    DOCS,
    STUDENT_DOC_TYPES,
    TUTOR_DOC_TYPES,
    install_payment_acceptance_hook,
    install_subscription_acceptance_hook,
    record_student_docs_presented,
)


def _url_keyboard(doc_types):
    rows = []
    for doc_type in doc_types:
        doc = DOCS[doc_type]
        rows.append([
            legacy.InlineKeyboardButton(text=f"📄 {doc['title']}", url=doc["url"])
        ])
    rows.append([
        legacy.InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu")
    ])
    return legacy.InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_student_legal_before_payment(user_id: int, booking_id=None):
    await record_student_docs_presented(user_id, "telegram", booking_id)
    keyboard = _url_keyboard(("student_offer", "privacy_policy"))
    await legacy.send_to_user(
        user_id,
        "telegram",
        "Перед оплатой ознакомьтесь с публичной офертой и Политикой обработки персональных данных. "
        "Оплата конкретного заказа является акцептом оферты в редакции, указанной в документах.",
        reply_markup_tg=keyboard.model_dump_json(),
    )


_original_get_main_menu = legacy.get_main_menu


async def _legal_get_main_menu(user_id: int):
    keyboard = await _original_get_main_menu(user_id)
    if not any(
        button.text == "📄 Документы"
        for row in keyboard.keyboard
        for button in row
    ):
        keyboard.keyboard.append([legacy.KeyboardButton(text="📄 Документы")])
    return keyboard


legacy.get_main_menu = _legal_get_main_menu


@legacy.dp.message(legacy.F.text == "📄 Документы")
async def legal_documents_menu(message):
    tutor_id = await legacy.get_tutor_by_telegram_id(message.from_user.id)
    is_tutor = tutor_id is not None and message.from_user.id != legacy.ADMING_ID
    doc_types = TUTOR_DOC_TYPES if is_tutor else STUDENT_DOC_TYPES
    intro = (
        "📄 Документы репетитора\n\n"
        "Первичный акцепт агентского договора и отдельные согласия репетитора оформляются "
        "через Яндекс Форму. Здесь всегда доступны актуальные редакции."
        if is_tutor else
        "📄 Юридические документы\n\n"
        "Здесь доступны актуальные документы для пользователя Сервиса."
    )
    await message.answer(intro, reply_markup=_url_keyboard(doc_types))


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


async def _legal_create_and_send_payment(source, bot, booking, email, booking_id):
    try:
        await _send_student_legal_before_payment(booking["user_id"], booking_id)
    except Exception:
        legacy.logging.exception("Не удалось показать документы перед оплатой booking=%s", booking_id)
    return await _original_create_and_send_payment(source, bot, booking, email, booking_id)


legacy.create_and_send_payment = _legal_create_and_send_payment


if hasattr(legacy, "create_subscription_payment"):
    _original_create_subscription_payment = legacy.create_subscription_payment

    async def _legal_create_subscription_payment(
        source, bot, user_id, tutor_id, subject, count, total, discount, email, user_platform
    ):
        try:
            await _send_student_legal_before_payment(user_id, None)
        except Exception:
            legacy.logging.exception("Не удалось показать документы перед оплатой абонемента")
        return await _original_create_subscription_payment(
            source, bot, user_id, tutor_id, subject, count, total, discount, email, user_platform
        )

    legacy.create_subscription_payment = _legal_create_subscription_payment


_paid_hook = install_payment_acceptance_hook()
legacy.mark_booking_paid_once = _paid_hook
_subscription_hook = install_subscription_acceptance_hook()
legacy.activate_subscription = _subscription_hook
