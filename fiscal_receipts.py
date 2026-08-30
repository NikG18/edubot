"""Идемпотентные закрывающие чеки для одиночных оплаченных занятий.

Важно: модуль НЕ закрывает чек автоматически по времени. Текущий cleanup переводит
paid -> completed только по окончанию слота, а это еще не доказательство фактического
оказания услуги. Вызывать send_booking_closing_receipt нужно после подтвержденного
факта проведения занятия (или вручную в smoke-тесте на тестовом платеже).
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import database as _db
import payments
from fiscal_agent import get_tutor_phone

MSK = ZoneInfo("Europe/Moscow")


async def ensure_fiscal_receipt_schema():
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fiscal_receipts (
                id BIGSERIAL PRIMARY KEY,
                booking_id INTEGER REFERENCES bookings(id) ON DELETE SET NULL,
                payment_id TEXT NOT NULL,
                receipt_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                amount INTEGER NOT NULL,
                customer_email TEXT NOT NULL,
                supplier_name TEXT NOT NULL,
                supplier_inn TEXT NOT NULL,
                supplier_phone TEXT NOT NULL,
                description TEXT NOT NULL,
                response JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sent_at TIMESTAMPTZ,
                UNIQUE(payment_id, receipt_kind)
            );
            CREATE INDEX IF NOT EXISTS idx_fiscal_receipts_booking
                ON fiscal_receipts(booking_id, created_at DESC)
                WHERE booking_id IS NOT NULL;
            """
        )


async def snapshot_booking_prepayment(
    *,
    booking_id: int,
    payment_id: str,
    amount_kop: int,
    customer_email: str,
    supplier_name: str,
    supplier_inn: str,
    supplier_phone: str,
    description: str,
):
    """Сохраняет неизменяемый набор реквизитов, использованный при Init."""
    await ensure_fiscal_receipt_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO fiscal_receipts
                (booking_id,payment_id,receipt_kind,status,amount,customer_email,
                 supplier_name,supplier_inn,supplier_phone,description,response,sent_at)
            VALUES($1,$2,'prepayment','created',$3,$4,$5,$6,$7,$8,'{}'::jsonb,NOW())
            ON CONFLICT(payment_id,receipt_kind) DO NOTHING
            """,
            int(booking_id), str(payment_id), int(amount_kop), customer_email,
            supplier_name, supplier_inn, supplier_phone, description,
        )


async def _get_prepayment_snapshot(booking_id: int):
    await ensure_fiscal_receipt_schema()
    async with _db._legacy.pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT * FROM fiscal_receipts
            WHERE booking_id=$1 AND receipt_kind='prepayment'
            ORDER BY id DESC LIMIT 1
            """,
            int(booking_id),
        )


async def send_booking_closing_receipt(booking_id: int, *, allow_noncompleted: bool = False) -> dict:
    """Формирует закрывающий чек один раз для одиночного занятия.

    В production allow_noncompleted оставлять False. Для тестового терминала можно
    передать True, если нужно проверить закрывающий чек без ожидания конца занятия.
    """
    await ensure_fiscal_receipt_schema()
    booking = await _db.get_booking(int(booking_id))
    if not booking:
        return {"ok": False, "reason": "booking_not_found"}
    if not allow_noncompleted and booking.get("status") != "completed":
        return {"ok": False, "reason": "lesson_not_confirmed_completed"}

    snap = await _get_prepayment_snapshot(int(booking_id))
    if not snap:
        return {"ok": False, "reason": "prepayment_snapshot_not_found"}

    async with _db._legacy.pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM fiscal_receipts WHERE payment_id=$1 AND receipt_kind='closing'",
            snap["payment_id"],
        )
        if existing and existing["status"] == "sent":
            return {"ok": True, "already_sent": True, "response": dict(existing["response"] or {})}
        if not existing:
            await conn.execute(
                """
                INSERT INTO fiscal_receipts
                    (booking_id,payment_id,receipt_kind,status,amount,customer_email,
                     supplier_name,supplier_inn,supplier_phone,description)
                VALUES($1,$2,'closing','sending',$3,$4,$5,$6,$7,$8)
                ON CONFLICT(payment_id,receipt_kind) DO NOTHING
                """,
                int(booking_id), snap["payment_id"], snap["amount"], snap["customer_email"],
                snap["supplier_name"], snap["supplier_inn"], snap["supplier_phone"], snap["description"],
            )
        else:
            await conn.execute(
                "UPDATE fiscal_receipts SET status='sending' WHERE id=$1",
                existing["id"],
            )

    try:
        response = await payments.send_closing_receipt(
            payment_id=snap["payment_id"],
            amount_kop=int(snap["amount"]),
            description=snap["description"],
            customer_email=snap["customer_email"],
            tutor_name=snap["supplier_name"],
            inn=snap["supplier_inn"],
            supplier_phone=snap["supplier_phone"],
        )
    except Exception as exc:
        logging.exception("Ошибка закрывающего чека booking=%s", booking_id)
        response = {"exception": str(exc)}

    success = bool(response.get("Success", False))
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE fiscal_receipts
            SET status=$1,response=$2::jsonb,sent_at=$3
            WHERE payment_id=$4 AND receipt_kind='closing'
            """,
            "sent" if success else "failed",
            json.dumps(response, ensure_ascii=False),
            datetime.now(MSK) if success else None,
            snap["payment_id"],
        )
    return {"ok": success, "already_sent": False, "response": response}


async def snapshot_from_booking(booking_id: int, customer_email: str, description: str):
    """Утилита для регистрации уже созданного одиночного платежа."""
    booking = await _db.get_booking(int(booking_id))
    if not booking or not booking.get("tinkoff_payment_id"):
        return False
    tutors = await _db.get_all_tutors()
    tutor = tutors.get(booking["tutor_id"])
    if not tutor:
        return False
    phone = await get_tutor_phone(booking["tutor_id"])
    await snapshot_booking_prepayment(
        booking_id=booking_id,
        payment_id=booking["tinkoff_payment_id"],
        amount_kop=int(booking.get("amount") or 0),
        customer_email=customer_email,
        supplier_name=tutor["name"],
        supplier_inn=(tutor.get("inn") or "").strip(),
        supplier_phone=phone,
        description=description,
    )
    return True
