"""Catch pending payment email in Telegram without relying on local FSM.

A tutor may confirm a Telegram student's booking from VK. In that case the VK
process cannot create an aiogram FSM state in the Telegram process. The durable
platform-specific pending request is the source of truth instead.
"""

import database as _db
import student_account_hardening as accounts


def install_telegram_email_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_telegram_pending_email_fallback_installed", False):
        return

    @legacy.dp.message()
    async def pending_payment_email_fallback(
        message: legacy.Message,
        bot: legacy.Bot,
        state: legacy.FSMContext,
    ):
        user_id = message.from_user.id
        booking_id = await accounts.get_pending_email_request("telegram", user_id)
        if not booking_id:
            return

        email = (message.text or "").strip()
        if not email:
            await message.answer("Сейчас ожидается e-mail. Отправьте адрес текстовым сообщением.")
            return
        if not legacy.valid_email(email):
            await message.answer("Введите корректный e-mail, например name@example.com")
            return
        if int(booking_id) <= 0:
            await accounts.delete_pending_email_request("telegram", user_id)
            await state.clear()
            await message.answer("Запрос оплаты устарел. Откройте раздел «Оплата» заново.")
            return

        booking = await _db.get_booking(int(booking_id))
        if not booking or not await _db.account_owns_booking("telegram", user_id, booking):
            await accounts.delete_pending_email_request("telegram", user_id)
            await state.clear()
            await message.answer("Запрос оплаты больше не актуален. Откройте раздел «Оплата» заново.")
            return

        await _db.set_student_email("telegram", user_id, email)
        await legacy.create_and_send_payment(message, bot, booking, email, int(booking_id))
        await accounts.delete_pending_email_request("telegram", user_id)
        await state.clear()

    legacy._telegram_pending_email_fallback_installed = True
