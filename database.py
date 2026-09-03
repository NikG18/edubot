import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

import database_legacy as _legacy
from database_legacy import *
from student_identity import (
    LINK_CODE_TTL,
    generate_link_code,
    hash_link_code,
    is_late_trial_cancellation,
    normalize_email,
    normalize_link_code,
)

MSK = _legacy.MSK


async def _ensure_pool():
    await _legacy._ensure_pool()


def _booking_stats_period(booking) -> Optional[tuple[int, int, int]]:
    """Возвращает (tutor_id, year, month) по дате занятия для точного перерасчёта."""
    if not booking:
        return None
    try:
        lesson_date = datetime.strptime(str(booking["date"]), "%d.%m.%Y")
        return int(booking["tutor_id"]), lesson_date.year, lesson_date.month
    except (KeyError, TypeError, ValueError):
        logging.warning("Не удалось определить месяц статистики для занятия %s", booking)
        return None


async def _recalculate_booking_stats(booking):
    period = _booking_stats_period(booking)
    if not period:
        return
    tutor_id, year, month = period
    await _legacy.recalculate_monthly_stats(tutor_id, year, month)


def _cancel_requires_refund(booking) -> bool:
    """Возврат нужен для реально оплаченного занятия, даже если оно уже completed."""
    if not booking:
        return False
    return (
        booking["status"] in {"paid", "completed"}
        and (booking["refund_status"] or "none") == "none"
        and int(booking["amount"] or 0) > 0
        and bool(booking["tinkoff_payment_id"])
    )


def _stats_counted_after_status_change(old_status: str, new_status: str,
                                       currently_counted: bool,
                                       refund_required: bool) -> bool:
    """Определяет участие занятия в статистике после смены статуса.

    Проведённое оплаченное занятие остаётся в статистике после административной
    отмены до тех пор, пока банк не подтвердит полный возврат.
    """
    if new_status == "completed":
        return True
    if new_status == "cancelled":
        return bool(old_status == "completed" and currently_counted and refund_required)
    return bool(currently_counted)


async def _student_for_account_conn(conn, platform: str, platform_user_id: int,
                                    *, create: bool = True) -> Optional[int]:
    platform = str(platform or "").lower()
    if platform not in {"telegram", "vk"}:
        raise ValueError("platform must be 'telegram' or 'vk'")
    platform_user_id = int(platform_user_id)
    student_id = await conn.fetchval(
        "SELECT student_id FROM student_accounts WHERE platform=$1 AND platform_user_id=$2",
        platform, platform_user_id,
    )
    if student_id or not create:
        return student_id

    # The caller holds a transaction. This prevents two bot processes from creating
    # two profiles for the same platform account during first use.
    lock_key = f"student-account:{platform}:{platform_user_id}"
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", lock_key)
    student_id = await conn.fetchval(
        "SELECT student_id FROM student_accounts WHERE platform=$1 AND platform_user_id=$2",
        platform, platform_user_id,
    )
    if student_id:
        return student_id
    student_id = await conn.fetchval("INSERT INTO student_profiles DEFAULT VALUES RETURNING id")
    await conn.execute(
        "INSERT INTO student_accounts(platform,platform_user_id,student_id) VALUES($1,$2,$3)",
        platform, platform_user_id, student_id,
    )
    return student_id


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
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS stats_counted BOOLEAN",
        ]
        for sql in migrations:
            await conn.execute(sql)
        await conn.execute("UPDATE bookings SET refund_status='none' WHERE refund_status IS NULL")
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('student-identity-schema-v1', 0))"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS student_profiles (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS email TEXT;
                CREATE TABLE IF NOT EXISTS student_accounts (
                    platform TEXT NOT NULL CHECK (platform IN ('telegram','vk')),
                    platform_user_id BIGINT NOT NULL,
                    student_id BIGINT NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY(platform, platform_user_id),
                    UNIQUE(student_id, platform)
                );
                CREATE TABLE IF NOT EXISTS account_link_tokens (
                    token_hash TEXT PRIMARY KEY,
                    source_student_id BIGINT NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
                    source_platform TEXT NOT NULL CHECK (source_platform IN ('telegram','vk')),
                    expires_at TIMESTAMPTZ NOT NULL,
                    consumed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_account_link_tokens_expiry
                ON account_link_tokens(expires_at);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_account_link_tokens_source
                ON account_link_tokens(source_student_id);
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS student_id BIGINT
                    REFERENCES student_profiles(id);
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booking_type TEXT NOT NULL
                    DEFAULT 'regular';
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS trial_consumed BOOLEAN NOT NULL
                    DEFAULT FALSE;
                ALTER TABLE bookings ADD COLUMN IF NOT EXISTS trial_email TEXT;
                ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS user_platform TEXT NOT NULL
                    DEFAULT 'telegram';
                ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS student_id BIGINT
                    REFERENCES student_profiles(id);
                ALTER TABLE pending_subscriptions ADD COLUMN IF NOT EXISTS student_id BIGINT
                    REFERENCES student_profiles(id);
                CREATE INDEX IF NOT EXISTS idx_bookings_student ON bookings(student_id, status);
                CREATE INDEX IF NOT EXISTS idx_trial_student_tutor
                    ON bookings(student_id, tutor_id)
                    WHERE booking_type='trial';
                CREATE INDEX IF NOT EXISTS idx_trial_email_tutor
                    ON bookings((LOWER(BTRIM(trial_email))), tutor_id)
                    WHERE booking_type='trial' AND trial_email IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_subscriptions_student
                    ON subscriptions(student_id, active);
                """
            )

            account_rows = await conn.fetch(
                """
                SELECT DISTINCT platform, platform_user_id
                FROM (
                    SELECT COALESCE(NULLIF(user_platform,''),'telegram') AS platform,
                           user_id AS platform_user_id FROM bookings
                    UNION
                    SELECT COALESCE(NULLIF(user_platform,''),'telegram'), user_id
                    FROM pending_subscriptions
                    UNION
                    SELECT COALESCE(NULLIF(user_platform,''),'telegram'), user_id
                    FROM subscriptions
                ) accounts
                WHERE platform IN ('telegram','vk')
                """
            )
            for account in account_rows:
                await _student_for_account_conn(
                    conn, account["platform"], account["platform_user_id"], create=True
                )

            await conn.execute(
                """
                UPDATE bookings b
                SET student_id=a.student_id
                FROM student_accounts a
                WHERE b.student_id IS NULL
                  AND a.platform=COALESCE(NULLIF(b.user_platform,''),'telegram')
                  AND a.platform_user_id=b.user_id;

                UPDATE subscriptions s
                SET student_id=a.student_id
                FROM student_accounts a
                WHERE s.student_id IS NULL
                  AND a.platform=COALESCE(NULLIF(s.user_platform,''),'telegram')
                  AND a.platform_user_id=s.user_id;

                UPDATE pending_subscriptions p
                SET student_id=a.student_id
                FROM student_accounts a
                WHERE p.student_id IS NULL
                  AND a.platform=COALESCE(NULLIF(p.user_platform,''),'telegram')
                  AND a.platform_user_id=p.user_id;

                UPDATE bookings
                SET booking_type='trial'
                WHERE subject LIKE 'Пробное: %' AND booking_type='regular';

                UPDATE bookings
                SET trial_consumed=TRUE
                WHERE booking_type='trial' AND status='completed';

                """
            )
            await conn.execute(
                "DELETE FROM account_link_tokens WHERE expires_at < NOW() - INTERVAL '1 day'"
            )
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
        # Миграция выполняется только для NULL, поэтому уже обработанные возвраты
        # не меняются при последующих перезапусках. Ранее отменённые проведённые
        # занятия сохраняем в статистике: это не затрагивает существующий тестовый
        # возврат задним числом.
        await conn.execute(
            """
            UPDATE bookings
            SET stats_counted=TRUE
            WHERE stats_counted IS NULL AND status='completed';

            UPDATE bookings b
            SET stats_counted=TRUE
            WHERE b.stats_counted IS NULL
              AND b.status='cancelled'
              AND EXISTS (
                  SELECT 1 FROM booking_events e
                  WHERE e.booking_id=b.id AND e.old_status='completed'
              );

            UPDATE bookings SET stats_counted=FALSE WHERE stats_counted IS NULL;
            ALTER TABLE bookings ALTER COLUMN stats_counted SET DEFAULT FALSE;
            ALTER TABLE bookings ALTER COLUMN stats_counted SET NOT NULL;
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
        "student_id": row["student_id"],
        "booking_type": row["booking_type"],
        "trial_consumed": bool(row["trial_consumed"]),
        "trial_email": row["trial_email"],
        "payment_notified": bool(row["payment_notified"]),
        "balance_credited": bool(row["balance_credited"]),
        "stats_counted": bool(row["stats_counted"]),
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


async def get_student_id(platform: str, platform_user_id: int, *, create: bool = True) -> Optional[int]:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            return await _student_for_account_conn(
                conn, platform, platform_user_id, create=create
            )


async def get_student_email(platform: str, platform_user_id: int) -> Optional[str]:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            student_id = await _student_for_account_conn(
                conn, platform, platform_user_id, create=True
            )
            email = await conn.fetchval(
                "SELECT email FROM student_profiles WHERE id=$1", student_id
            )
    return normalize_email(email) or None


async def set_student_email(platform: str, platform_user_id: int, email: str) -> str:
    normalized = normalize_email(email)
    if not normalized:
        raise ValueError("email is required")
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            student_id = await _student_for_account_conn(
                conn, platform, platform_user_id, create=True
            )
            await conn.execute(
                "UPDATE student_profiles SET email=$1 WHERE id=$2",
                normalized, student_id,
            )
    return normalized


async def get_bookings_for_account(platform: str, platform_user_id: int,
                                   statuses=None) -> dict[int, dict]:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            student_id = await _student_for_account_conn(
                conn, platform, platform_user_id, create=True
            )
            if statuses:
                rows = await conn.fetch(
                    "SELECT * FROM bookings WHERE student_id=$1 AND status=ANY($2::text[]) ORDER BY id",
                    student_id, list(statuses),
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM bookings WHERE student_id=$1 ORDER BY id", student_id
                )
    return {row["id"]: _booking_dict(row) for row in rows}


async def account_owns_booking(platform: str, platform_user_id: int, booking) -> bool:
    if not booking:
        return False
    student_id = await get_student_id(platform, platform_user_id, create=True)
    return bool(student_id and booking.get("student_id") == student_id)


async def is_trial_available(platform: str, platform_user_id: int, tutor_id: int,
                             email: str | None = None) -> bool:
    platform = str(platform or "").lower()
    if platform not in {"telegram", "vk"}:
        raise ValueError("platform must be 'telegram' or 'vk'")
    platform_user_id = int(platform_user_id)
    normalized_email = normalize_email(email)
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            student_id = await _student_for_account_conn(
                conn, platform, platform_user_id, create=True
            )
            used = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM bookings
                    WHERE tutor_id=$1 AND booking_type='trial'
                      AND (
                          status IN ('pending','confirmed','paid','completed')
                          OR trial_consumed=TRUE
                      )
                      AND (
                          student_id=$2
                          OR (
                              COALESCE(NULLIF(user_platform,''),'telegram')=$3
                              AND user_id=$4
                          )
                          OR (
                              $5::text <> ''
                              AND LOWER(BTRIM(trial_email))=$5
                          )
                      )
                )
                """,
                int(tutor_id), student_id, platform, platform_user_id,
                normalized_email,
            )
    return not bool(used)


async def get_linked_accounts(platform: str, platform_user_id: int) -> dict[str, int]:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            student_id = await _student_for_account_conn(
                conn, platform, platform_user_id, create=True
            )
            rows = await conn.fetch(
                "SELECT platform,platform_user_id FROM student_accounts WHERE student_id=$1",
                student_id,
            )
    return {row["platform"]: row["platform_user_id"] for row in rows}


async def create_account_link_code(platform: str, platform_user_id: int) -> dict:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            student_id = await _student_for_account_conn(
                conn, platform, platform_user_id, create=True
            )
            accounts = await conn.fetch(
                "SELECT platform,platform_user_id FROM student_accounts WHERE student_id=$1",
                student_id,
            )
            if len(accounts) > 1:
                return {"status": "already_linked", "accounts": {r["platform"]: r["platform_user_id"] for r in accounts}}

            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"link-code:{student_id}",
            )
            await conn.execute(
                "DELETE FROM account_link_tokens WHERE source_student_id=$1", student_id
            )
            for _ in range(3):
                code = generate_link_code()
                try:
                    async with conn.transaction():
                        await conn.execute(
                            """
                            INSERT INTO account_link_tokens
                                (token_hash,source_student_id,source_platform,expires_at)
                            VALUES($1,$2,$3,$4)
                            """,
                            hash_link_code(code), student_id, platform,
                            datetime.now(timezone.utc) + LINK_CODE_TTL,
                        )
                except asyncpg.UniqueViolationError:
                    continue
                return {"status": "created", "code": code, "expires_minutes": 10}
    raise RuntimeError("failed to create a unique account link code")


async def consume_account_link_code(target_platform: str, target_platform_user_id: int,
                                    code: str) -> dict:
    target_platform = str(target_platform or "").lower()
    normalized = normalize_link_code(code)
    if not normalized:
        return {"status": "invalid"}
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            token = await conn.fetchrow(
                """
                SELECT * FROM account_link_tokens
                WHERE token_hash=$1
                FOR UPDATE
                """,
                hash_link_code(normalized),
            )
            if not token or token["consumed_at"] is not None:
                return {"status": "invalid"}
            if token["expires_at"] <= datetime.now(timezone.utc):
                return {"status": "expired"}
            if token["source_platform"] == target_platform:
                return {"status": "same_platform"}

            target_student_id = await _student_for_account_conn(
                conn, target_platform, target_platform_user_id, create=True
            )
            source_student_id = token["source_student_id"]
            for student_id in sorted({source_student_id, target_student_id}):
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"student-merge:{student_id}",
                )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"link-code:{student_id}",
                )

            source_accounts = await conn.fetch(
                "SELECT platform,platform_user_id FROM student_accounts WHERE student_id=$1",
                source_student_id,
            )
            target_accounts = await conn.fetch(
                "SELECT platform,platform_user_id FROM student_accounts WHERE student_id=$1",
                target_student_id,
            )
            source_map = {r["platform"]: r["platform_user_id"] for r in source_accounts}
            target_map = {r["platform"]: r["platform_user_id"] for r in target_accounts}

            if source_student_id == target_student_id:
                await conn.execute(
                    "UPDATE account_link_tokens SET consumed_at=NOW() WHERE token_hash=$1",
                    token["token_hash"],
                )
                return {"status": "already_linked", "accounts": source_map}
            if target_platform in source_map or token["source_platform"] in target_map:
                return {"status": "account_conflict"}

            conflicting_tutor = await conn.fetchval(
                """
                SELECT tutor_id
                FROM bookings
                WHERE student_id=ANY($1::bigint[]) AND booking_type='trial'
                  AND (status IN ('pending','confirmed','paid','completed') OR trial_consumed=TRUE)
                GROUP BY tutor_id
                HAVING COUNT(DISTINCT student_id)>1
                LIMIT 1
                """,
                [source_student_id, target_student_id],
            )
            if conflicting_tutor is not None:
                return {"status": "trial_conflict", "tutor_id": conflicting_tutor}

            source_email = await conn.fetchval(
                "SELECT email FROM student_profiles WHERE id=$1", source_student_id
            )
            target_email = await conn.fetchval(
                "SELECT email FROM student_profiles WHERE id=$1", target_student_id
            )
            normalized_source_email = normalize_email(source_email)
            normalized_target_email = normalize_email(target_email)
            email_reset = bool(
                normalized_source_email
                and normalized_target_email
                and normalized_source_email != normalized_target_email
            )
            merged_email = None if email_reset else (
                normalized_source_email or normalized_target_email or None
            )
            await conn.execute(
                "UPDATE student_profiles SET email=$1 WHERE id=$2",
                merged_email, source_student_id,
            )

            await conn.execute(
                "UPDATE bookings SET student_id=$1 WHERE student_id=$2",
                source_student_id, target_student_id,
            )
            await conn.execute(
                "UPDATE subscriptions SET student_id=$1 WHERE student_id=$2",
                source_student_id, target_student_id,
            )
            await conn.execute(
                "UPDATE pending_subscriptions SET student_id=$1 WHERE student_id=$2",
                source_student_id, target_student_id,
            )
            await conn.execute(
                "UPDATE student_accounts SET student_id=$1 WHERE student_id=$2",
                source_student_id, target_student_id,
            )
            await conn.execute(
                "DELETE FROM account_link_tokens WHERE source_student_id=$1",
                target_student_id,
            )
            await conn.execute(
                "UPDATE account_link_tokens SET source_student_id=$1 WHERE source_student_id=$2",
                source_student_id, target_student_id,
            )
            await conn.execute(
                "UPDATE account_link_tokens SET consumed_at=NOW() WHERE token_hash=$1",
                token["token_hash"],
            )
            await conn.execute("DELETE FROM student_profiles WHERE id=$1", target_student_id)

            accounts = {**source_map, **target_map}
            return {
                "status": "linked",
                "accounts": accounts,
                "source_platform": token["source_platform"],
                "source_platform_user_id": source_map[token["source_platform"]],
                "email_reset": email_reset,
            }


async def get_student_subscriptions(user_id: int, user_platform: str = "telegram") -> list:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            student_id = await _student_for_account_conn(
                conn, user_platform, user_id, create=True
            )
            rows = await conn.fetch(
                """
                SELECT * FROM subscriptions
                WHERE student_id=$1 AND active=1 AND remaining_lessons>0
                ORDER BY id
                """,
                student_id,
            )
    return [dict(row) for row in rows]


async def add_pending_subscription(user_id: int, tutor_id: int, subject: str,
                                   total_lessons: int, discount_percent: int,
                                   total_price, payment_id: str,
                                   user_platform: str = "telegram") -> int:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            student_id = await _student_for_account_conn(
                conn, user_platform, user_id, create=True
            )
            return await conn.fetchval(
                """
                INSERT INTO pending_subscriptions
                    (user_id,tutor_id,subject,total_lessons,discount_percent,total_price,
                     payment_id,user_platform,student_id)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT(payment_id) DO UPDATE SET payment_id=EXCLUDED.payment_id
                RETURNING id
                """,
                user_id, tutor_id, subject, total_lessons, discount_percent,
                total_price, payment_id, user_platform, student_id,
            )


async def activate_subscription(payment_id: str) -> bool:
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            pending = await conn.fetchrow(
                "SELECT * FROM pending_subscriptions WHERE payment_id=$1 FOR UPDATE",
                payment_id,
            )
            if not pending:
                return False
            student_id = pending["student_id"]
            if not student_id:
                student_id = await _student_for_account_conn(
                    conn, pending["user_platform"] or "telegram", pending["user_id"], create=True
                )
            await conn.execute(
                """
                INSERT INTO subscriptions
                    (user_id,tutor_id,subject,total_lessons,remaining_lessons,
                     discount_percent,active,user_platform,student_id)
                VALUES($1,$2,$3,$4,$4,$5,1,$6,$7)
                """,
                pending["user_id"], pending["tutor_id"], pending["subject"],
                pending["total_lessons"], pending["discount_percent"],
                pending["user_platform"] or "telegram", student_id,
            )
            await conn.execute("DELETE FROM pending_subscriptions WHERE id=$1", pending["id"])
            return True


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
                      channel_msg_id=None, user_platform="telegram", booking_type="regular",
                      trial_email: str | None = None):
    """Создаёт бронь и сразу начинает неизменяемую историю событий."""
    await _ensure_pool()
    booking_type = str(booking_type or "regular").lower()
    if booking_type not in {"regular", "trial"}:
        raise ValueError("booking_type must be 'regular' or 'trial'")
    async with _legacy.pool.acquire() as conn:
        try:
            async with conn.transaction():
                student_id = await _student_for_account_conn(
                    conn, user_platform, user_id, create=True
                )
                if booking_type == "trial":
                    normalized_trial_email = normalize_email(trial_email)
                    normalized_platform = str(user_platform or "").lower()
                    if normalized_platform not in {"telegram", "vk"}:
                        raise ValueError("user_platform must be 'telegram' or 'vk'")
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"trial:{student_id}:{int(tutor_id)}",
                    )
                    if normalized_trial_email:
                        await conn.execute(
                            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                            f"trial-email:{normalized_trial_email}:{int(tutor_id)}",
                        )
                    already_has_trial = await conn.fetchval(
                        """
                        SELECT EXISTS(
                            SELECT 1 FROM bookings
                            WHERE tutor_id=$1 AND booking_type='trial'
                              AND (
                                  status IN ('pending','confirmed','paid','completed')
                                  OR trial_consumed=TRUE
                              )
                              AND (
                                  student_id=$2
                                  OR (
                                      COALESCE(NULLIF(user_platform,''),'telegram')=$3
                                      AND user_id=$4
                                  )
                                  OR (
                                      $5::text <> ''
                                      AND LOWER(BTRIM(trial_email))=$5
                                  )
                              )
                        )
                        """,
                        int(tutor_id), student_id, normalized_platform, int(user_id),
                        normalized_trial_email,
                    )
                    if already_has_trial:
                        return None
                else:
                    normalized_trial_email = None
                booking_id = await conn.fetchval(
                    """
                    INSERT INTO bookings
                        (tutor_id,user_id,username,subject,date,time_slot,status,reminded,
                         channel_msg_id,user_platform,student_id,booking_type,trial_email)
                    VALUES($1,$2,$3,$4,$5,$6,'pending',0,$7,$8,$9,$10,$11)
                    RETURNING id
                    """,
                    tutor_id, user_id, username, subject, date, time_slot,
                    channel_msg_id, user_platform, student_id, booking_type,
                    normalized_trial_email,
                )
                await _add_booking_event(
                    conn, booking_id, "created", None, "pending", "student", user_id,
                    {"platform": user_platform, "booking_type": booking_type},
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
        "trial_consumed", "stats_counted",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return await get_booking(booking_id)
    should_sync = any(k not in {"channel_msg_id", "reminded", "payment_msg_id"} for k in fields)
    stats_affected = False
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            old = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not old:
                return None
            target_status = fields.get("status", old["status"])
            refund_required = _cancel_requires_refund(old)
            if target_status != old["status"]:
                fields.setdefault(
                    "stats_counted",
                    _stats_counted_after_status_change(
                        old["status"], target_status, old["stats_counted"], refund_required
                    ),
                )
            if target_status == "cancelled" and old["status"] != "cancelled":
                fields.setdefault("cancelled_by", actor_type)
                fields.setdefault("cancel_reason", reason)
                fields.setdefault("cancelled_at", datetime.now(tz=MSK))
                if refund_required:
                    fields.setdefault("refund_status", "required")
                    fields.setdefault("refund_updated_at", datetime.now(tz=MSK))
            if old["booking_type"] == "trial" and old["status"] != target_status:
                if target_status == "completed":
                    fields["trial_consumed"] = True
                elif target_status == "cancelled" and actor_type == "student":
                    try:
                        fields["trial_consumed"] = is_late_trial_cancellation(
                            old["date"], old["time_slot"], datetime.now(tz=MSK).replace(tzinfo=None)
                        )
                    except (ValueError, TypeError, AttributeError):
                        logging.exception("Cannot calculate trial cancellation boundary for booking %s", booking_id)
                        fields["trial_consumed"] = True
            fields["updated_at"] = datetime.now(tz=MSK)
            set_clause = ", ".join(f"{key}=${i}" for i, key in enumerate(fields, start=1))
            values = list(fields.values()) + [booking_id]
            updated = await conn.fetchrow(
                f"UPDATE bookings SET {set_clause} WHERE id=${len(values)} RETURNING *",
                *values,
            )
            if "status" in fields and old["status"] != fields["status"]:
                stats_affected = "completed" in {old["status"], fields["status"]}
                details = {}
                if reason:
                    details["reason"] = reason
                if fields["status"] == "confirmed":
                    details.update({
                        "amount": fields.get("amount", old["amount"]),
                        "payment_id": fields.get("tinkoff_payment_id", old["tinkoff_payment_id"]),
                    })
                if old["booking_type"] == "trial" and "trial_consumed" in fields:
                    details["trial_consumed"] = bool(fields["trial_consumed"])
                await _add_booking_event(
                    conn, booking_id, event_type or fields["status"],
                    old["status"], fields["status"], actor_type, actor_id, details,
                )
                if fields["status"] == "cancelled" and refund_required:
                    await _add_booking_event(
                        conn, booking_id, "refund_pending", "cancelled", "cancelled",
                        actor_type, actor_id,
                        {"amount": old["amount"], "payment_id": old["tinkoff_payment_id"]},
                    )
    if stats_affected:
        await _recalculate_booking_stats(updated)
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
    stats_affected = False
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
            refund_required = _cancel_requires_refund(old)
            if new_status == "cancelled":
                cancelled_by = actor_type
                cancel_reason = reason
                cancelled_at = datetime.now(tz=MSK)
                if refund_required:
                    refund_status = "required"
                    refund_updated_at = datetime.now(tz=MSK)
            trial_consumed = bool(old["trial_consumed"])
            stats_counted = _stats_counted_after_status_change(
                old["status"], new_status, old["stats_counted"], refund_required
            )
            if old["booking_type"] == "trial":
                if new_status == "completed":
                    trial_consumed = True
                elif new_status == "cancelled" and actor_type == "student":
                    try:
                        trial_consumed = is_late_trial_cancellation(
                            old["date"], old["time_slot"], datetime.now(tz=MSK).replace(tzinfo=None)
                        )
                    except (ValueError, TypeError, AttributeError):
                        logging.exception("Cannot calculate trial cancellation boundary for booking %s", booking_id)
                        trial_consumed = True
            updated = await conn.fetchrow(
                """
                UPDATE bookings
                SET status=$1,cancelled_by=$2,cancel_reason=$3,cancelled_at=$4,
                    refund_status=$5,refund_updated_at=$6,trial_consumed=$7,
                    stats_counted=$8,updated_at=NOW()
                WHERE id=$9 RETURNING *
                """,
                new_status, cancelled_by, cancel_reason, cancelled_at,
                refund_status, refund_updated_at, trial_consumed, stats_counted, booking_id,
            )
            stats_affected = "completed" in {old["status"], new_status}
            event_details = dict(details or {})
            if reason:
                event_details["reason"] = reason
            if old["booking_type"] == "trial":
                event_details["trial_consumed"] = trial_consumed
            await _add_booking_event(
                conn, booking_id, event_type or new_status, old["status"], new_status,
                actor_type, actor_id, event_details,
            )
            if new_status == "cancelled" and refund_required:
                await _add_booking_event(
                    conn, booking_id, "refund_pending", "cancelled", "cancelled",
                    actor_type, actor_id,
                    {"amount": old["amount"], "payment_id": old["tinkoff_payment_id"]},
                )
    if stats_affected:
        await _recalculate_booking_stats(updated)
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
        expected_statuses={"pending", "confirmed", "paid", "completed"},
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


async def mark_booking_refund_pending(booking_id: int, admin_id: int) -> tuple[bool, Optional[dict]]:
    """Фиксирует, что банк принял запрос, но полный возврат ещё не подтверждён."""
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not row:
                return False, None
            refund_status = row["refund_status"] or "none"
            if refund_status == "refunded":
                return False, _booking_dict(row)
            if refund_status not in {"required", "pending"}:
                return False, _booking_dict(row)
            if refund_status == "pending":
                return False, _booking_dict(row)
            updated = await conn.fetchrow(
                """
                UPDATE bookings
                SET refund_status='pending',refund_updated_at=NOW(),updated_at=NOW()
                WHERE id=$1 RETURNING *
                """,
                booking_id,
            )
            await _add_booking_event(
                conn, booking_id, "refund_requested", row["status"], row["status"],
                "admin", admin_id,
                {"amount": row["amount"], "payment_id": row["tinkoff_payment_id"]},
            )
    await _sync_booking_record_safely(booking_id)
    return True, _booking_dict(updated)


async def confirm_booking_refunded(booking_id: int, *, actor_type: str = "payment",
                                   actor_id: int = None) -> tuple[bool, Optional[dict]]:
    """Идемпотентно подтверждает полный возврат и только тогда уменьшает статистику."""
    await _ensure_pool()
    async with _legacy.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not row:
                return False, None
            if (row["refund_status"] or "none") == "refunded":
                return False, _booking_dict(row)
            if (row["refund_status"] or "none") not in {"required", "pending"}:
                return False, _booking_dict(row)
            updated = await conn.fetchrow(
                """
                UPDATE bookings
                SET refund_status='refunded',refund_updated_at=NOW(),updated_at=NOW(),
                    balance_credited=FALSE,stats_counted=FALSE
                WHERE id=$1 RETURNING *
                """,
                booking_id,
            )
            await _add_booking_event(
                conn, booking_id, "refunded", row["status"], row["status"], actor_type, actor_id,
                {"amount": row["amount"], "payment_id": row["tinkoff_payment_id"]},
            )
    if row["stats_counted"]:
        await _recalculate_booking_stats(updated)
    await _sync_booking_record_safely(booking_id)
    return True, _booking_dict(updated)


async def mark_booking_refunded(booking_id: int, admin_id: int) -> bool:
    """Совместимая оболочка для старого административного обработчика."""
    changed, _booking = await confirm_booking_refunded(
        booking_id, actor_type="admin", actor_id=admin_id
    )
    return changed


async def cleanup_old_bookings():
    """Автоматически завершает оплаченные и бесплатные пробные уроки."""
    await _ensure_pool()
    now = datetime.now(MSK).replace(tzinfo=None)
    completed_ids = []
    affected_periods = set()
    async with _legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM bookings
            WHERE status='paid' OR (status='confirmed' AND booking_type='trial')
            """
        )
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
                if not current or not (
                    current["status"] == "paid"
                    or (current["status"] == "confirmed" and current["booking_type"] == "trial")
                ):
                    continue
                await conn.execute(
                    """
                    UPDATE bookings
                    SET status='completed',
                        trial_consumed=CASE WHEN booking_type='trial' THEN TRUE ELSE trial_consumed END,
                        stats_counted=TRUE,
                        updated_at=NOW()
                    WHERE id=$1
                    """,
                    row["id"],
                )
                await _add_booking_event(
                    conn, row["id"], "completed", current["status"], "completed", "system", None,
                    {"reason": "Время занятия завершилось"},
                )
                completed_ids.append(row["id"])
                affected_periods.add((row["tutor_id"], end_dt.year, end_dt.month))
    for tutor_id, year, month in affected_periods:
        await _legacy.recalculate_monthly_stats(tutor_id, year, month)
    for booking_id in completed_ids:
        await _sync_booking_record_safely(booking_id)
    return completed_ids
