"""Idempotent T-Bank prepayment creation for subscription packages.

The same canonical student/tutor/subject/package intent reuses one outstanding
PaymentId across Telegram and VK. A PostgreSQL advisory lock protects against
simultaneous confirms in different bot processes. Package price is always recomputed
from current server-side pricing rules rather than trusting FSM/payload totals.
"""

from __future__ import annotations

import logging

import database as _db
import payments as _payments
import subscription_hardening as subs
from pricing import get_subscription_discount, subscription_total_kop, subscription_total_rub


async def create_or_reuse_subscription_payment(
    *,
    platform: str,
    platform_user_id: int,
    tutor_id: int,
    subject: str,
    lessons_count: int,
    customer_email: str,
) -> dict:
    platform = str(platform or "").lower()
    if platform not in {"telegram", "vk"}:
        return {"ok": False, "reason": "invalid_platform"}

    try:
        count = int(lessons_count)
        discount = get_subscription_discount(count)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "invalid_package"}

    tutors = await _db.get_all_tutors()
    tutor = tutors.get(int(tutor_id))
    if not tutor:
        return {"ok": False, "reason": "tutor_missing"}
    price = (tutor.get("subjects") or {}).get(str(subject))
    if not price:
        return {"ok": False, "reason": "subject_missing"}
    inn = str(tutor.get("inn") or "").strip()
    if not inn:
        return {"ok": False, "reason": "tutor_inn_missing"}

    email = str(customer_email or "").strip()
    if not email:
        return {"ok": False, "reason": "email_missing"}

    total_rub = subscription_total_rub(price, count)
    amount_kop = subscription_total_kop(price, count)
    student_id = await _db.get_student_id(platform, int(platform_user_id), create=True)
    await _db.set_student_email(platform, int(platform_user_id), email)
    await subs.ensure_subscription_schema()
    await _db._ensure_pool()

    lock_key = f"subscription-purchase:{student_id}:{int(tutor_id)}:{subject}:{count}"
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                lock_key,
            )
            pending_rows = await conn.fetch(
                """
                SELECT * FROM pending_subscriptions
                WHERE student_id=$1 AND tutor_id=$2 AND subject=$3 AND total_lessons=$4
                ORDER BY id DESC
                """,
                int(student_id), int(tutor_id), str(subject), count,
            )
            if len(pending_rows) > 1:
                logging.error(
                    "Multiple pending subscription payments for student=%s tutor=%s subject=%s count=%s",
                    student_id, tutor_id, subject, count,
                )
                return {"ok": False, "reason": "duplicate_pending_payments"}

            if pending_rows:
                pending = pending_rows[0]
                payment_id = str(pending["payment_id"] or "")
                state = await _payments.check_payment(payment_id)
                if state.get("Success"):
                    status = str(state.get("Status") or "").upper()
                    if status in {"CONFIRMED", "AUTHORIZED"}:
                        activated = await subs.activate_subscription(payment_id)
                        return {
                            "ok": bool(activated),
                            "activated": bool(activated),
                            "reused": True,
                            "payment_id": payment_id,
                            "payment_url": None,
                            "total_rub": pending["total_price"],
                            "discount": int(pending["discount_percent"] or discount),
                            "lessons_count": int(pending["total_lessons"]),
                            "reason": None if activated else "activation_failed",
                        }
                    if status in {"REJECTED", "CANCELED"}:
                        await conn.execute(
                            "DELETE FROM pending_subscriptions WHERE id=$1",
                            pending["id"],
                        )
                    else:
                        url = await _payments.get_sbp_payment_link(payment_id)
                        return {
                            "ok": True,
                            "activated": False,
                            "reused": True,
                            "payment_id": payment_id,
                            "payment_url": url,
                            "total_rub": pending["total_price"],
                            "discount": int(pending["discount_percent"] or discount),
                            "lessons_count": int(pending["total_lessons"]),
                            "reason": None if url else "payment_link_unavailable",
                        }
                else:
                    # Bank status could not be verified. Never create a second Init
                    # while an outstanding PaymentId exists.
                    url = await _payments.get_sbp_payment_link(payment_id)
                    return {
                        "ok": True,
                        "activated": False,
                        "reused": True,
                        "payment_id": payment_id,
                        "payment_url": url,
                        "total_rub": pending["total_price"],
                        "discount": int(pending["discount_percent"] or discount),
                        "lessons_count": int(pending["total_lessons"]),
                        "reason": None if url else "payment_status_unknown",
                    }

            description = f"Абонемент: {count} занятий по {subject} у {tutor['name']}"
            payment_url, payment_id = await _payments.create_payment(
                booking_id=0,
                amount_kop=amount_kop,
                description=description,
                tutor_id=int(tutor_id),
                tutor_name=tutor["name"],
                customer_email=email,
                inn=inn,
                order_id_prefix="sub",
            )
            if not payment_id:
                return {"ok": False, "reason": "payment_init_failed"}

            await conn.execute(
                """
                INSERT INTO pending_subscriptions
                    (user_id,tutor_id,subject,total_lessons,discount_percent,total_price,
                     payment_id,user_platform,student_id)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                int(platform_user_id), int(tutor_id), str(subject), count, discount,
                total_rub, str(payment_id), platform, int(student_id),
            )
            return {
                "ok": True,
                "activated": False,
                "reused": False,
                "payment_id": str(payment_id),
                "payment_url": payment_url,
                "total_rub": total_rub,
                "discount": discount,
                "lessons_count": count,
                "reason": None if payment_url else "payment_link_unavailable",
            }


async def _telegram_respond(legacy, source, text: str, reply_markup=None):
    if isinstance(source, legacy.types.Message):
        await source.answer(text, reply_markup=reply_markup)
    elif isinstance(source, legacy.types.CallbackQuery):
        try:
            await source.message.edit_text(text, reply_markup=reply_markup)
        except legacy.TelegramBadRequest:
            await source.message.answer(text, reply_markup=reply_markup)


def install_telegram_subscription_purchase_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_subscription_purchase_tg_hardened", False):
        return

    async def create_subscription_payment(
        source, bot, user_id, tutor_id, subject, count, total, discount, email, user_platform
    ):
        # total/discount are legacy UI snapshots only; server-side rules above are authoritative.
        result = await create_or_reuse_subscription_payment(
            platform="telegram",
            platform_user_id=int(user_id),
            tutor_id=int(tutor_id),
            subject=str(subject),
            lessons_count=int(count),
            customer_email=str(email),
        )
        if not result.get("ok"):
            await _telegram_respond(
                legacy,
                source,
                "⚠️ Не удалось создать оплату абонемента. "
                f"Причина: {result.get('reason') or 'неизвестная ошибка'}. Обратитесь в поддержку.",
            )
            return result
        if result.get("activated"):
            await _telegram_respond(legacy, source, "✅ Абонемент уже оплачен и активирован.")
            return result
        url = result.get("payment_url")
        if not url:
            await _telegram_respond(
                legacy,
                source,
                "Платёж уже создан, но Т-Банк временно не вернул ссылку СБП. "
                "Откройте покупку ещё раз позже — новый платёж создан не будет.",
            )
            return result
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text="💳 Оплатить через СБП", url=url)]
        ])
        await legacy.send_to_user(
            int(user_id),
            "telegram",
            f"Ссылка на оплату абонемента на {result['lessons_count']} занятий "
            f"({result['total_rub']} ₽):",
            reply_markup_tg=keyboard.model_dump_json(),
        )
        return result

    legacy.create_subscription_payment = create_subscription_payment
    if hasattr(app, "create_subscription_payment"):
        app.create_subscription_payment = create_subscription_payment
    legacy._subscription_purchase_tg_hardened = True
