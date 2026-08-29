"""Идемпотентная оплата занятия через СБП для VK entrypoint.

Одна бронь получает один T-Bank PaymentId. При повторной выдаче ссылки новый Init
не создаётся: GetQr запрашивает свежую СБП-ссылку для уже сохранённого PaymentId.
"""

import asyncio

import database as _db
import payments as _payments
import vk_bot as app

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


async def _send_payment_link(booking, booking_id: int, payment_url: str, price_rub):
    student_msg = (
        f"💳 Оплата через СБП — {float(price_rub):g} руб.:\n"
        f"📚 {booking['subject']}\n"
        f"📅 {booking['date']} (МСК) {booking['time_slot']}\n\n"
        "Нажмите кнопку ниже и выберите банк."
    )
    keyboard = legacy.Keyboard(inline=True)
    keyboard.add(legacy.OpenLink("💳 Оплатить через СБП", payment_url))
    await legacy.send_to_user(
        booking["user_id"],
        booking.get("user_platform", "vk"),
        student_msg,
        keyboard_vk=keyboard.get_json(),
    )


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
            return

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
                return
            await _save_payment_url(booking_id, payment_url)
            amount_kop = int(current.get("amount") or 0)
            price_rub = amount_kop / 100 if amount_kop else 0
            await _send_payment_link(current, booking_id, payment_url, price_rub)
            return

        tutors = await legacy.get_all_tutors()
        tutor = tutors.get(current["tutor_id"])
        if not tutor:
            await _respond(source, "Репетитор не найден.")
            return

        if current.get("amount"):
            amount_kop = int(current["amount"])
            price_rub = amount_kop / 100
        else:
            price_rub = tutor["subjects"].get(current["subject"])
            if not price_rub:
                await _respond(source, "Не указана цена предмета.")
                return
            amount_kop = int(price_rub) * 100

        now = legacy.now_msk_naive()
        if tutor.get("commission_mode") == "auto":
            percent, _ = await legacy.calculate_auto_commission(
                current["tutor_id"], now.year, now.month
            )
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
            inn=tutor.get("inn", ""),
            order_id_prefix="booking",
        )

        # PaymentId сохраняем сразу после Init, даже если GetQr временно не вернул ссылку.
        # Иначе повторная попытка могла бы создать второй платёж.
        if payment_id:
            await legacy.update_booking(
                booking_id,
                status="confirmed",
                reminded=0,
                amount=amount_kop,
                commission_percent=percent,
                tinkoff_payment_id=payment_id,
            )

        if not payment_id:
            await legacy.send_to_user(
                current["user_id"],
                current.get("user_platform", "vk"),
                "Ошибка создания платежа. Обратитесь в поддержку.",
            )
            return

        if not payment_url:
            payment_url = await _sbp_link_for_existing(payment_id)
        if not payment_url:
            await _respond(
                source,
                "Платёж создан, но Т-Банк временно не вернул ссылку СБП. "
                "Повторная выдача ссылки будет использовать тот же платёж.",
            )
            return

        await _save_payment_url(booking_id, payment_url)
        refreshed = await _db.get_booking(booking_id) or current
        await _send_payment_link(refreshed, booking_id, payment_url, price_rub)

        # Репетитора уведомляем только при первом создании PaymentId.
        await legacy.send_to_tutor(
            refreshed["tutor_id"],
            f"✅ Занятие с {refreshed['username']} подтверждено. Ожидается оплата.",
        )


legacy.create_and_send_payment = _idempotent_create_and_send_payment
