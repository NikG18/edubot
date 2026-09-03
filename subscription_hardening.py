"""Auditable subscription lifecycle shared by Telegram and VK.

The legacy `subscriptions.remaining_lessons` value is preserved for UI compatibility,
but every reserved package lesson is additionally written to subscription_usages.
This makes retries, cross-platform actions and future closing receipts idempotent.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal, ROUND_HALF_UP

import database as _db
import payments
from fiscal_agent import get_tutor_phone

_SCHEMA_READY = False
_FINAL_RECEIPT_STATUSES = {"submitted", "sent"}


def _rub_to_kop(value) -> int:
    return int((Decimal(str(value or 0)) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def allocated_unit_amount(total_kop: int, total_lessons: int, unit_index: int) -> int:
    """Split a package total exactly across lessons without losing kopecks."""
    total_kop = int(total_kop)
    total_lessons = int(total_lessons)
    unit_index = int(unit_index)
    if total_kop <= 0 or total_lessons <= 0 or not 1 <= unit_index <= total_lessons:
        raise ValueError("invalid subscription allocation")
    base, remainder = divmod(total_kop, total_lessons)
    return base + (1 if unit_index <= remainder else 0)


async def ensure_subscription_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('subscription-ledger-v1', 0))")
            await conn.execute(
                """
                ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS payment_id TEXT;
                ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS total_price NUMERIC(14,2);
                ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS customer_email TEXT;
                ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS supplier_name TEXT;
                ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS supplier_inn TEXT;
                ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS supplier_phone TEXT;
                ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ;

                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS subscription_id INTEGER
                    REFERENCES subscriptions(id) ON DELETE SET NULL;
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS subscription_unit_index INTEGER;
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS subscription_unit_amount INTEGER;

                CREATE TABLE IF NOT EXISTS subscription_usages (
                    id BIGSERIAL PRIMARY KEY,
                    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE RESTRICT,
                    booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE RESTRICT,
                    unit_index INTEGER NOT NULL,
                    amount_kop INTEGER NOT NULL CHECK(amount_kop > 0),
                    status TEXT NOT NULL CHECK(status IN ('reserved','consumed','released')),
                    reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    consumed_at TIMESTAMPTZ,
                    released_at TIMESTAMPTZ,
                    UNIQUE(booking_id),
                    UNIQUE(subscription_id, unit_index)
                );
                CREATE INDEX IF NOT EXISTS idx_subscription_usages_subscription
                    ON subscription_usages(subscription_id, status, unit_index);

                CREATE TABLE IF NOT EXISTS subscription_closing_receipts (
                    id BIGSERIAL PRIMARY KEY,
                    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE RESTRICT,
                    booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE RESTRICT,
                    payment_id TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK(amount > 0),
                    status TEXT NOT NULL,
                    response JSONB NOT NULL DEFAULT '{}'::jsonb,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sent_at TIMESTAMPTZ,
                    UNIQUE(booking_id)
                );
                """
            )
    _SCHEMA_READY = True


async def activate_subscription(payment_id: str) -> bool:
    """Activate once while preserving the purchase/fiscal snapshot."""
    await ensure_subscription_schema()
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            pending = await conn.fetchrow(
                "SELECT * FROM pending_subscriptions WHERE payment_id=$1 FOR UPDATE",
                str(payment_id),
            )
            if not pending:
                # A duplicate CONFIRMED webhook after activation is successful/idempotent.
                existing = await conn.fetchval(
                    "SELECT id FROM subscriptions WHERE payment_id=$1", str(payment_id)
                )
                return bool(existing)

            existing = await conn.fetchval(
                "SELECT id FROM subscriptions WHERE payment_id=$1", str(payment_id)
            )
            if existing:
                await conn.execute("DELETE FROM pending_subscriptions WHERE id=$1", pending["id"])
                return True

            tutors = await _db.get_all_tutors()
            tutor = tutors.get(int(pending["tutor_id"]))
            if not tutor:
                logging.error("Cannot activate subscription %s: tutor missing", payment_id)
                return False
            inn = str(tutor.get("inn") or "").strip()
            direct = payments.is_operator_tutor(inn)
            phone = payments.OPERATOR_PHONE if direct else await get_tutor_phone(int(pending["tutor_id"]))
            if not direct and not phone:
                logging.error("Cannot activate subscription %s: supplier phone missing", payment_id)
                return False

            email = await conn.fetchval(
                "SELECT email FROM student_profiles WHERE id=$1", pending["student_id"]
            )
            if not email:
                logging.error("Cannot activate subscription %s: customer email missing", payment_id)
                return False

            await conn.execute(
                """
                INSERT INTO subscriptions
                    (user_id,tutor_id,subject,total_lessons,remaining_lessons,
                     discount_percent,active,user_platform,student_id,payment_id,total_price,
                     customer_email,supplier_name,supplier_inn,supplier_phone,activated_at)
                VALUES($1,$2,$3,$4,$4,$5,1,$6,$7,$8,$9,$10,$11,$12,$13,NOW())
                """,
                pending["user_id"], pending["tutor_id"], pending["subject"],
                pending["total_lessons"], pending["discount_percent"],
                pending["user_platform"], pending["student_id"], str(payment_id),
                pending["total_price"], str(email), tutor["name"], inn,
                phone or payments.OPERATOR_PHONE,
            )
            await conn.execute("DELETE FROM pending_subscriptions WHERE id=$1", pending["id"])
            return True


async def reserve_for_booking(booking_id: int) -> dict | None:
    """Reserve exactly one package unit for a pending regular booking."""
    await ensure_subscription_schema()
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            booking = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", int(booking_id))
            if not booking or booking["status"] != "pending" or booking["booking_type"] == "trial":
                return None

            existing = await conn.fetchrow(
                "SELECT * FROM subscription_usages WHERE booking_id=$1", int(booking_id)
            )
            if existing and existing["status"] in {"reserved", "consumed"}:
                return dict(existing)

            subscription = await conn.fetchrow(
                """
                SELECT * FROM subscriptions
                WHERE student_id=$1 AND tutor_id=$2 AND subject=$3
                  AND active=1 AND remaining_lessons>0 AND payment_id IS NOT NULL
                ORDER BY activated_at NULLS LAST, id
                LIMIT 1 FOR UPDATE
                """,
                booking["student_id"], booking["tutor_id"], booking["subject"],
            )
            if not subscription:
                return None

            used_count = int(await conn.fetchval(
                "SELECT COUNT(*) FROM subscription_usages WHERE subscription_id=$1",
                subscription["id"],
            ) or 0)
            unit_index = used_count + 1
            total_kop = _rub_to_kop(subscription["total_price"])
            amount_kop = allocated_unit_amount(total_kop, subscription["total_lessons"], unit_index)

            usage = await conn.fetchrow(
                """
                INSERT INTO subscription_usages(subscription_id,booking_id,unit_index,amount_kop,status)
                VALUES($1,$2,$3,$4,'reserved') RETURNING *
                """,
                subscription["id"], int(booking_id), unit_index, amount_kop,
            )
            await conn.execute(
                """
                UPDATE subscriptions
                SET remaining_lessons=remaining_lessons-1,
                    active=CASE WHEN remaining_lessons-1<=0 THEN 0 ELSE active END
                WHERE id=$1
                """,
                subscription["id"],
            )
            await conn.execute(
                """
                UPDATE bookings
                SET subscription_id=$1,subscription_unit_index=$2,
                    subscription_unit_amount=$3,amount=$3,updated_at=NOW()
                WHERE id=$4
                """,
                subscription["id"], unit_index, amount_kop, int(booking_id),
            )
            return dict(usage)


async def release_booking_unit(booking_id: int) -> bool:
    """Return an unconsumed reservation to the package exactly once."""
    await ensure_subscription_schema()
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            usage = await conn.fetchrow(
                "SELECT * FROM subscription_usages WHERE booking_id=$1 FOR UPDATE", int(booking_id)
            )
            if not usage or usage["status"] == "released":
                return False
            if usage["status"] == "consumed":
                return False
            await conn.execute(
                "UPDATE subscription_usages SET status='released',released_at=NOW() WHERE id=$1",
                usage["id"],
            )
            await conn.execute(
                "UPDATE subscriptions SET remaining_lessons=remaining_lessons+1,active=1 WHERE id=$1",
                usage["subscription_id"],
            )
            return True


async def consume_booking_unit(booking_id: int) -> bool:
    """Mark an already reserved package unit consumed, idempotently."""
    await ensure_subscription_schema()
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE subscription_usages
                SET status='consumed',consumed_at=NOW()
                WHERE booking_id=$1 AND status='reserved'
                RETURNING id
                """,
                int(booking_id),
            )
            if row:
                return True
            current = await conn.fetchval(
                "SELECT status FROM subscription_usages WHERE booking_id=$1", int(booking_id)
            )
            return current == "consumed"


async def get_booking_usage(booking_id: int) -> dict | None:
    await ensure_subscription_schema()
    async with _db._legacy.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT u.*, s.payment_id,s.customer_email,s.supplier_name,s.supplier_inn,
                   s.supplier_phone,s.subject
            FROM subscription_usages u
            JOIN subscriptions s ON s.id=u.subscription_id
            WHERE u.booking_id=$1
            """,
            int(booking_id),
        )
    return dict(row) if row else None


async def send_subscription_closing_receipt(booking_id: int, *, allow_noncompleted: bool = False) -> dict:
    """Submit one closing receipt for one consumed package lesson."""
    await ensure_subscription_schema()
    booking = await _db.get_booking(int(booking_id))
    if not booking:
        return {"ok": False, "reason": "booking_not_found"}
    if not allow_noncompleted and booking.get("status") != "completed":
        return {"ok": False, "reason": "lesson_not_confirmed_completed"}

    usage = await get_booking_usage(int(booking_id))
    if not usage or usage["status"] != "consumed":
        return {"ok": False, "reason": "subscription_unit_not_consumed"}

    async with _db._legacy.pool.acquire() as conn:
        claimed = await conn.fetchrow(
            """
            INSERT INTO subscription_closing_receipts
                (subscription_id,booking_id,payment_id,amount,status,attempt_count)
            VALUES($1,$2,$3,$4,'sending',1)
            ON CONFLICT(booking_id) DO NOTHING RETURNING *
            """,
            usage["subscription_id"], int(booking_id), usage["payment_id"], usage["amount_kop"],
        )
        if not claimed:
            existing = await conn.fetchrow(
                "SELECT * FROM subscription_closing_receipts WHERE booking_id=$1",
                int(booking_id),
            )
            if existing and existing["status"] in _FINAL_RECEIPT_STATUSES:
                return {"ok": True, "already_sent": True, "status": existing["status"]}
            return {"ok": False, "reason": "closing_receipt_already_claimed"}

    description = f"Занятие по абонементу: {booking['subject']} {booking['date']} {booking['time_slot']}"
    try:
        response = await payments.send_closing_receipt(
            payment_id=usage["payment_id"],
            amount_kop=int(usage["amount_kop"]),
            description=description,
            customer_email=usage["customer_email"],
            tutor_name=usage["supplier_name"],
            inn=usage["supplier_inn"],
            supplier_phone=usage["supplier_phone"],
        )
    except Exception as exc:
        logging.exception("Subscription closing receipt failed booking=%s", booking_id)
        response = {"exception": str(exc)}
        status = "unknown"
    else:
        status = "submitted" if response.get("Success") is True else ("failed" if response else "unknown")

    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE subscription_closing_receipts
            SET status=$1,response=$2::jsonb,updated_at=NOW(),
                sent_at=CASE WHEN $1='submitted' THEN NOW() ELSE NULL END
            WHERE id=$3 AND status='sending'
            """,
            status, json.dumps(response, ensure_ascii=False), claimed["id"],
        )
    return {"ok": status == "submitted", "already_sent": False, "status": status, "response": response}


def install_subscription_database_aliases(app) -> None:
    """Patch already imported aliases, including webhook_server's direct import."""
    legacy = app.legacy
    for target in (_db, _db._legacy, legacy):
        target.activate_subscription = activate_subscription
    try:
        import webhook_server
        webhook_server.activate_subscription = activate_subscription
    except Exception:
        logging.exception("Could not patch webhook subscription activation alias")
