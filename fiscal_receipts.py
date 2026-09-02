"""Идемпотентные закрывающие чеки для одиночных оплаченных занятий.

Модуль не закрывает чек автоматически только по времени. Вызывать
send_booking_closing_receipt нужно после подтвержденного факта проведения
занятия или вручную во время smoke-теста.
"""

import json
import logging

import database as _db
import payments
from fiscal_agent import get_tutor_phone


_FINAL_CLOSING_STATUSES = {"submitted", "sent"}


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
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sent_at TIMESTAMPTZ,
                UNIQUE(payment_id, receipt_kind)
            );
            ALTER TABLE fiscal_receipts
                ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE fiscal_receipts
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
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
    """Сохраняет реквизиты, переданные в Init, но не считает чек пробитым.

    Первый чек формирует подключенная к T-Bank касса CloudKassir только после
    успешной оплаты. Запись prepared — это снимок запроса для последующего
    закрывающего чека, а не подтверждение фискализации.
    """
    await ensure_fiscal_receipt_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO fiscal_receipts
                (booking_id,payment_id,receipt_kind,status,amount,customer_email,
                 supplier_name,supplier_inn,supplier_phone,description,response,
                 attempt_count,sent_at)
            VALUES($1,$2,'prepayment','prepared',$3,$4,$5,$6,$7,$8,'{}'::jsonb,0,NULL)
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


async def _claim_closing_receipt(conn, booking_id: int, snap):
    """Атомарно резервирует единственную отправку закрывающего чека."""
    claimed = await conn.fetchrow(
        """
        INSERT INTO fiscal_receipts
            (booking_id,payment_id,receipt_kind,status,amount,customer_email,
             supplier_name,supplier_inn,supplier_phone,description,attempt_count)
        VALUES($1,$2,'closing','sending',$3,$4,$5,$6,$7,$8,1)
        ON CONFLICT(payment_id,receipt_kind) DO NOTHING
        RETURNING *
        """,
        int(booking_id), snap["payment_id"], snap["amount"], snap["customer_email"],
        snap["supplier_name"], snap["supplier_inn"], snap["supplier_phone"],
        snap["description"],
    )
    if claimed:
        return claimed, None

    existing = await conn.fetchrow(
        "SELECT * FROM fiscal_receipts WHERE payment_id=$1 AND receipt_kind='closing'",
        snap["payment_id"],
    )
    if not existing:
        return None, "closing_receipt_claim_failed"
    if existing["status"] in _FINAL_CLOSING_STATUSES:
        return None, "already_submitted"
    if existing["status"] == "sending":
        return None, "closing_receipt_in_progress"
    if existing["status"] == "unknown":
        # При таймауте неизвестно, принял ли банк запрос. Повтор без проверки
        # операции в T-Bank может создать дубль.
        return None, "closing_receipt_status_unknown"
    if existing["status"] != "failed":
        return None, "closing_receipt_not_retryable"

    claimed = await conn.fetchrow(
        """
        UPDATE fiscal_receipts
        SET status='sending', attempt_count=attempt_count+1,
            response='{}'::jsonb, updated_at=NOW()
        WHERE id=$1 AND status='failed'
        RETURNING *
        """,
        existing["id"],
    )
    return (claimed, None) if claimed else (None, "closing_receipt_claimed_elsewhere")


async def send_booking_closing_receipt(
    booking_id: int,
    *,
    allow_noncompleted: bool = False,
) -> dict:
    """Один раз передает закрывающий чек для одиночного занятия.

    Повтор разрешен только после явного ответа банка с ошибкой. После сетевого
    таймаута статус становится unknown: сначала нужно проверить операцию в
    T-Bank/CloudKassir, чтобы не отправить второй чек.
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
        claimed, reason = await _claim_closing_receipt(conn, int(booking_id), snap)
        if not claimed:
            if reason == "already_submitted":
                existing = await conn.fetchrow(
                    "SELECT response FROM fiscal_receipts "
                    "WHERE payment_id=$1 AND receipt_kind='closing'",
                    snap["payment_id"],
                )
                return {
                    "ok": True,
                    "already_sent": True,
                    "status": "submitted",
                    "response": dict(existing["response"] or {}) if existing else {},
                }
            return {"ok": False, "already_sent": False, "reason": reason}

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
        result_status = "unknown"
    else:
        if response.get("Success") is True:
            # API подтвердил прием запроса. Фактический чек затем проверяется в
            # разделе операции T-Bank или в CloudKassir.
            result_status = "submitted"
        elif response:
            result_status = "failed"
        else:
            result_status = "unknown"

    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE fiscal_receipts
            SET status=$1, response=$2::jsonb,
                sent_at=CASE WHEN $1='submitted' THEN NOW() ELSE NULL END,
                updated_at=NOW()
            WHERE id=$3 AND status='sending'
            """,
            result_status,
            json.dumps(response, ensure_ascii=False),
            claimed["id"],
        )

    return {
        "ok": result_status == "submitted",
        "already_sent": False,
        "status": result_status,
        "response": response,
    }


async def snapshot_from_booking(booking_id: int, customer_email: str, description: str):
    """Регистрирует снимок реквизитов уже созданного одиночного платежа."""
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
