import logging
from aiohttp import web
from payments import generate_token
from database import (
    get_pending_subscription_by_payment_id,
    activate_subscription,
    get_booking_id_by_payment_id,
)
from messaging import send_to_user
from bot_common import process_booking_payment_status


def _valid_notification(payload: dict) -> bool:
    received = payload.get("Token")
    if not received:
        return False
    expected = generate_token({k: v for k, v in payload.items() if k != "Token"})
    import hmac
    return hmac.compare_digest(str(received), str(expected))


async def _handle_notification(request: web.Request):
    bot = request.app["bot"]
    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")

    if not _valid_notification(payload):
        logging.warning("Отклонён webhook T-Bank с неверным Token")
        return web.Response(status=403, text="forbidden")

    payment_id = str(payload.get("PaymentId") or "")
    status = str(payload.get("Status") or "")

    if not payment_id or not status:
        return web.Response(status=400, text="missing fields")

    # Сначала проверяем, относится ли платёж к абонементу.
    pending_sub = await get_pending_subscription_by_payment_id(payment_id)
    if pending_sub:
        if status in ("CONFIRMED", "AUTHORIZED"):
            activated = await activate_subscription(payment_id)
            if activated:
                await send_to_user(
                    pending_sub["user_id"],
                    pending_sub.get("user_platform", "telegram"),
                    f"✅ Абонемент на {pending_sub['total_lessons']} занятий активирован."
                )
        elif status in ("REJECTED", "CANCELED"):
            from database import delete_pending_subscription
            await delete_pending_subscription(payment_id)
        return web.Response(text="OK")

    booking_id = await get_booking_id_by_payment_id(payment_id)
    if booking_id:
        changed, booking = await process_booking_payment_status(booking_id, status)
        if changed and booking:
            if status in ("CONFIRMED", "AUTHORIZED"):
                if booking.get("payment_msg_id") and booking.get("user_platform") == "telegram":
                    try:
                        await bot.delete_message(
                            chat_id=booking["user_id"],
                            message_id=booking["payment_msg_id"]
                        )
                    except Exception:
                        logging.exception("Не удалось удалить сообщение оплаты")
                await send_to_user(
                    booking["user_id"], booking.get("user_platform", "telegram"),
                    "✅ Оплата получена! Занятие подтверждено."
                )
                from messaging import send_to_tutor
                await send_to_tutor(
                    booking["tutor_id"],
                    f"✅ Оплата за занятие {booking['date']} {booking['time_slot']} получена."
                )
            elif status in ("REJECTED", "CANCELED"):
                await send_to_user(
                    booking["user_id"], booking.get("user_platform", "telegram"),
                    "❌ Платёж не прошёл. Запись отменена."
                )

    # T-Bank ожидает HTTP 200 и OK.
    return web.Response(text="OK")


def create_webhook_app(bot):
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/tinkoff-webhook", _handle_notification)
    return app
