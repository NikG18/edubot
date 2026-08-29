"""Делает создание ссылки оплаты занятия идемпотентным для Telegram entrypoint.

Старый create_and_send_payment при каждом повторном открытии оплаты создавал новый
T-Bank Init и повторно уведомлял преподавателя. Здесь одна бронь получает один
активный payment_id/payment_url; повторное открытие только показывает ту же ссылку.
"""

import asyncio
import json

import database as _db
import Bot_test as app

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


async def _get_payment_url(booking_id: int):
    await _ensure_payment_url_schema()
    async with _db._legacy.pool.acquire() as conn:
        return await conn.fetchval("SELECT payment_url FROM bookings WHERE id=$1", booking_id)


async def _save_payment_url(booking_id: int, payment_url: str):
    await _ensure_payment_url_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            "UPDATE bookings SET payment_url=$1, updated_at=NOW() WHERE id=$2",
            payment_url,
            booking_id,
        )


async def _respond(source, text: str):
    if isinstance(source, legacy.types.Message):
        await source.answer(text)
    elif isinstance(source, legacy.types.CallbackQuery):
        try:
            await source.message.edit_text(text)
        except legacy.TelegramBadRequest:
            await source.message.answer(text)


async def _send_payment_link(source, bot, booking, booking_id: int, payment_url: str, price_rub):
    student_msg = (
        f"💳 Для оплаты занятия внесите {price_rub:g} руб.:\n"
        f"📚 {booking['subject']}\n"
        f"📅 {booking['date']} (МСК) {booking['time_slot']}"
    )
    platform = booking.get("user_platform", "telegram")
    if platform == "telegram":
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text="💳 Оплатить", url=payment_url)]
        ])
        sent = await bot.send_message(booking["user_id"], student_msg, reply_markup=keyboard)
        await legacy.update_booking(booking_id, payment_msg_id=sent.message_id)
    else:
        keyboard = json.dumps({
            "inline": True,
            "buttons": [[{
                "action": {
                    "type": "open_link",
                    "link": payment_url,
                    "label": "💳 Оплатить",
                }
            }]],
        })
        await legacy.send_to_user(booking["user_id"], platform, student_msg, keyboard_vk=keyboard)


async def _idempotent_create_and_send_payment(source, bot, booking, email, booking_id):
    async with _lock_for(int(booking_id)):
        current = await _db.get_booking(int(booking_id))
        if not current or current.get("status") not in {"pending", "confirmed"}:
            await _respond(source, "Запись не найдена или её статус уже изменился.")
            return

        # Если payment_id уже есть, второй T-Bank Init запрещён.
        existing_payment_id = current.get("tinkoff_payment_id")
        if existing_payment_id:
            payment_url = await _get_payment_url(int(booking_id))
            if not payment_url:
                # Для платежей, созданных до этого исправления, пробуем восстановить URL
                # из GetState, если T-Bank его возвращает. Новый платёж не создаём.
                try:
                    state = await legacy.check_payment(existing_payment_id)
                    payment_url = state.get("PaymentURL") if state else None
                except Exception:
                    legacy.logging.exception(
                        "Не удалось получить состояние существующего payment_id booking=%s",
                        booking_id,
                    )
                if payment_url:
                    await _save_payment_url(int(booking_id), payment_url)

            if payment_url:
                amount_kop = int(current.get("amount") or 0)
                price_rub = amount_kop / 100 if amount_kop else 0
                await _send_payment_link(source, bot, current, int(booking_id), payment_url, price_rub)
            else:
                await _respond(
                    source,
                    "💳 Платёж для этого занятия уже создан. Используйте ранее отправленную "
                    "кнопку «Оплатить». Новая платёжная ссылка автоматически не создаётся, "
                    "чтобы исключить двойной платёж.",
                )
            return

        tutors = await legacy.get_all_tutors()
        tutor = tutors.get(current["tutor_id"])
        if not tutor:
            await _respond(source, "Репетитор не найден.")
            return

        inn = (tutor.get("inn") or "").strip()
        if not inn:
            await _respond(source, "Запись к репетитору недоступна. Напишите в поддержку.")
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
        payment_url, payment_id = await legacy.create_payment(
            booking_id=int(booking_id),
            amount_kop=amount_kop,
            description=description,
            tutor_id=current["tutor_id"],
            tutor_name=tutor["name"],
            customer_email=email,
            inn=inn,
            order_id_prefix="booking",
        )
        if not payment_url or not payment_id:
            await legacy.send_to_user(
                current["user_id"],
                current.get("user_platform", "telegram"),
                "Ошибка создания платежа. Обратитесь в поддержку.",
            )
            return

        await legacy.update_booking(
            int(booking_id),
            status="confirmed",
            reminded=0,
            amount=amount_kop,
            commission_percent=percent,
            tinkoff_payment_id=payment_id,
        )
        await _save_payment_url(int(booking_id), payment_url)

        refreshed = await _db.get_booking(int(booking_id)) or current
        await _send_payment_link(source, bot, refreshed, int(booking_id), payment_url, price_rub)

        # Только первый созданный платёж порождает уведомление преподавателю.
        await legacy.send_to_tutor(
            refreshed["tutor_id"],
            f"✅ Занятие с {refreshed['username']} подтверждено. Ожидается оплата.",
        )


legacy.create_and_send_payment = _idempotent_create_and_send_payment
