import json

import vk_bot as app
import legal_documents as legal

legacy = app.legacy


async def _vk_role(user_id: int) -> str:
    if user_id == legacy.ADMIN_VK_ID:
        return "admin"
    if await legacy.get_tutor_by_vk_id(user_id) is not None:
        return "tutor"
    return "student"


_original_get_main_menu = legacy.get_main_menu


async def _legal_main_menu(user_id: int) -> str:
    raw = await _original_get_main_menu(user_id)
    data = json.loads(raw)
    labels = {
        button.get("action", {}).get("label")
        for row in data.get("buttons", [])
        for button in row
    }
    if "📄 Документы" not in labels:
        data.setdefault("buttons", []).append([{
            "action": {
                "type": "text",
                "label": "📄 Документы",
                "payload": '{"legal":true}',
            },
            "color": "primary",
        }])
    return json.dumps(data, ensure_ascii=False)


legacy.get_main_menu = _legal_main_menu


def _vk_docs_keyboard(doc_types, role):
    kb = legacy.Keyboard(inline=True)
    for doc_type in doc_types:
        kb.add(legacy.OpenLink(legal.DOCS[doc_type][1][:40], legal.doc_url(doc_type)))
        kb.row()
    if role == "tutor" and legal.TUTOR_YANDEX_FORM_URL:
        kb.add(legacy.OpenLink("📝 Анкета репетитора", legal.TUTOR_YANDEX_FORM_URL))
        kb.row()
    return kb.get_json()


@legacy.bot.on.message(text=["📄 Документы", "Документы"])
async def legal_documents_menu(message: legacy.Message):
    role = await _vk_role(message.from_id)
    docs = (
        legal.ADMIN_DOCS if role == "admin"
        else legal.TUTOR_DOCS if role == "tutor"
        else legal.STUDENT_DOCS
    )
    text = "📄 Актуальные юридические документы."
    if role == "student":
        text += (
            "\n\nСогласие на дополнительные/рекламные сообщения необязательно "
            "для обычного бронирования."
        )
    elif role == "tutor":
        text += (
            "\n\nПервичные данные и отдельные согласия собираются через Яндекс "
            "Форму; повторно давать их в боте не требуется."
        )
    await message.answer(text, keyboard=_vk_docs_keyboard(docs, role))


_original_create_payment = legacy.create_payment


async def _create_payment_with_legal(*args, **kwargs):
    booking_id = kwargs.get("booking_id") or (args[0] if args else None)
    if booking_id:
        booking = await app._db.get_booking(int(booking_id))
        if booking and booking.get("user_platform") == "vk":
            uid = booking["user_id"]
            tutors = await app._db.get_all_tutors()
            tutor = tutors.get(booking["tutor_id"], {})
            await legacy.bot.api.messages.send(
                user_id=uid,
                random_id=0,
                message=(
                    "Перед оплатой ознакомьтесь с условиями заказа и документами:\n"
                    f"👨‍🏫 {tutor.get('name', 'Репетитор')}\n"
                    f"📚 {booking['subject']}\n"
                    f"📅 {booking['date']} 🕒 {booking['time_slot']}\n\n"
                    "Оплачивая заказ после ознакомления, вы акцептуете указанную "
                    "редакцию оферты."
                ),
                keyboard=_vk_docs_keyboard(legal.PAYMENT_DOCS, "student"),
            )
            for doc_type in legal.PAYMENT_DOCS:
                await legal.mark_document_presented(
                    uid, "vk", doc_type, int(booking_id)
                )
    return await _original_create_payment(*args, **kwargs)


legacy.create_payment = _create_payment_with_legal


_original_mark_paid = legacy.mark_booking_paid_once


async def _mark_paid_with_acceptance(*args, **kwargs):
    booking_id = kwargs.get("booking_id") or (args[0] if args else None)
    result = await _original_mark_paid(*args, **kwargs)
    if booking_id:
        booking = await app._db.get_booking(int(booking_id))
        if booking and booking.get("status") == "paid":
            if not await legal.already_logged(
                booking["user_id"], "student_offer", "accepted", int(booking_id)
            ):
                await legal.log_legal_event(
                    booking["user_id"],
                    "student",
                    booking.get("user_platform", "vk"),
                    "student_offer",
                    "accepted",
                    booking_id=int(booking_id),
                    metadata={
                        "acceptance_basis": "successful_payment_after_presentation"
                    },
                )
    return result


legacy.mark_booking_paid_once = _mark_paid_with_acceptance


async def main():
    return await app.main()


if __name__ == "__main__":
    legacy.logging.basicConfig(level=legacy.logging.INFO, stream=legacy.sys.stdout)
    legacy.asyncio.run(main())
