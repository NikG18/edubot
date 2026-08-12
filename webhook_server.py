import json
import hashlib
import logging
import os
from aiohttp import web
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
    # Т‑Банк подписывает тело запроса секретным ключом через SHA256
    expected = hashlib.sha256(request_body + TINKOFF_SECRET_KEY.encode()).hexdigest()
    return expected == token

async def handle_tinkoff_webhook(request):
    try:
        body = await request.read()
        data = json.loads(body)
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    token = request.headers.get("Token", "")
    if not check_signature(body, token):
        logging.warning("Webhook: неверная подпись")
        return web.Response(status=403, text="Forbidden")

    # Проверяем статус платежа
    status = data.get("Status")
    order_id = data.get("OrderId")  # формат booking_123
    if not order_id or not order_id.startswith("booking_"):
        return web.Response(status=400, text="Invalid OrderId")

    booking_id = int(order_id.split("_")[1])

    # Допустимые финальные статусы
    if status in ("CONFIRMED", "AUTHORIZED"):
        # Обновляем запись
        bookings = await get_all_bookings()
        booking = bookings.get(booking_id)
        if not booking:
            return web.Response(status=404, text="Booking not found")
        if booking["status"] != "confirmed":
            # Уже обработано или не в том статусе
            return web.Response(text="OK")

        await update_booking(booking_id, status="paid")
        await add_lesson_to_balance(booking["user_id"], booking["tutor_id"], booking["subject"])

        # Удаляем сообщение со ссылкой (если нужно) – но для этого нужен бот.
        # Мы можем просто отправить уведомление ученику через бота, если он доступен.
        # Поскольку бот работает в другом процессе, здесь нет доступа к bot.
        # Решение: использовать общую очередь (например, asyncio.Queue) или просто пропустить,
        # оставив очистку сообщений фоновой задаче check_pending_payments.
        # Либо можно вызвать send_message через API бота (requests), но проще оставить периодическую проверку.

        # Уведомление преподавателю (тоже можно отправить, если хранить бота как глобальный объект, но не рекомендуется)
        # В рамках простоты оставим уведомления на фоновую задачу check_pending_payments.
        logging.info(f"Webhook: booking {booking_id} оплачен (status={status})")
    elif status in ("REJECTED", "CANCELED"):
        await update_booking(booking_id, status="cancelled")
        logging.info(f"Webhook: booking {booking_id} отменён ({status})")

    return web.Response(text="OK")

def create_webhook_app():
    app = web.Application()
    app.router.add_post("/tinkoff-webhook", handle_tinkoff_webhook)
    return app
