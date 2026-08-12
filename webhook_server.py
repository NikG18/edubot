import json
import hashlib
import logging
import os
from aiohttp import web
from aiogram import Bot
from database import (
    get_all_bookings,
    update_booking,
    add_lesson_to_balance,
    get_all_tutors,
)

TINKOFF_SECRET_KEY = os.environ.get("TINKOFF_SECRET_KEY")
TINKOFF_TERMINAL_KEY = os.environ.get("TINKOFF_TERMINAL_KEY")

def check_signature(request_body: bytes, token: str) -> bool:
    """Проверяет подпись уведомления от Т‑Банка."""
    expected = hashlib.sha256(request_body + TINKOFF_SECRET_KEY.encode()).hexdigest()
    return expected == token

async def handle_tinkoff_webhook(request):
    bot: Bot = request.app.get("bot")
    if bot is None:
        logging.error("Webhook: bot не передан в приложение")
        return web.Response(status=500, text="Internal Server Error")

    try:
        body = await request.read()
        data = json.loads(body)
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    token = request.headers.get("Token", "")
    if not check_signature(body, token):
        logging.warning("Webhook: неверная подпись")
        return web.Response(status=403, text="Forbidden")

    status = data.get("Status")
    order_id = data.get("OrderId")  # формат booking_123
    if not order_id or not order_id.startswith("booking_"):
        return web.Response(status=400, text="Invalid OrderId")

    booking_id = int(order_id.split("_")[1])

    # Получаем бронь до изменения (для старых данных)
    bookings = await get_all_bookings()
    booking = bookings.get(booking_id)
    if not booking:
        return web.Response(status=404, text="Booking not found")

    # Если статус уже изменён, ничего не делаем
    if booking["status"] != "confirmed":
        return web.Response(text="OK")

    payment_msg_id = booking.get("payment_msg_id")
    user_id = booking["user_id"]
    tutor_id = booking["tutor_id"]

    # Обработка успешной оплаты
    if status in ("CONFIRMED", "AUTHORIZED"):
        await update_booking(booking_id, status="paid")
        await add_lesson_to_balance(user_id, tutor_id, booking["subject"])

        # Удаляем сообщение со ссылкой на оплату
        if payment_msg_id:
            try:
                await bot.delete_message(chat_id=user_id, message_id=payment_msg_id)
            except Exception as e:
                logging.warning(f"Не удалось удалить сообщение {payment_msg_id}: {e}")

        # Отправляем уведомление ученику
        await bot.send_message(user_id, "✅ Оплата получена! Занятие подтверждено.")

        # Уведомляем преподавателя
        tutors = await get_all_tutors()
        tutor = tutors.get(tutor_id)
        if tutor and tutor.get("telegram_id"):
            try:
                await bot.send_message(
                    tutor["telegram_id"],
                    f"✅ Оплата за занятие {booking['date']} {booking['time_slot']} получена."
                )
            except Exception as e:
                logging.warning(f"Не удалось уведомить преподавателя {tutor_id}: {e}")

        logging.info(f"Webhook: booking {booking_id} оплачен (status={status})")

    # Обработка отклонённого/отменённого платежа
    elif status in ("REJECTED", "CANCELED"):
        await update_booking(booking_id, status="cancelled")

        if payment_msg_id:
            try:
                await bot.delete_message(chat_id=user_id, message_id=payment_msg_id)
            except Exception as e:
                logging.warning(f"Не удалось удалить сообщение {payment_msg_id}: {e}")

        await bot.send_message(user_id, "❌ Платёж не прошёл. Запись отменена.")

        tutors = await get_all_tutors()
        tutor = tutors.get(tutor_id)
        if tutor and tutor.get("telegram_id"):
            try:
                await bot.send_message(
                    tutor["telegram_id"],
                    f"❌ Платёж за занятие {booking['date']} {booking['time_slot']} не прошёл, запись отменена."
                )
            except Exception as e:
                logging.warning(f"Не удалось уведомить преподавателя {tutor_id}: {e}")

        logging.info(f"Webhook: booking {booking_id} отменён ({status})")

    return web.Response(text="OK")

def create_webhook_app(bot: Bot):
    app = web.Application()
    app['bot'] = bot
    app.router.add_post("/tinkoff-webhook", handle_tinkoff_webhook)
    return app
