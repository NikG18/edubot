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
    activate_subscription,           
    get_pending_subscription_by_payment_id, 
    delete_pending_subscription,
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
    order_id = data.get("OrderId")
    payment_id = data.get("PaymentId")

    if not order_id or "_" not in order_id:
        return web.Response(status=400, text="Invalid OrderId")

    prefix, id_str = order_id.split("_", 1)
    try:
        order_id_num = int(id_str)
    except ValueError:
        return web.Response(status=400, text="Invalid OrderId")

    if prefix == "booking":
        # === Обработка занятия ===
        bookings = await get_all_bookings()
        booking = bookings.get(order_id_num)
        if not booking:
            return web.Response(status=404, text="Booking not found")

        # Если статус уже изменён, ничего не делаем
        if booking["status"] != "confirmed":
            return web.Response(text="OK")

        payment_msg_id = booking.get("payment_msg_id")
        user_id = booking["user_id"]
        tutor_id = booking["tutor_id"]

        if status in ("CONFIRMED", "AUTHORIZED"):
            await update_booking(order_id_num, status="paid")
            await add_lesson_to_balance(user_id, tutor_id, booking["subject"])

            if payment_msg_id:
                try:
                    await bot.delete_message(chat_id=user_id, message_id=payment_msg_id)
                except Exception as e:
                    logging.warning(f"Не удалось удалить сообщение {payment_msg_id}: {e}")

            await bot.send_message(user_id, "✅ Оплата получена! Занятие подтверждено.")

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

            logging.info(f"Webhook: booking {order_id_num} оплачен (status={status})")

        elif status in ("REJECTED", "CANCELED"):
            await update_booking(order_id_num, status="cancelled")

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

            logging.info(f"Webhook: booking {order_id_num} отменён ({status})")

    elif prefix == "sub":
        # === Обработка абонемента ===
        pending = await get_pending_subscription_by_payment_id(payment_id)
        if not pending:
            logging.warning(f"Webhook: pending subscription не найден для payment_id={payment_id}")
            return web.Response(text="OK")  # возможно уже обработано

        if status in ("CONFIRMED", "AUTHORIZED"):
            await activate_subscription(payment_id)
            await bot.send_message(pending["user_id"], "✅ Абонемент успешно активирован!")
            logging.info(f"Webhook: subscription {payment_id} оплачен")
        elif status in ("REJECTED", "CANCELED"):
            await delete_pending_subscription(payment_id)
            await bot.send_message(pending["user_id"], "❌ Платёж за абонемент не прошёл.")
            logging.info(f"Webhook: subscription {payment_id} отменён")

    return web.Response(text="OK")


def create_webhook_app(bot: Bot):
    app = web.Application()
    app['bot'] = bot
    app.router.add_post("/tinkoff-webhook", handle_tinkoff_webhook)
    return app
