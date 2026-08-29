import copy
import json
import logging
import os
import re
from typing import Awaitable, Callable, Optional

import database as _db


AGENT_FISCALIZATION_ENABLED = os.environ.get("AGENT_FISCALIZATION_ENABLED", "0").strip().lower() in {
    "1", "true", "yes", "on",
}
FISCAL_TAXATION = os.environ.get("FISCAL_TAXATION", "usn_income").strip() or "usn_income"
FISCAL_AGENT_SIGN = os.environ.get("FISCAL_AGENT_SIGN", "another").strip() or "another"

_ALLOWED_TAXATIONS = {"osn", "usn_income", "usn_income_outcome", "esn", "patent"}
_ALLOWED_AGENT_SIGNS = {
    "bank_paying_agent", "bank_paying_subagent", "paying_agent",
    "paying_subagent", "attorney", "commission_agent", "another",
}


class FiscalizationError(RuntimeError):
    pass


class FiscalProfileError(FiscalizationError):
    pass


def normalize_supplier_phone(value: str) -> str:
    raw = (value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if not digits.startswith("7") or len(digits) != 11:
        raise FiscalProfileError("Телефон поставщика должен быть российским номером в формате +7XXXXXXXXXX")
    return "+" + digits


def validate_supplier_inn(value: str) -> str:
    inn = re.sub(r"\D", "", (value or "").strip())
    # В этом проекте принципалы — физлица/ИП на НПД, у них ИНН физлица из 12 цифр.
    if len(inn) != 12:
        raise FiscalProfileError("Для самозанятого принципала нужен ИНН физлица из 12 цифр")
    return inn


def validate_supplier_name(value: str) -> str:
    name = " ".join((value or "").strip().split())
    if len(name) < 5:
        raise FiscalProfileError("Укажите полное ФИО самозанятого поставщика")
    if len(name) > 200:
        raise FiscalProfileError("ФИО поставщика слишком длинное")
    return name


def _validate_config() -> None:
    if FISCAL_TAXATION not in _ALLOWED_TAXATIONS:
        raise FiscalizationError(f"Неподдерживаемая Taxation: {FISCAL_TAXATION}")
    if FISCAL_AGENT_SIGN not in _ALLOWED_AGENT_SIGNS:
        raise FiscalizationError(f"Неподдерживаемый AgentSign: {FISCAL_AGENT_SIGN}")


async def ensure_fiscal_schema() -> None:
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tutor_fiscal_profiles (
                tutor_id INTEGER PRIMARY KEY REFERENCES tutors(id) ON DELETE CASCADE,
                supplier_name TEXT NOT NULL,
                supplier_inn TEXT NOT NULL,
                supplier_phone TEXT NOT NULL,
                npd_verified_at TIMESTAMPTZ NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS fiscal_receipts (
                id BIGSERIAL PRIMARY KEY,
                receipt_type TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id BIGINT NOT NULL,
                payment_id TEXT NULL,
                user_id BIGINT NULL,
                tutor_id INTEGER NULL REFERENCES tutors(id) ON DELETE SET NULL,
                amount_kop BIGINT NOT NULL,
                customer_email TEXT NULL,
                description TEXT NOT NULL,
                supplier_name TEXT NOT NULL,
                supplier_inn TEXT NOT NULL,
                supplier_phone TEXT NOT NULL,
                status TEXT NOT NULL,
                request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                error TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(receipt_type, scope_type, scope_id)
            );

            CREATE INDEX IF NOT EXISTS idx_fiscal_receipts_status
                ON fiscal_receipts(receipt_type, status, updated_at);

            CREATE UNIQUE INDEX IF NOT EXISTS uq_fiscal_receipt_payment_type
                ON fiscal_receipts(receipt_type, payment_id)
                WHERE payment_id IS NOT NULL;
            """
        )


async def set_tutor_fiscal_profile(
    tutor_id: int,
    *,
    supplier_name: str,
    supplier_inn: str,
    supplier_phone: str,
    npd_verified: bool = False,
) -> None:
    name = validate_supplier_name(supplier_name)
    inn = validate_supplier_inn(supplier_inn)
    phone = normalize_supplier_phone(supplier_phone)
    await ensure_fiscal_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tutor_fiscal_profiles
                (tutor_id, supplier_name, supplier_inn, supplier_phone, npd_verified_at, updated_at)
            VALUES($1,$2,$3,$4,CASE WHEN $5 THEN NOW() ELSE NULL END,NOW())
            ON CONFLICT(tutor_id) DO UPDATE SET
                supplier_name=EXCLUDED.supplier_name,
                supplier_inn=EXCLUDED.supplier_inn,
                supplier_phone=EXCLUDED.supplier_phone,
                npd_verified_at=CASE
                    WHEN $5 THEN NOW()
                    WHEN tutor_fiscal_profiles.supplier_inn <> EXCLUDED.supplier_inn THEN NULL
                    ELSE tutor_fiscal_profiles.npd_verified_at
                END,
                updated_at=NOW()
            """,
            int(tutor_id), name, inn, phone, bool(npd_verified),
        )


async def mark_tutor_npd_verified(tutor_id: int, verified: bool) -> None:
    await ensure_fiscal_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE tutor_fiscal_profiles
            SET npd_verified_at=CASE WHEN $2 THEN NOW() ELSE NULL END, updated_at=NOW()
            WHERE tutor_id=$1
            """,
            int(tutor_id), bool(verified),
        )


async def get_tutor_fiscal_profile(tutor_id: int) -> Optional[dict]:
    await ensure_fiscal_schema()
    async with _db._legacy.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT tutor_id,supplier_name,supplier_inn,supplier_phone,npd_verified_at,updated_at
            FROM tutor_fiscal_profiles
            WHERE tutor_id=$1
            """,
            int(tutor_id),
        )
    return dict(row) if row else None


async def require_tutor_fiscal_profile(tutor_id: int, expected_inn: Optional[str] = None) -> dict:
    _validate_config()
    profile = await get_tutor_fiscal_profile(tutor_id)
    if not profile:
        raise FiscalProfileError(
            f"Для tutor_id={tutor_id} не заполнен fiscal profile (ФИО, ИНН, телефон)"
        )
    profile["supplier_name"] = validate_supplier_name(profile["supplier_name"])
    profile["supplier_inn"] = validate_supplier_inn(profile["supplier_inn"])
    profile["supplier_phone"] = normalize_supplier_phone(profile["supplier_phone"])
    if expected_inn:
        expected = validate_supplier_inn(expected_inn)
        if expected != profile["supplier_inn"]:
            raise FiscalProfileError(
                f"ИНН в карточке репетитора и fiscal profile tutor_id={tutor_id} не совпадают"
            )
    if not profile.get("npd_verified_at"):
        raise FiscalProfileError(
            f"Для tutor_id={tutor_id} не подтвержден актуальный статус НПД"
        )
    return profile


def _agent_service_item(
    *,
    amount_kop: int,
    description: str,
    supplier_name: str,
    supplier_inn: str,
    supplier_phone: str,
    payment_method: str,
) -> dict:
    if amount_kop <= 0:
        raise FiscalizationError("Сумма чека должна быть больше нуля")
    if payment_method not in {"full_prepayment", "full_payment"}:
        raise FiscalizationError(f"Неподдерживаемый PaymentMethod: {payment_method}")
    return {
        "Name": (description or "Занятие с репетитором")[:128],
        "Price": int(amount_kop),
        "Quantity": 1,
        "Amount": int(amount_kop),
        "Tax": "none",
        "PaymentMethod": payment_method,
        "PaymentObject": "service",
        "MeasurementUnit": "шт",
        "AgentData": {"AgentSign": FISCAL_AGENT_SIGN},
        "SupplierInfo": {
            "Phones": [supplier_phone],
            "Name": supplier_name,
            "Inn": supplier_inn,
        },
    }


def build_agent_prepayment_receipt(
    *,
    amount_kop: int,
    description: str,
    customer_email: str,
    profile: dict,
) -> dict:
    _validate_config()
    if not customer_email or "@" not in customer_email:
        raise FiscalizationError("Для электронного чека нужен email покупателя")
    return {
        "Email": customer_email.strip(),
        "Taxation": FISCAL_TAXATION,
        "Items": [
            _agent_service_item(
                amount_kop=int(amount_kop),
                description=description,
                supplier_name=profile["supplier_name"],
                supplier_inn=profile["supplier_inn"],
                supplier_phone=profile["supplier_phone"],
                payment_method="full_prepayment",
            )
        ],
    }


def build_agent_closing_receipt(prepayment_receipt: dict) -> dict:
    """Строит чек зачета 100% предоплаты после фактического занятия."""
    receipt = copy.deepcopy(prepayment_receipt)
    items = receipt.get("Items") or []
    if len(items) != 1:
        raise FiscalizationError("Ожидалась ровно одна позиция занятия")
    amount = int(items[0]["Amount"])
    items[0]["PaymentMethod"] = "full_payment"
    # В момент оказания услуги новых денег нет: зачитывается ранее внесенная предоплата.
    receipt["Payments"] = {
        "Cash": 0,
        "Electronic": 0,
        "AdvancePayment": amount,
        "Credit": 0,
        "Provision": 0,
    }
    return receipt


async def record_booking_prepayment_snapshot(
    *,
    booking_id: int,
    payment_id: str,
    tutor_id: int,
    amount_kop: int,
    customer_email: str,
    description: str,
    receipt: dict,
) -> None:
    if not booking_id or int(booking_id) <= 0:
        raise FiscalizationError("Snapshot можно привязать только к реальной записи")
    booking = await _db.get_booking(int(booking_id))
    if not booking:
        raise FiscalizationError(f"Запись {booking_id} не найдена для fiscal snapshot")
    item = (receipt.get("Items") or [{}])[0]
    supplier = item.get("SupplierInfo") or {}
    await ensure_fiscal_schema()
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                """
                SELECT payment_id
                FROM fiscal_receipts
                WHERE receipt_type='prepayment' AND scope_type='booking' AND scope_id=$1
                FOR UPDATE
                """,
                int(booking_id),
            )
            if existing:
                if str(existing["payment_id"] or "") != str(payment_id):
                    raise FiscalizationError(
                        f"У booking_id={booking_id} уже есть другой fiscal PaymentId"
                    )
                return
            await conn.execute(
                """
                INSERT INTO fiscal_receipts
                    (receipt_type,scope_type,scope_id,payment_id,user_id,tutor_id,amount_kop,
                     customer_email,description,supplier_name,supplier_inn,supplier_phone,
                     status,request_payload)
                VALUES
                    ('prepayment','booking',$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'registered',$11::jsonb)
                """,
                int(booking_id), str(payment_id), int(booking["user_id"]), int(tutor_id),
                int(amount_kop), customer_email, description,
                supplier.get("Name") or "",
                supplier.get("Inn") or "",
                (supplier.get("Phones") or [""])[0],
                json.dumps(receipt, ensure_ascii=False),
            )


async def _claim_closing_receipt(prepayment_row: dict) -> Optional[int]:
    await ensure_fiscal_schema()
    closing_receipt = build_agent_closing_receipt(prepayment_row["request_payload"])
    async with _db._legacy.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO fiscal_receipts
                (receipt_type,scope_type,scope_id,payment_id,user_id,tutor_id,amount_kop,
                 customer_email,description,supplier_name,supplier_inn,supplier_phone,
                 status,request_payload)
            VALUES
                ('closing','booking',$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'sending',$11::jsonb)
            ON CONFLICT(receipt_type,scope_type,scope_id) DO NOTHING
            RETURNING id
            """,
            int(prepayment_row["scope_id"]),
            str(prepayment_row["payment_id"]),
            prepayment_row.get("user_id"),
            prepayment_row.get("tutor_id"),
            int(prepayment_row["amount_kop"]),
            prepayment_row.get("customer_email"),
            prepayment_row.get("description") or "Занятие с репетитором",
            prepayment_row.get("supplier_name") or "",
            prepayment_row.get("supplier_inn") or "",
            prepayment_row.get("supplier_phone") or "",
            json.dumps(closing_receipt, ensure_ascii=False),
        )
    return int(row["id"]) if row else None


async def _finish_closing_receipt(
    receipt_id: int,
    *,
    status: str,
    response: dict,
    error: Optional[str] = None,
) -> None:
    await ensure_fiscal_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE fiscal_receipts
            SET status=$1,response_payload=$2::jsonb,error=$3,updated_at=NOW()
            WHERE id=$4
            """,
            status,
            json.dumps(response or {}, ensure_ascii=False),
            error,
            int(receipt_id),
        )


async def list_completed_bookings_ready_for_closing(limit: int = 25) -> list[dict]:
    await ensure_fiscal_schema()
    async with _db._legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.*
            FROM fiscal_receipts p
            JOIN bookings b ON b.id=p.scope_id
            LEFT JOIN fiscal_receipts c
              ON c.receipt_type='closing'
             AND c.scope_type='booking'
             AND c.scope_id=p.scope_id
            WHERE p.receipt_type='prepayment'
              AND p.scope_type='booking'
              AND p.status='registered'
              AND b.status='completed'
              AND b.tinkoff_payment_id IS NOT NULL
              AND b.tinkoff_payment_id=p.payment_id
              AND c.id IS NULL
            ORDER BY b.id
            LIMIT $1
            """,
            int(limit),
        )
    result = []
    for row in rows:
        item = dict(row)
        payload = item.get("request_payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        item["request_payload"] = payload or {}
        result.append(item)
    return result


async def reconcile_completed_lessons(
    send_closing_receipt: Callable[[str, dict], Awaitable[dict]],
    *,
    limit: int = 25,
) -> dict:
    """Отправляет закрывающие чеки с DB-защитой от повторной отправки.

    Неизвестный транспортный результат не ретраится автоматически: банк мог принять
    первый запрос, и автоматический повтор способен создать дубль фискального документа.
    """
    if not AGENT_FISCALIZATION_ENABLED:
        return {"enabled": False, "sent": 0, "failed": 0, "unknown": 0}

    candidates = await list_completed_bookings_ready_for_closing(limit=limit)
    stats = {"enabled": True, "sent": 0, "failed": 0, "unknown": 0}
    for prepayment in candidates:
        receipt_id = await _claim_closing_receipt(prepayment)
        if not receipt_id:
            continue
        closing = build_agent_closing_receipt(prepayment["request_payload"])
        try:
            response = await send_closing_receipt(str(prepayment["payment_id"]), closing)
        except Exception as exc:
            logging.exception("Неизвестный результат отправки closing receipt id=%s", receipt_id)
            await _finish_closing_receipt(
                receipt_id,
                status="unknown",
                response={},
                error=f"{type(exc).__name__}: {exc}",
            )
            stats["unknown"] += 1
            continue

        if response.get("_transport_error"):
            await _finish_closing_receipt(
                receipt_id,
                status="unknown",
                response=response,
                error=response.get("Message") or "transport error",
            )
            stats["unknown"] += 1
        elif response.get("Success"):
            await _finish_closing_receipt(receipt_id, status="sent", response=response)
            stats["sent"] += 1
        else:
            await _finish_closing_receipt(
                receipt_id,
                status="failed",
                response=response,
                error=response.get("Details") or response.get("Message") or "T-Bank rejected receipt",
            )
            stats["failed"] += 1
    return stats


async def get_preflight_rows() -> list[dict]:
    await ensure_fiscal_schema()
    async with _db._legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id,t.name,t.inn,
                   p.supplier_name,p.supplier_inn,p.supplier_phone,p.npd_verified_at
            FROM tutors t
            LEFT JOIN tutor_fiscal_profiles p ON p.tutor_id=t.id
            ORDER BY t.id
            """
        )
    result = []
    for row in rows:
        d = dict(row)
        result.append({
            "tutor_id": d["id"],
            "name": d["name"],
            "profile": bool(d.get("supplier_name") and d.get("supplier_inn") and d.get("supplier_phone")),
            "inn_matches": bool(
                d.get("inn") and d.get("supplier_inn")
                and re.sub(r"\D", "", d["inn"]) == re.sub(r"\D", "", d["supplier_inn"])
            ),
            "npd_verified": bool(d.get("npd_verified_at")),
        })
    return result
