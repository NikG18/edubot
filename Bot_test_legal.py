import Bot_test as app
import legal_documents as legal
from aiogram.types import FSInputFile, KeyboardButton

legacy = app.legacy


async def _tg_role(user_id: int) -> str:
    if user_id == legacy.ADMING_ID:
        return "admin"
    if await legacy.get_tutor_by_telegram_id(user_id) is not None:
        return "tutor"
    return "student"


_original_get_main_menu = legacy.get_main_menu


async def _legal_main_menu(user_id: int):
    keyboard = await _original_get_main_menu(user_id)
    if not any(
        any(getattr(btn, "text", "") == "📄 Документы" for btn in row)
        for row in keyboard.keyboard
    ):
        keyboard.keyboard.append([KeyboardButton(text="📄 Документы")])
    return keyboard


legacy.get_main_menu = _legal_main_menu


async def _send_doc(message, doc_type: str):
    path = legal.doc_path(doc_type)
    title = legal.DOCS[doc_type][1]
    if path.exists():
        await message.answer_document(
            FSInputFile(path),
            caption=f"📄 {title}\nРедакция: {legal.DOC_VERSION}",
        )
    else:
        await message.answer(f"📄 {title}\n{legal.doc_url(doc_type)}")


async def _send_docs_for_role(message, role: str):
    docs = (
        legal.ADMIN_DOCS if role == "admin"
        else legal.TUTOR_DOCS if role == "tutor"
        else legal.STUDENT_DOCS
    )
    note = ""
    if role == "student":
        note = (
            "\n\nСогласие на дополнительные/рекламные сообщения является "
            "необязательным и не требуется для обычного бронирования."
        )
    elif role == "tutor":
        note = (
            "\n\nПервичные данные и отдельные согласия репетитора собираются "
            "через Яндекс Форму; бот показывает актуальные редакции и не просит "
            "дать те же согласия повторно."
        )
    await message.answer(f"📄 Актуальные юридические документы.{note}")
    for doc_type in docs:
        await _send_doc(message, doc_type)
    if role == "tutor" and legal.TUTOR_YANDEX_FORM_URL:
        kb = legacy.InlineKeyboardMarkup(inline_keyboard=[[
            legacy.InlineKeyboardButton(
                text="📝 Анкета репетитора (Яндекс Форма)",
                url=legal.TUTOR_YANDEX_FORM_URL,
            )
        ]])
        await message.answer("Анкета и первичные подтверждения:", reply_markup=kb)


@legacy.dp.message(legacy.F.text == "📄 Документы")
async def legal_documents_menu(message: legacy.Message):
    await _send_docs_for_role(message, await _tg_role(message.from_user.id))


@legacy.dp.message(legacy.Command("documents"))
async def legal_documents_command(message: legacy.Message):
    await _send_docs_for_role(message, await _tg_role(message.from_user.id))


_original_create_payment = legacy.create_payment


async def _create_payment_with_legal(*args, **kwargs):
    booking_id = kwargs.get("booking_id") or (args[0] if args else None)
    if booking_id:
        booking = await app._db.get_booking(int(booking_id))
        if booking and booking.get("user_platform", "telegram") == "telegram":
            uid = booking["user_id"]
            tutors = await app._db.get_all_tutors()
            tutor = tutors.get(booking["tutor_id"], {})
            links = "\n".join(
                f"• {legal.DOCS[d][1]}: {legal.doc_url(d)}"
                for d in legal.PAYMENT_DOCS
            )
            await legacy.send_telegram_message(
                uid,
                "Перед оплатой доступны условия заказа и юридические документы:\n"
                f"👨‍🏫 {tutor.get('name', 'Репетитор')}\n"
                f"📚 {booking['subject']}\n"
                f"📅 {booking['date']} 🕒 {booking['time_slot']}\n\n"
                f"{links}\n\n"
                "Оплачивая заказ после ознакомления, вы акцептуете редакцию "
                "оферты, указанную в документах.",
            )
            for doc_type in legal.PAYMENT_DOCS:
                await legal.mark_document_presented(
                    uid, "telegram", doc_type, int(booking_id)
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
                    booking.get("user_platform", "telegram"),
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
