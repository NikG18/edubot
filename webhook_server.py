import logging
from aiohttp import web
from payments import generate_token
import database as _db
from messaging import send_to_user
from bot_common import FULL_REFUND_STATUSES, process_booking_payment_status


def _valid_notification(payload: dict) -> bool:
    received = payload.get("Token")
    if not received:
        return False
    expected = generate_token({k: v for k, v in payload.items() if k != "Token"})
    import hmac
    return hmac.compare_digest(str(received), str(expected))


async def _read_notification_payload(request: web.Request) -> dict:
    """Accept both JSON and form-encoded T-Bank notifications."""
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass

    try:
        payload = await request.post()
    except Exception:
        return {}
    return dict(payload) if payload else {}


async def _handle_subscription_notification(payment_id: str, status: str):
    """Serialize webhook activation with Telegram/VK fallback polling."""
    # Schema preparation may open its own connection and execute DDL. It must run
    # before the transaction below: otherwise the outer SELECT and inner ALTER can
    # wait on one another when this process sees a VK-created package for the first
    # time.
    import subscription_hardening as subscriptions

    await subscriptions.ensure_subscription_schema()
    await _db._ensure_pool()
    notification = None
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"payment-poll:{payment_id}",
            )
            pending_sub = await conn.fetchrow(
                "SELECT * FROM pending_subscriptions WHERE payment_id=$1",
                payment_id,
            )
            if not pending_sub:
                return True, None

            pending = dict(pending_sub)
            if status in {"CONFIRMED", "AUTHORIZED"}:
                activated = await subscriptions.activate_subscription(payment_id)
                if activated:
                    notification = (
                        pending["user_id"],
                        pending.get("user_platform") or "telegram",
                        f"✅ Абонемент на {pending['total_lessons']} занятий активирован.",
                    )
                else:
                    logging.error("Webhook could not activate subscription payment_id=%s", payment_id)
            elif status in {"REJECTED", "CANCELED"}:
                await conn.execute(
                    "DELETE FROM pending_subscriptions WHERE payment_id=$1",
                    payment_id,
                )
                notification = (
                    pending["user_id"],
                    pending.get("user_platform") or "telegram",
                    "❌ Платёж за абонемент не прошёл. Абонемент не активирован.",
                )
            else:
                return True, None
    return True, notification


async def _handle_notification(request: web.Request):
    bot = request.app["bot"]
    payload = await _read_notification_payload(request)
    if not payload:
        return web.Response(status=400, text="bad payload")

    if not _valid_notification(payload):
        logging.warning("Отклонён webhook T-Bank с неверным Token")
        return web.Response(status=403, text="forbidden")

    payment_id = str(payload.get("PaymentId") or "")
    status = str(payload.get("Status") or "").upper()

    if not payment_id or not status:
        return web.Response(status=400, text="missing fields")

    # Сначала проверяем, относится ли платёж к абонементу. The shared advisory
    # lock prevents duplicate activation/notifications if polling runs concurrently.
    pending_sub = await _db.get_pending_subscription_by_payment_id(payment_id)
    if pending_sub:
        _handled, notification = await _handle_subscription_notification(payment_id, status)
        if notification is not None:
            user_id, platform, text = notification
            await send_to_user(user_id, platform, text)
        return web.Response(text="OK")

    booking_id = await _db.get_booking_id_by_payment_id(payment_id)
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
            elif status in FULL_REFUND_STATUSES:
                logging.info(
                    "Полный возврат подтверждён webhook: booking=%s payment_id=%s",
                    booking_id,
                    payment_id,
                )

    # T-Bank ожидает HTTP 200 и OK.
    return web.Response(text="OK")


def create_webhook_app(bot):
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/tinkoff-webhook", _handle_notification)
    return app
