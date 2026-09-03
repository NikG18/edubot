"""Safe replacement for VK's catch-all pending-payment email handler."""


async def _safe_process_payment_email(message):
    booking_id = await get_pending_email_request(message.from_id)
    if not booking_id:
        return

    email = (message.text or "").strip()
    if not email:
        await message.answer("Сейчас ожидается email. Отправьте адрес текстовым сообщением.")
        return
    if not valid_email(email):
        await message.answer("Введите корректный email, например name@example.com")
        return

    if int(booking_id) <= 0:
        # VK currently has no subscription-purchase email flow. A stale/legacy -1
        # marker must not be treated as a booking id.
        await delete_pending_email_request(message.from_id)
        await message.answer("Запрос оплаты устарел. Откройте раздел «Оплата» заново.")
        return

    await set_user_email(message.from_id, email)
    bookings = await get_all_bookings()
    booking = bookings.get(int(booking_id))
    if not booking:
        await message.answer("Ошибка: запись не найдена. Откройте раздел «Оплата» заново.")
        await delete_pending_email_request(message.from_id)
        return

    await create_and_send_payment(message, booking, email, int(booking_id))
    await delete_pending_email_request(message.from_id)


def install_vk_email_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_vk_payment_email_hardened", False):
        return
    target = getattr(legacy, "process_payment_email", None)
    if target is None:
        return
    if target.__code__.co_freevars or _safe_process_payment_email.__code__.co_freevars:
        raise RuntimeError("VK email handler replacement must not use closures")
    target.__code__ = _safe_process_payment_email.__code__
    legacy._vk_payment_email_hardened = True
