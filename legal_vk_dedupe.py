"""Не допускает повторной отправки оферты/Политики для одной VK-записи и редакции."""

import legal_vk
from legal_common import record_student_docs_presented


async def _deduplicated_send_student_legal_before_payment(user_id: int, booking_id=None):
    newly_presented = await record_student_docs_presented(user_id, "vk", booking_id)
    if booking_id is not None and not newly_presented:
        return

    await legal_vk.legacy.send_to_user(
        user_id,
        "vk",
        "Перед оплатой ознакомьтесь с публичной офертой и Политикой обработки персональных данных. "
        "Оплата конкретного заказа является акцептом оферты в указанной редакции.",
        keyboard_vk=legal_vk._docs_keyboard(("student_offer", "privacy_policy")),
    )


legal_vk._send_student_legal_before_payment = _deduplicated_send_student_legal_before_payment
