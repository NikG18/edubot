import json

import vk_bot_legacy as legacy

from legal_common import (
    DOCS,
    STUDENT_DOC_TYPES,
    TUTOR_DOC_TYPES,
    install_payment_acceptance_hook,
    install_subscription_acceptance_hook,
    record_student_docs_presented,
)


def _docs_keyboard(doc_types):
    kb = legacy.Keyboard(inline=True)
    for doc_type in doc_types:
        doc = DOCS[doc_type]
        kb.add(legacy.OpenLink(doc["title"], doc["url"]))
        kb.row()
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


@legacy.bot.on.private_message(text="📄 Документы")
async def legal_documents_menu(message):
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


_paid_hook = install_payment_acceptance_hook()
legacy.mark_booking_paid_once = _paid_hook
_subscription_hook = install_subscription_acceptance_hook()
legacy.activate_subscription = _subscription_hook
