import json
import logging
from datetime import datetime
from typing import Optional

import asyncpg

import database_legacy as _legacy
from database_legacy import *

MSK = _legacy.MSK


async def _ensure_pool():
    await _legacy._ensure_pool()


async def init_db():
    """Инициализирует старую схему и поверх неё безопасно добавляет учёт жизненного цикла занятий."""
    await _legacy.init_db()
    async with _legacy.pool.acquire() as conn:
        migrations = [
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS cancelled_by TEXT",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS cancel_reason TEXT",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS refund_status TEXT DEFAULT 'none'",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS refund_updated_at TIMESTAMPTZ",
        ]
        for sql in migrations:
            await conn.execute(sql)
        await conn.execute("UPDATE bookings SET refund_status='none' WHERE refund_status IS NULL")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS booking_events (
                id BIGSERIAL PRIMARY KEY,
                booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT,
                actor_type TEXT NOT NULL DEFAULT 'system',
                actor_id BIGINT,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_booking_events_booking
            ON booking_events(booking_id, created_at, id);
            """
        )
        # Старые записи не выдумываем: создаём один честный snapshot текущего состояния.
        await conn.execute(
            """
            INSERT INTO booking_events
                (booking_id,event_type,old_status,new_status,actor_type,details,created_at)
            SELECT b.id,'legacy_import',NULL,b.status,'system',
                   '{"note":"Запись существовала до внедрения журнала"}'::jsonb,
                   COALESCE(b.updated_at,b.created_at,NOW())
            FROM bookings b
            WHERE NOT EXISTS (
                SELECT 1 FROM booking_events e WHERE e.booking_id=b.id
            )
            """
        )


def _booking_dict(row) -> dict:
    return {
        "id": row["id"],
        "tutor_id": row["tutor_id"],
        "user_id": row["user_id"],
        "username": row["username"],
        "subject": row["subject"],
        "date": row["date"],
        "time_slot": row["time_slot"],
        "status": row["status"],
        "reminded": bool(row["reminded"]),
        "channel_msg_id": row["channel_msg_id"],
        "amount": row["amount"],
        "commission_percent": row["commission_percent"],
        "tinkoff_payment_id": row["tinkoff_payment_id"],
        "payment_msg_id": row["payment_msg_id"],
        "user_platform": row["user_platform"],
        "payment_notified": bool(row["payment_notified"]),
        "balance_credited": bool(row["balance_credited"]),
        "cancelled_by": row["cancelled_by"],
        "cancel_reason": row["cancel_reason"],
        "cancelled_at": row["cancelled_at"],
        "refund_status": row["refund_status"] or "none",
        "refund_updated_at": row["refund_updated_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def get_all_bookings():
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM bookings ORDER BY id")
    return {row["id"]: _booking_dict(row) for row in rows}


async def get_booking(booking_id: int) -> Optional[dict]:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1", booking_id)
    return _booking_dict(row) if row else None


async def _add_booking_event(conn, booking_id: int, event_type: str, old_status=None, new_status=None,
                             actor_type: str = "system", actor_id: int = None, details: dict = None):
    await conn.execute(
        """
        INSERT INTO booking_events
            (booking_id,event_type,old_status,new_status,actor_type,actor_id,details)
        VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)
        """,
        booking_id,
        event_type,
        old_status,
        new_status,
        actor_type,
        actor_id,
        json.dumps(details or {}, ensure_ascii=False),
    )


async def add_booking_event(booking_id: int, event_type: str, *, old_status=None, new_status=None,
                            actor_type: str = "system", actor_id: int = None, details: dict = None):
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        await _add_booking_event(conn, booking_id, event_type, old_status, new_status,
                                 actor_type, actor_id, details)


async def get_booking_events(booking_id: int) -> list:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM booking_events WHERE booking_id=$1 ORDER BY created_at ASC,id ASC",
            booking_id,
        )
    return [dict(row) for row in rows]


async def _sync_booking_record_safely(booking_id: int):
    try:
        from booking_records import sync_booking_record
        await sync_booking_record(booking_id)
    except Exception:
        logging.exception("records_channel sync failed for booking %s", booking_id)


async def add_booking(tutor_id, user_id, username, subject, date, time_slot,
                      channel_msg_id=None, user_platform="telegram"):
    """Создаёт бронь и сразу начинает неизменяемую историю событий."""
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        try:
            async with conn.transaction():
                booking_id = await conn.fetchval(
                    """
                    INSERT INTO bookings
                        (tutor_id,user_id,username,subject,date,time_slot,status,reminded,
                         channel_msg_id,user_platform)
                    VALUES($1,$2,$3,$4,$5,$6,'pending',0,$7,$8)
                    RETURNING id
                    """,
                    tutor_id, user_id, username, subject, date, time_slot,
                    channel_msg_id, user_platform,
                )
                await _add_booking_event(
                    conn, booking_id, "created", None, "pending", "student", user_id,
                    {"platform": user_platform},
                )
        except asyncpg.UniqueViolationError:
            return None
    await _sync_booking_record_safely(booking_id)
    return booking_id


async def update_booking(booking_id, **kwargs):
    """Совместимый update + аудит смены статуса.

    Служебные kwargs начинаются с подчёркивания и не пишутся в bookings:
    _actor_type, _actor_id, _event_type, _reason.
    """
    await _ensure_pool()
    actor_type = kwargs.pop("_actor_type", "system")
    actor_id = kwargs.pop("_actor_id", None)
    event_type = kwargs.pop("_event_type", None)
    reason = kwargs.pop("_reason", None)
    allowed = {
        "status", "reminded", "channel_msg_id", "amount", "commission_percent",
        "tinkoff_payment_id", "payment_msg_id", "user_platform", "payment_notified",
        "balance_credited", "date", "time_slot", "subject", "username",
        "cancelled_by", "cancel_reason", "cancelled_at", "refund_status", "refund_updated_at",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return await get_booking(booking_id)
    should_sync = any(k not in {"channel_msg_id", "reminded", "payment_msg_id"} for k in fields)
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            old = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not old:
                return None
            target_status = fields.get("status", old["status"])
            if target_status == "cancelled" and old["status"] != "cancelled":
                fields.setdefault("cancelled_by", actor_type)
                fields.setdefault("cancel_reason", reason)
                fields.setdefault("cancelled_at", datetime.now(tz=MSK))
                if old["status"] == "paid" and (old["refund_status"] or "none") == "none":
                    fields.setdefault("refund_status", "required")
                    fields.setdefault("refund_updated_at", datetime.now(tz=MSK))
            fields["updated_at"] = datetime.now(tz=MSK)
            set_clause = ", ".join(f"{key}=${i}" for i, key in enumerate(fields, start=1))
            values = list(fields.values()) + [booking_id]
            updated = await conn.fetchrow(
                f"UPDATE bookings SET {set_clause} WHERE id=${len(values)} RETURNING *",
                *values,
            )
            if "status" in fields and old["status"] != fields["status"]:
                details = {}
                if reason:
                    details["reason"] = reason
                if fields["status"] == "confirmed":
                    details.update({
                        "amount": fields.get("amount", old["amount"]),
                        "payment_id": fields.get("tinkoff_payment_id", old["tinkoff_payment_id"]),
                    })
                await _add_booking_event(
                    conn, booking_id, event_type or fields["status"],
                    old["status"], fields["status"], actor_type, actor_id, details,
                )
                if fields["status"] == "cancelled" and old["status"] == "paid":
                    await _add_booking_event(
                        conn, booking_id, "refund_pending", "cancelled", "cancelled",
                        actor_type, actor_id,
                        {"amount": old["amount"], "payment_id": old["tinkoff_payment_id"]},
                    )
    if should_sync:
        await _sync_booking_record_safely(booking_id)
    return _booking_dict(updated)


async def change_booking_status(booking_id: int, new_status: str, *, event_type: str = None,
                                actor_type: str = "system", actor_id: int = None,
                                reason: str = None, expected_statuses=None, details: dict = None):
    allowed = {"pending", "confirmed", "paid", "completed", "cancelled"}
    if new_status not in allowed:
        raise ValueError(f"Некорректный статус: {new_status}")
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            old = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not old:
                return False, None
            if expected_statuses is not None and old["status"] not in set(expected_statuses):
                return False, _booking_dict(old)
            if old["status"] == new_status:
                return False, _booking_dict(old)
            refund_status = old["refund_status"] or "none"
            refund_updated_at = old["refund_updated_at"]
            cancelled_by = old["cancelled_by"]
            cancel_reason = old["cancel_reason"]
            cancelled_at = old["cancelled_at"]
            if new_status == "cancelled":
                cancelled_by = actor_type
                cancel_reason = reason
                cancelled_at = datetime.now(tz=MSK)
                if old["status"] == "paid" and refund_status == "none":
                    refund_status = "required"
                    refund_updated_at = datetime.now(tz=MSK)
            updated = await conn.fetchrow(
                """
                UPDATE bookings
                SET status=$1,cancelled_by=$2,cancel_reason=$3,cancelled_at=$4,
                    refund_status=$5,refund_updated_at=$6,updated_at=NOW()
                WHERE id=$7 RETURNING *
                """,
                new_status, cancelled_by, cancel_reason, cancelled_at,
                refund_status, refund_updated_at, booking_id,
            )
            event_details = dict(details or {})
            if reason:
                event_details["reason"] = reason
            await _add_booking_event(
                conn, booking_id, event_type or new_status, old["status"], new_status,
                actor_type, actor_id, event_details,
            )
            if new_status == "cancelled" and old["status"] == "paid" and refund_status == "required":
                await _add_booking_event(
                    conn, booking_id, "refund_pending", "cancelled", "cancelled",
                    actor_type, actor_id,
                    {"amount": old["amount"], "payment_id": old["tinkoff_payment_id"]},
                )
    await _sync_booking_record_safely(booking_id)
    return True, _booking_dict(updated)


async def cancel_booking_record(booking_id: int, *, actor_type: str, actor_id: int = None,
                                reason: str = None, expected_statuses=None):
    return await change_booking_status(
        booking_id,
        "cancelled",
        event_type="cancelled",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        expected_statuses=expected_statuses or {"pending", "confirmed", "paid"},
    )


async def admin_cancel_booking(booking_id: int, admin_id: int,
                               reason: str = "Отменено администратором"):
    return await cancel_booking_record(
        booking_id,
        actor_type="admin",
        actor_id=admin_id,
        reason=reason,
        expected_statuses={"pending", "confirmed", "paid"},
    )


async def delete_booking(booking_id: int):
    """Старые reject-handlers больше не уничтожают данные."""
    changed, booking = await change_booking_status(
        booking_id,
        "cancelled",
        event_type="rejected",
        actor_type="tutor",
        reason="Заявка отклонена преподавателем",
        expected_statuses={"pending"},
    )
    return booking if changed else booking


async def move_booking_in_place(booking_id: int, new_date: str, new_time: str,
                                actor_type: str = "tutor", actor_id: int = None) -> bool:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        try:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
                if not row or row["status"] not in {"confirmed", "paid"}:
                    return False
                if row["date"] == new_date and row["time_slot"] == new_time:
                    return True
                await conn.execute(
                    "UPDATE bookings SET date=$1,time_slot=$2,reminded=0,updated_at=NOW() WHERE id=$3",
                    new_date, new_time, booking_id,
                )
                await _add_booking_event(
                    conn, booking_id, "rescheduled", row["status"], row["status"],
                    actor_type, actor_id,
                    {"old_date": row["date"], "old_time": row["time_slot"],
                     "new_date": new_date, "new_time": new_time},
                )
        except asyncpg.UniqueViolationError:
            return False
    await _sync_booking_record_safely(booking_id)
    return True


async def reschedule_booking(booking_id: int, new_date: str, new_time: str,
                             new_status: str = "pending") -> Optional[int]:
    """Ученический перенос сохраняет booking_id и историю вместо создания новой строки."""
    if new_status not in {"pending", "confirmed"}:
        raise ValueError("Некорректный статус новой записи")
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        try:
            async with conn.transaction():
                old = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
                if not old or old["status"] != "confirmed":
                    return None
                if old["date"] == new_date and old["time_slot"] == new_time:
                    return booking_id
                await conn.execute(
                    """
                    UPDATE bookings
                    SET date=$1,time_slot=$2,status=$3,reminded=0,
                        tinkoff_payment_id=CASE WHEN $3='pending' THEN NULL ELSE tinkoff_payment_id END,
                        payment_msg_id=CASE WHEN $3='pending' THEN NULL ELSE payment_msg_id END,
                        amount=CASE WHEN $3='pending' THEN 0 ELSE amount END,
                        commission_percent=CASE WHEN $3='pending' THEN 0 ELSE commission_percent END,
                        updated_at=NOW()
                    WHERE id=$4
                    """,
                    new_date, new_time, new_status, booking_id,
                )
                await _add_booking_event(
                    conn, booking_id, "rescheduled", old["status"], new_status,
                    "student", old["user_id"],
                    {"old_date": old["date"], "old_time": old["time_slot"],
                     "new_date": new_date, "new_time": new_time},
                )
        except asyncpg.UniqueViolationError:
            return None
    await _sync_booking_record_safely(booking_id)
    return booking_id


async def mark_booking_paid_once(booking_id: int) -> tuple[bool, Optional[dict]]:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not row:
                return False, None
            if row["status"] != "confirmed":
                return False, _booking_dict(row)
            updated = await conn.fetchrow(
                """
                UPDATE bookings
                SET status='paid',payment_notified=TRUE,balance_credited=TRUE,updated_at=NOW()
                WHERE id=$1 RETURNING *
                """,
                booking_id,
            )
            await _add_booking_event(
                conn, booking_id, "paid", "confirmed", "paid", "payment", None,
                {"payment_id": row["tinkoff_payment_id"], "amount": row["amount"]},
            )
    await _sync_booking_record_safely(booking_id)
    return True, _booking_dict(updated)


async def mark_booking_payment_failed(booking_id: int) -> tuple[bool, Optional[dict]]:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not row:
                return False, None
            if row["status"] != "confirmed":
                return False, _booking_dict(row)
            updated = await conn.fetchrow(
                """
                UPDATE bookings
                SET status='cancelled',cancelled_by='payment',
                    cancel_reason='Платёж отклонён или отменён',cancelled_at=NOW(),updated_at=NOW()
                WHERE id=$1 RETURNING *
                """,
                booking_id,
            )
            await _add_booking_event(
                conn, booking_id, "payment_failed", "confirmed", "cancelled", "payment", None,
                {"payment_id": row["tinkoff_payment_id"]},
            )
    await _sync_booking_record_safely(booking_id)
    return True, _booking_dict(updated)


async def mark_booking_refunded(booking_id: int, admin_id: int) -> bool:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not row or (row["refund_status"] or "none") not in {"required", "pending"}:
                return False
            await conn.execute(
                "UPDATE bookings SET refund_status='refunded',refund_updated_at=NOW(),updated_at=NOW() WHERE id=$1",
                booking_id,
            )
            await _add_booking_event(
                conn, booking_id, "refunded", row["status"], row["status"], "admin", admin_id,
                {"amount": row["amount"], "payment_id": row["tinkoff_payment_id"]},
            )
    await _sync_booking_record_safely(booking_id)
    return True


async def cleanup_old_bookings():
    """Автоматически завершает оплаченные уроки и фиксирует время завершения."""
    await _ensure_pool()
    now = datetime.now(MSK).replace(tzinfo=None)
    completed_ids = []
    affected = set()
    async with _legacy.pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM bookings WHERE status='paid'")
        for row in rows:
            try:
                end_part = row["time_slot"].split("-")[-1].replace(".", ":")
                end_dt = datetime.strptime(f"{row['date']} {end_part}", "%d.%m.%Y %H:%M")
            except (ValueError, AttributeError):
                logging.warning("Некорректная дата/время у брони %s", row["id"])
                continue
            if end_dt >= now:
                continue
            async with conn.transaction():
                current = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", row["id"])
                if not current or current["status"] != "paid":
                    continue
                await conn.execute(
                    "UPDATE bookings SET status='completed',updated_at=NOW() WHERE id=$1",
                    row["id"],
                )
                await _add_booking_event(
                    conn, row["id"], "completed", "paid", "completed", "system", None,
                    {"reason": "Время занятия завершилось"},
                )
                completed_ids.append(row["id"])
                affected.add(row["tutor_id"])
    for tutor_id in affected:
        await _legacy.recalculate_monthly_stats(tutor_id, now.year, now.month)
    for booking_id in completed_ids:
        await _sync_booking_record_safely(booking_id)
    return completed_ids
