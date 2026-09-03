"""Идемпотентная оплата занятия через СБП для VK entrypoint.

Одна бронь получает один T-Bank PaymentId. При повторной выдаче ссылки новый Init
не создаётся: GetQr запрашивает свежую СБП-ссылку для уже сохранённого PaymentId.
"""

import asyncio

import database as _db
import payments as _payments
import vk_bot as app
from fiscal_agent import get_tutor_phone
from fiscal_receipts import snapshot_booking_prepayment

legacy = app.legacy
_booking_locks: dict[int, asyncio.Lock] = {}
_schema_ready = False


def _lock_for(booking_id: int) -> asyncio.Lock:
    lock = _booking_locks.get(booking_id)
    if lock is None:
        lock = asyncio.Lock()
        _booking_locks[booking_id] = lock
    return lock


async def _ensure_payment_url_schema():
    global _schema_ready
    if _schema_ready:
        return
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_url TEXT")
    _schema_ready = True


async def _save_payment_url(booking_id: int, payment_url: str):
    await _ensure_payment_url_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            "UPDATE bookings SET payment_url=$1, updated_at=NOW() WHERE id=$2",
            payment_url,
            booking_id,
        )


async def _respond(source, text: str):
    if isinstance(source, legacy.MessageEvent):
        await legacy.edit_event_message(source, text)
    elif isinstance(source, legacy.Message):
        await source.answer(text)


async def _send_payment_link(booking, booking_id: int, payment_url: str, price_rub) -> bool:
    student_msg = (
        f"💳 Оплата через СБП — {float(price_rub):g} руб.:\n"
        f"📚 {booking['subject']}\n"
        f"📅 {booking['date']} (МСК) {booking['time_slot']}\n\n"
        "Нажмите кнопку ниже и выберите банк."
    )
    keyboard = legacy.Keyboard(inline=True)
    keyboard.add(legacy.OpenLink("💳 Оплатить через СБП", payment_url))
    return bool(await legacy.send_to_user(
        booking["user_id"],
        booking.get("user_platform", "vk"),
        student_msg,
        keyboard_vk=keyboard.get_json(),
    ))


async def _sbp_link_for_existing(payment_id: str):
    try:
        return await _payments.get_sbp_payment_link(str(payment_id))
    except Exception:
        legacy.logging.exception("Не удалось получить СБП-ссылку payment_id=%s", payment_id)
        return None


async def _idempotent_create_and_send_payment(source, booking, email, booking_id):
    booking_id = int(booking_id)
    async with _lock_for(booking_id):
        current = await _db.get_booking(booking_id)
        if not current or current.get("status") not in {"pending", "confirmed"}:
            await _respond(source, "Запись не найдена или её статус уже изменился.")
            return {"payment_id": None, "link_sent": False, "reason": "invalid_booking"}

        existing_payment_id = current.get("tinkoff_payment_id")
        if existing_payment_id:
            payment_url = await _sbp_link_for_existing(existing_payment_id)
            if not payment_url:
                await _respond(
                    source,
                    "Не удалось получить ссылку СБП для уже созданного платежа. "
                    "Попробуйте ещё раз через минуту или обратитесь в поддержку. "
                    "Новый платёж автоматически не создаётся, чтобы исключить двойную оплату.",
                )
                return {"payment_id": str(existing_payment_id), "link_sent": False, "reason": "no_sbp_url"}
            await _save_payment_url(booking_id, payment_url)
            amount_kop = int(current.get("amount") or 0)
            price_rub = amount_kop / 100 if amount_kop else 0
            delivered = await _send_payment_link(current, booking_id, payment_url, price_rub)
            return {"payment_id": str(existing_payment_id), "link_sent": delivered, "reused": True}

        tutors = await legacy.get_all_tutors()
        tutor = tutors.get(current["tutor_id"])
        if not tutor:
            await _respond(source, "Репетитор не найден.")
            return {"payment_id": None, "link_sent": False, "reason": "tutor_missing"}

        inn = (tutor.get("inn") or "").strip()
        if not inn:
            await _respond(source, "Запись к репетитору недоступна. Напишите в поддержку.")
            return {"payment_id": None, "link_sent": False, "reason": "inn_missing"}
        direct_service = _payments.is_operator_tutor(inn)
        supplier_phone = _payments.OPERATOR_PHONE if direct_service else await get_tutor_phone(current["tutor_id"])
        if not supplier_phone:
            await _respond(source, "Для репетитора не заполнен телефон для кассового чека. Напишите в поддержку.")
            return {"payment_id": None, "link_sent": False, "reason": "supplier_phone_missing"}

        if current.get("amount"):
            amount_kop = int(current["amount"])
            price_rub = amount_kop / 100
        else:
            price_rub = tutor["subjects"].get(current["subject"])
            if not price_rub:
                await _respond(source, "Не указана цена предмета.")
                return {"payment_id": None, "link_sent": False, "reason": "price_missing"}
            amount_kop = int(price_rub) * 100

        if direct_service:
            percent = 0
        else:
            now = legacy.now_msk_naive()
            if tutor.get("commission_mode") == "auto":
                percent, _ = await legacy.calculate_auto_commission(current["tutor_id"], now.year, now.month)
            else:
                percent = tutor.get("commission_percent", 25)

        description = (
            f"Занятие: {current['subject']} с {tutor['name']} "
            f"{current['date']} {current['time_slot']}"
        )
        payment_url, payment_id = await _payments.create_payment(
            booking_id=booking_id,
            amount_kop=amount_kop,
            description=description,
            tutor_id=current["tutor_id"],
            tutor_name=tutor["name"],
            customer_email=email,
            inn=inn,
            supplier_phone=supplier_phone,
            order_id_prefix="booking",
        )

        if payment_id:
            await legacy.update_booking(
                booking_id,
                status="confirmed",
                reminded=0,
                amount=amount_kop,
                commission_percent=percent,
                tinkoff_payment_id=payment_id,
            )
            try:
                await snapshot_booking_prepayment(
                    booking_id=booking_id,
                    payment_id=payment_id,
                    amount_kop=amount_kop,
                    customer_email=email,
                    supplier_name=tutor["name"],
                    supplier_inn=inn,
                    supplier_phone=supplier_phone,
                    description=description,
                )
            except Exception:
                legacy.logging.exception("Не удалось сохранить фискальный snapshot booking=%s", booking_id)

        if not payment_id:
            await legacy.send_to_user(
                current["user_id"],
                current.get("user_platform", "vk"),
                "Ошибка создания платежа. Проверьте кассовые реквизиты или обратитесь в поддержку.",
            )
            return {"payment_id": None, "link_sent": False, "reason": "payment_init_failed"}

        if not payment_url:
            payment_url = await _sbp_link_for_existing(payment_id)
        if not payment_url:
            await _respond(
                source,
                "Платёж создан, но Т-Банк временно не вернул ссылку СБП. "
                "Повторная выдача ссылки будет использовать тот же платёж.",
            )
            return {"payment_id": str(payment_id), "link_sent": False, "reason": "no_sbp_url"}

        await _save_payment_url(booking_id, payment_url)
        refreshed = await _db.get_booking(booking_id) or current
        delivered = await _send_payment_link(refreshed, booking_id, payment_url, price_rub)
        if delivered:
            await legacy.send_to_tutor(
                refreshed["tutor_id"],
                f"✅ Занятие с {refreshed['username']} подтверждено. Ожидается оплата.",
            )
        return {"payment_id": str(payment_id), "link_sent": delivered, "reused": False}


legacy.create_and_send_payment = _idempotent_create_and_send_payment
