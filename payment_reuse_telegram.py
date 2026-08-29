"""Идемпотентная оплата занятия через СБП для Telegram entrypoint.

Одна бронь получает один T-Bank PaymentId. Кнопку СБП можно получать заново через
GetQr для того же PaymentId, поэтому потеря старого сообщения не требует нового Init
и не порождает повторных уведомлений преподавателю.
"""

import asyncio
import json

import database as _db
import Bot_test as app
import fiscalization as _fiscal
import payments as _payments

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
    if isinstance(source, legacy.types.Message):
        await source.answer(text)
    elif isinstance(source, legacy.types.CallbackQuery):
        try:
            await source.message.edit_text(text)
        except legacy.TelegramBadRequest:
            await source.message.answer(text)


async def _send_payment_link(source, bot, booking, booking_id: int, payment_url: str, price_rub):
    student_msg = (
        f"💳 Оплата через СБП — {price_rub:g} руб.:\n"
        f"📚 {booking['subject']}\n"
        f"📅 {booking['date']} (МСК) {booking['time_slot']}\n\n"
        "Нажмите кнопку ниже и выберите банк."
    )
    platform = booking.get("user_platform", "telegram")
    if platform == "telegram":
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text="💳 Оплатить через СБП", url=payment_url)]
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
                    "label": "💳 Оплатить через СБП",
                }
            }]],
        })
        await legacy.send_to_user(booking["user_id"], platform, student_msg, keyboard_vk=keyboard)


async def _sbp_link_for_existing(payment_id: str):
    try:
        return await _payments.get_sbp_payment_link(str(payment_id))
    except Exception:
        legacy.logging.exception("Не удалось получить СБП-ссылку payment_id=%s", payment_id)
        return None


async def _fiscal_payment_may_be_exposed(booking_id: int, payment_id: str) -> bool:
    if not _fiscal.AGENT_FISCALIZATION_ENABLED:
        return True
    try:
        return await _fiscal.booking_prepayment_snapshot_matches(booking_id, str(payment_id))
    except Exception:
        legacy.logging.exception(
            "Не удалось проверить fiscal snapshot booking=%s payment=%s", booking_id, payment_id
        )
        return False


async def _idempotent_create_and_send_payment(source, bot, booking, email, booking_id):
    booking_id = int(booking_id)
    async with _lock_for(booking_id):
        current = await _db.get_booking(booking_id)
        if not current or current.get("status") not in {"pending", "confirmed"}:
            await _respond(source, "Запись не найдена или её статус уже изменился.")
            return

        existing_payment_id = current.get("tinkoff_payment_id")
        if existing_payment_id:
            if not await _fiscal_payment_may_be_exposed(booking_id, existing_payment_id):
                await _respond(
                    source,
                    "Ссылка на оплату временно заблокирована: не подтверждена фискальная связка "
                    "этого платежа. Новый платёж автоматически не создаётся. Обратитесь в поддержку.",
                )
                return
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
            await _send_payment_link(source, bot, current, booking_id, payment_url, price_rub)
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
        payment_url, payment_id = await _payments.create_payment(
            booking_id=booking_id,
            amount_kop=amount_kop,
            description=description,
            tutor_id=current["tutor_id"],
            tutor_name=tutor["name"],
            customer_email=email,
            inn=inn,
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

        if not payment_id:
            await legacy.send_to_user(
                current["user_id"],
                current.get("user_platform", "telegram"),
                "Ошибка создания платежа. Обратитесь в поддержку.",
            )
            return

        if not await _fiscal_payment_may_be_exposed(booking_id, payment_id):
            await _respond(
                source,
                "Платёж создан, но ссылка заблокирована из-за ошибки фиксации фискальных данных. "
                "Новый платёж не создаётся. Обратитесь в поддержку.",
            )
            return

        if not payment_url:
            payment_url = await _sbp_link_for_existing(payment_id)
        if not payment_url:
            await _respond(
                source,
                "Платёж создан, но Т-Банк временно не вернул ссылку СБП. "
                "Откройте оплату ещё раз через минуту — будет использован тот же платёж.",
            )
            return

        await _save_payment_url(booking_id, payment_url)
        refreshed = await _db.get_booking(booking_id) or current
        await _send_payment_link(source, bot, refreshed, booking_id, payment_url, price_rub)

        await legacy.send_to_tutor(
            refreshed["tutor_id"],
            f"✅ Занятие с {refreshed['username']} подтверждено. Ожидается оплата.",
        )


legacy.create_and_send_payment = _idempotent_create_and_send_payment
