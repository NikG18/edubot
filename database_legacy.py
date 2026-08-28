import asyncpg
import os
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL не задан")

pool: Optional[asyncpg.Pool] = None

MSK = ZoneInfo("Europe/Moscow")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_NAMES = {
    "monday": "Пн", "tuesday": "Вт", "wednesday": "Ср",
    "thursday": "Чт", "friday": "Пт", "saturday": "Сб", "sunday": "Вс"
}


async def _ensure_pool():
    if pool is None:
        raise RuntimeError("База данных не инициализирована. Вызовите init_db().")


async def init_db():
    global pool
    if pool is not None:
        return
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10, command_timeout=30)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS tutors (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            photo TEXT DEFAULT '',
            telegram_id BIGINT,
            vk_id BIGINT,
            description TEXT DEFAULT '',
            commission_percent INTEGER DEFAULT 25,
            commission_mode TEXT DEFAULT 'manual',
            inn TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS subjects (
            id SERIAL PRIMARY KEY,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price INTEGER NOT NULL CHECK (price > 0),
            UNIQUE(tutor_id, name)
        );

        CREATE TABLE IF NOT EXISTS schedule_slots (
            id SERIAL PRIMARY KEY,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            time_slot TEXT NOT NULL,
            UNIQUE(tutor_id, day, time_slot)
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            username TEXT NOT NULL,
            subject TEXT NOT NULL,
            date TEXT NOT NULL,
            time_slot TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reminded INTEGER NOT NULL DEFAULT 0,
            channel_msg_id BIGINT,
            amount INTEGER NOT NULL DEFAULT 0,
            commission_percent INTEGER NOT NULL DEFAULT 0,
            tinkoff_payment_id TEXT,
            payment_msg_id BIGINT,
            user_platform TEXT NOT NULL DEFAULT 'telegram',
            payment_notified BOOLEAN NOT NULL DEFAULT FALSE,
            balance_credited BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            subject TEXT NOT NULL,
            total_lessons INTEGER NOT NULL DEFAULT 0,
            remaining_lessons INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            discount_percent INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS blocked_days (
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            PRIMARY KEY (tutor_id, day)
        );

        CREATE TABLE IF NOT EXISTS monthly_stats (
            id SERIAL PRIMARY KEY,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            lessons_count INTEGER DEFAULT 0,
            total_income NUMERIC(14,2) DEFAULT 0,
            commission_amount NUMERIC(14,2) DEFAULT 0,
            net_income NUMERIC(14,2) DEFAULT 0,
            commission_mode TEXT DEFAULT 'manual',
            commission_percent NUMERIC(6,2) DEFAULT 25,
            UNIQUE(tutor_id, year, month)
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            email TEXT DEFAULT '',
            autopay_enabled BOOLEAN DEFAULT FALSE
        );

        CREATE TABLE IF NOT EXISTS pending_email_requests (
            user_id BIGINT PRIMARY KEY,
            booking_id INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pending_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            subject TEXT NOT NULL,
            total_lessons INTEGER NOT NULL,
            discount_percent INTEGER DEFAULT 0,
            total_price NUMERIC(14,2) NOT NULL,
            payment_id TEXT NOT NULL UNIQUE,
            user_platform TEXT NOT NULL DEFAULT 'telegram',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)

        # Безопасные миграции старой схемы.
        migrations = [
            "ALTER TABLE tutors ADD COLUMN IF NOT EXISTS vk_id BIGINT",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_msg_id BIGINT",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS amount INTEGER DEFAULT 0",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS commission_percent INTEGER DEFAULT 0",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS tinkoff_payment_id TEXT",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_platform TEXT DEFAULT 'telegram'",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_notified BOOLEAN DEFAULT FALSE",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS balance_credited BOOLEAN DEFAULT FALSE",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS discount_percent INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS autopay_enabled BOOLEAN DEFAULT FALSE",
            "ALTER TABLE pending_subscriptions ADD COLUMN IF NOT EXISTS user_platform TEXT DEFAULT 'telegram'",
        ]
        for sql in migrations:
            await conn.execute(sql)

        # Убираем уже существующие дубли активных слотов: оставляем самую раннюю запись.
        await conn.execute("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY tutor_id, date, time_slot
                       ORDER BY id
                   ) AS rn
            FROM bookings
            WHERE status IN ('pending', 'confirmed', 'paid')
        )
        UPDATE bookings b
        SET status='cancelled', updated_at=NOW()
        FROM ranked r
        WHERE b.id=r.id AND r.rn>1
        """)

        await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_booking_slot
        ON bookings(tutor_id, date, time_slot)
        WHERE status IN ('pending', 'confirmed', 'paid')
        """)
        await conn.execute("""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY tinkoff_payment_id ORDER BY id) rn
            FROM bookings WHERE tinkoff_payment_id IS NOT NULL
        )
        UPDATE bookings b SET tinkoff_payment_id=NULL, updated_at=NOW()
        FROM ranked r WHERE b.id=r.id AND r.rn>1
        """)

        await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_tinkoff_payment_id
        ON bookings(tinkoff_payment_id)
        WHERE tinkoff_payment_id IS NOT NULL
        """)


async def close_db():
    global pool
    if pool is not None:
        await pool.close()
        pool = None


# ------------------------------------------------------------
# TUTORS
# ------------------------------------------------------------
async def get_all_tutors() -> Dict[int, dict]:
    await _ensure_pool()
    tutors: Dict[int, dict] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM tutors ORDER BY id")
        for row in rows:
            tutors[row["id"]] = {
                "name": row["name"],
                "photo": row["photo"] or "",
                "telegram_id": row["telegram_id"],
                "vk_id": row["vk_id"],
                "description": row["description"] or "",
                "commission_percent": row["commission_percent"],
                "commission_mode": row["commission_mode"] or "manual",
                "inn": row["inn"] or "",
                "subjects": {},
            }
        subject_rows = await conn.fetch("SELECT tutor_id, name, price FROM subjects ORDER BY id")
        for row in subject_rows:
            if row["tutor_id"] in tutors:
                tutors[row["tutor_id"]]["subjects"][row["name"]] = row["price"]
    return tutors


async def add_tutor(name, photo="", telegram_id=None, description="", commission_percent=25,
                    commission_mode="manual", inn="", vk_id=None):
    await _ensure_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO tutors
               (name, photo, telegram_id, description, commission_percent, commission_mode, inn, vk_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
            name.strip(), photo or "", telegram_id, description or "", int(commission_percent),
            commission_mode, inn or "", vk_id
        )


async def update_tutor(tutor_id: int, **kwargs):
    await _ensure_pool()
    allowed = {"name", "photo", "telegram_id", "vk_id", "description",
               "commission_percent", "commission_mode", "inn"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{key}=${i}" for i, key in enumerate(fields, start=1))
    values = list(fields.values())
    values.append(tutor_id)
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE tutors SET {set_clause} WHERE id=${len(values)}",
            *values
        )


async def delete_tutor(tutor_id: int):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tutors WHERE id=$1", tutor_id)


async def get_tutor_by_telegram_id(telegram_id: int) -> Optional[int]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM tutors WHERE telegram_id=$1", telegram_id)


async def get_tutor_by_vk_id(vk_id: int) -> Optional[int]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM tutors WHERE vk_id=$1", vk_id)


# ------------------------------------------------------------
# SUBJECTS
# ------------------------------------------------------------
async def add_subject(tutor_id: int, name: str, price: int):
    await _ensure_pool()
    name = name.strip()
    if not name or price <= 0:
        raise ValueError("Некорректный предмет или цена")
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO subjects(tutor_id,name,price) VALUES($1,$2,$3)
               ON CONFLICT(tutor_id,name) DO UPDATE SET price=EXCLUDED.price""",
            tutor_id, name, int(price)
        )


async def update_subject(tutor_id: int, old_name: str, new_name: str = None, new_price: int = None):
    await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            current_name = old_name
            if new_name is not None:
                new_name = new_name.strip()
                if not new_name:
                    raise ValueError("Название предмета не может быть пустым")
                await conn.execute(
                    "UPDATE subjects SET name=$1 WHERE tutor_id=$2 AND name=$3",
                    new_name, tutor_id, old_name
                )
                current_name = new_name
            if new_price is not None:
                if int(new_price) <= 0:
                    raise ValueError("Цена должна быть положительной")
                await conn.execute(
                    "UPDATE subjects SET price=$1 WHERE tutor_id=$2 AND name=$3",
                    int(new_price), tutor_id, current_name
                )


async def delete_subject(tutor_id: int, name: str):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM subjects WHERE tutor_id=$1 AND name=$2", tutor_id, name)


# ------------------------------------------------------------
# SCHEDULE
# ------------------------------------------------------------
async def get_all_schedules() -> Dict[int, Dict[str, List[str]]]:
    await _ensure_pool()
    result: Dict[int, Dict[str, List[str]]] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tutor_id,day,time_slot FROM schedule_slots ORDER BY tutor_id,day,time_slot")
    for row in rows:
        result.setdefault(row["tutor_id"], {}).setdefault(row["day"], []).append(row["time_slot"])
    return result


async def get_schedule(tutor_id: int) -> Dict[str, List[str]]:
    await _ensure_pool()
    result: Dict[str, List[str]] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT day,time_slot FROM schedule_slots WHERE tutor_id=$1 ORDER BY day,time_slot", tutor_id
        )
    for row in rows:
        result.setdefault(row["day"], []).append(row["time_slot"])
    return result


async def add_schedule_slot(tutor_id: int, day: str, time_slot: str):
    await _ensure_pool()
    if day not in WEEKDAYS:
        raise ValueError("Некорректный день недели")
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO schedule_slots(tutor_id,day,time_slot) VALUES($1,$2,$3)
               ON CONFLICT(tutor_id,day,time_slot) DO NOTHING""",
            tutor_id, day, time_slot
        )


async def delete_schedule_slot(tutor_id: int, day: str, time_slot: str):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM schedule_slots WHERE tutor_id=$1 AND day=$2 AND time_slot=$3",
            tutor_id, day, time_slot
        )


async def block_day(tutor_id: int, day: str):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO blocked_days(tutor_id,day) VALUES($1,$2) ON CONFLICT DO NOTHING",
            tutor_id, day
        )


async def unblock_day(tutor_id: int, day: str):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM blocked_days WHERE tutor_id=$1 AND day=$2", tutor_id, day)


async def is_day_blocked(tutor_id: int, day: str) -> bool:
    await _ensure_pool()
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM blocked_days WHERE tutor_id=$1 AND day=$2)", tutor_id, day
        ))


async def get_booked_slots(tutor_id: int, date: str, exclude_booking_id: int = None) -> List[str]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        if exclude_booking_id is None:
            rows = await conn.fetch(
                """SELECT time_slot FROM bookings
                   WHERE tutor_id=$1 AND date=$2 AND status IN ('pending','confirmed','paid')""",
                tutor_id, date
            )
        else:
            rows = await conn.fetch(
                """SELECT time_slot FROM bookings
                   WHERE tutor_id=$1 AND date=$2 AND id<>$3
                     AND status IN ('pending','confirmed','paid')""",
                tutor_id, date, exclude_booking_id
            )
    return [r["time_slot"] for r in rows]


# ------------------------------------------------------------
# BOOKINGS
# ------------------------------------------------------------
def _booking_dict(row) -> dict:
    return {
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
    }


async def get_all_bookings() -> Dict[int, dict]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM bookings ORDER BY id")
    return {row["id"]: _booking_dict(row) for row in rows}


async def get_booking(booking_id: int) -> Optional[dict]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1", booking_id)
    return _booking_dict(row) if row else None


async def get_booking_id_by_payment_id(payment_id: str) -> Optional[int]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM bookings WHERE tinkoff_payment_id=$1", str(payment_id)
        )


async def add_booking(tutor_id, user_id, username, subject, date, time_slot,
                      channel_msg_id=None, user_platform='telegram'):
    """Атомарно создаёт активную бронь. Возвращает None, если слот уже занят."""
    await _ensure_pool()
    async with pool.acquire() as conn:
        try:
            return await conn.fetchval(
                """INSERT INTO bookings
                   (tutor_id,user_id,username,subject,date,time_slot,status,reminded,channel_msg_id,user_platform)
                   VALUES($1,$2,$3,$4,$5,$6,'pending',0,$7,$8)
                   RETURNING id""",
                tutor_id, user_id, username, subject, date, time_slot, channel_msg_id, user_platform
            )
        except asyncpg.UniqueViolationError:
            return None


async def move_booking_in_place(booking_id: int, new_date: str, new_time: str) -> bool:
    """Атомарно переносит existing confirmed booking, сохраняя payment_id и booking_id."""
    await _ensure_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT status FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
                if not row or row["status"] != "confirmed":
                    return False
                await conn.execute(
                    "UPDATE bookings SET date=$1,time_slot=$2,reminded=0,updated_at=NOW() WHERE id=$3",
                    new_date, new_time, booking_id
                )
                return True
        except asyncpg.UniqueViolationError:
            return False


async def reschedule_booking(booking_id: int, new_date: str, new_time: str,
                             new_status: str = "pending") -> Optional[int]:
    """Атомарный перенос. При конфликте новый слот не создаётся, старая бронь не отменяется."""
    await _ensure_pool()
    if new_status not in {"pending", "confirmed"}:
        raise ValueError("Некорректный статус новой записи")
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                old = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
                if not old or old["status"] not in ("confirmed",):
                    return None
                if old["date"] == new_date and old["time_slot"] == new_time:
                    return booking_id

                await conn.execute(
                    "UPDATE bookings SET status='cancelled', updated_at=NOW() WHERE id=$1",
                    booking_id
                )
                new_id = await conn.fetchval(
                    """INSERT INTO bookings
                       (tutor_id,user_id,username,subject,date,time_slot,status,reminded,user_platform,
                        amount,commission_percent,tinkoff_payment_id,payment_notified,balance_credited)
                       VALUES($1,$2,$3,$4,$5,$6,$7,0,$8,$9,$10,NULL,FALSE,FALSE)
                       RETURNING id""",
                    old["tutor_id"], old["user_id"], old["username"], old["subject"],
                    new_date, new_time, new_status, old["user_platform"],
                    old["amount"] if new_status == "confirmed" else 0,
                    old["commission_percent"] if new_status == "confirmed" else 0,
                )
                return new_id
        except asyncpg.UniqueViolationError:
            return None


async def update_booking(booking_id, **kwargs):
    await _ensure_pool()
    allowed = {
        "status", "reminded", "channel_msg_id", "amount", "commission_percent",
        "tinkoff_payment_id", "payment_msg_id", "user_platform", "payment_notified",
        "balance_credited", "date", "time_slot", "subject", "username"
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now(tz=MSK)
    set_clause = ", ".join(f"{key}=${i}" for i, key in enumerate(fields, start=1))
    values = list(fields.values()) + [booking_id]
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE bookings SET {set_clause} WHERE id=${len(values)}", *values)


async def delete_booking(booking_id: int):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM bookings WHERE id=$1", booking_id)


async def mark_booking_paid_once(booking_id: int) -> tuple[bool, Optional[dict]]:
    """Идемпотентно переводит confirmed -> paid. Абонемент здесь не изменяется."""
    await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not row:
                return False, None
            if row["status"] != "confirmed":
                return False, _booking_dict(row)
            row = await conn.fetchrow(
                """UPDATE bookings
                   SET status='paid', payment_notified=TRUE, balance_credited=TRUE, updated_at=NOW()
                   WHERE id=$1 RETURNING *""",
                booking_id
            )
            return True, _booking_dict(row)


async def mark_booking_payment_failed(booking_id: int) -> tuple[bool, Optional[dict]]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
            if not row:
                return False, None
            if row["status"] != "confirmed":
                return False, _booking_dict(row)
            row = await conn.fetchrow(
                "UPDATE bookings SET status='cancelled', updated_at=NOW() WHERE id=$1 RETURNING *",
                booking_id
            )
            return True, _booking_dict(row)


# ------------------------------------------------------------
# USERS / EMAIL
# ------------------------------------------------------------
async def get_user_email(user_id: int) -> Optional[str]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT email FROM users WHERE user_id=$1", user_id)
    return row["email"] if row and row["email"] else None


async def set_user_email(user_id: int, email: str):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users(user_id,email) VALUES($1,$2)
               ON CONFLICT(user_id) DO UPDATE SET email=EXCLUDED.email""",
            user_id, email
        )


async def set_pending_email_request(user_id: int, booking_id: int):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO pending_email_requests(user_id,booking_id) VALUES($1,$2)
               ON CONFLICT(user_id) DO UPDATE SET booking_id=EXCLUDED.booking_id""",
            user_id, booking_id
        )


async def get_pending_email_request(user_id: int) -> Optional[int]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT booking_id FROM pending_email_requests WHERE user_id=$1", user_id)


async def delete_pending_email_request(user_id: int):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM pending_email_requests WHERE user_id=$1", user_id)


async def is_autopay_enabled(user_id: int) -> bool:
    await _ensure_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT autopay_enabled FROM users WHERE user_id=$1", user_id)
    return bool(value)


async def set_autopay_enabled(user_id: int, enabled: bool):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users(user_id,autopay_enabled) VALUES($1,$2)
               ON CONFLICT(user_id) DO UPDATE SET autopay_enabled=EXCLUDED.autopay_enabled""",
            user_id, enabled
        )


# ------------------------------------------------------------
# SUBSCRIPTIONS
# ------------------------------------------------------------
async def add_subscription(user_id, tutor_id, subject, total_lessons, discount_percent=0):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO subscriptions
               (user_id,tutor_id,subject,total_lessons,remaining_lessons,discount_percent)
               VALUES($1,$2,$3,$4,$4,$5)""",
            user_id, tutor_id, subject, total_lessons, discount_percent
        )


async def get_student_subscriptions(user_id: int) -> list:
    await _ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscriptions
               WHERE user_id=$1 AND active=1 AND remaining_lessons>0 ORDER BY id""", user_id
        )
    return [dict(r) for r in rows]


async def add_lesson_to_balance(user_id: int, tutor_id: int, subject: str):
    """Совместимость со старым кодом. Для платежей используйте mark_booking_paid_once()."""
    await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT id FROM subscriptions
                   WHERE user_id=$1 AND tutor_id=$2 AND subject=$3 AND active=1
                   ORDER BY id LIMIT 1 FOR UPDATE""",
                user_id, tutor_id, subject
            )
            if row:
                await conn.execute(
                    "UPDATE subscriptions SET remaining_lessons=remaining_lessons+1 WHERE id=$1", row["id"]
                )
            else:
                await conn.execute(
                    """INSERT INTO subscriptions(user_id,tutor_id,subject,total_lessons,remaining_lessons)
                       VALUES($1,$2,$3,1,1)""",
                    user_id, tutor_id, subject
                )


async def decrement_subscription_lessons(user_id: int, tutor_id: int, subject: str) -> bool:
    await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT id FROM subscriptions
                   WHERE user_id=$1 AND tutor_id=$2 AND subject=$3
                     AND active=1 AND remaining_lessons>0
                   ORDER BY id LIMIT 1 FOR UPDATE""",
                user_id, tutor_id, subject
            )
            if not row:
                return False
            await conn.execute(
                """UPDATE subscriptions
                   SET remaining_lessons=remaining_lessons-1,
                       active=CASE WHEN remaining_lessons-1<=0 THEN 0 ELSE active END
                   WHERE id=$1""",
                row["id"]
            )
            return True


async def add_pending_subscription(user_id: int, tutor_id: int, subject: str, total_lessons: int,
                                   discount_percent: int, total_price, payment_id: str,
                                   user_platform: str = "telegram") -> int:
    await _ensure_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO pending_subscriptions
               (user_id,tutor_id,subject,total_lessons,discount_percent,total_price,payment_id,user_platform)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT(payment_id) DO UPDATE SET payment_id=EXCLUDED.payment_id
               RETURNING id""",
            user_id, tutor_id, subject, total_lessons, discount_percent, total_price, payment_id, user_platform
        )


async def activate_subscription(payment_id: str) -> bool:
    await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            pending = await conn.fetchrow(
                "SELECT * FROM pending_subscriptions WHERE payment_id=$1 FOR UPDATE", payment_id
            )
            if not pending:
                return False
            await conn.execute(
                """INSERT INTO subscriptions
                   (user_id,tutor_id,subject,total_lessons,remaining_lessons,discount_percent,active)
                   VALUES($1,$2,$3,$4,$4,$5,1)""",
                pending["user_id"], pending["tutor_id"], pending["subject"],
                pending["total_lessons"], pending["discount_percent"]
            )
            await conn.execute("DELETE FROM pending_subscriptions WHERE id=$1", pending["id"])
            return True


async def get_pending_subscription_by_payment_id(payment_id: str) -> Optional[dict]:
    await _ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM pending_subscriptions WHERE payment_id=$1", payment_id)
    return dict(row) if row else None


async def delete_pending_subscription(payment_id: str):
    await _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM pending_subscriptions WHERE payment_id=$1", payment_id)


# ------------------------------------------------------------
# COMMISSION / STATS
# ------------------------------------------------------------
async def get_tutor_first_lesson_date(tutor_id: int):
    await _ensure_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """SELECT MIN(to_date(date,'DD.MM.YYYY'))
               FROM bookings WHERE tutor_id=$1 AND status='completed'""",
            tutor_id
        )
    return datetime.combine(value, datetime.min.time()) if value else None


async def calculate_auto_commission(tutor_id: int, year: int, month: int):
    await _ensure_pool()
    async with pool.acquire() as conn:
        lessons = await conn.fetchval(
            """SELECT COUNT(*) FROM bookings
               WHERE tutor_id=$1 AND status='completed'
                 AND EXTRACT(YEAR FROM to_date(date,'DD.MM.YYYY'))=$2
                 AND EXTRACT(MONTH FROM to_date(date,'DD.MM.YYYY'))=$3""",
            tutor_id, year, month
        )
    lessons = int(lessons or 0)
    first_lesson = await get_tutor_first_lesson_date(tutor_id)
    if not first_lesson:
        return 25, lessons

    months_diff = (year - first_lesson.year) * 12 + (month - first_lesson.month)
    if lessons >= 41 and months_diff >= 4:
        return 15, lessons
    if lessons >= 21 and months_diff >= 2:
        return 20, lessons
    return 25, lessons


async def recalculate_monthly_stats(tutor_id: int, year: int, month: int):
    await _ensure_pool()
    tutors = await get_all_tutors()
    tutor = tutors.get(tutor_id)
    if not tutor:
        return

    if tutor.get("commission_mode") == "auto":
        percent, lessons = await calculate_auto_commission(tutor_id, year, month)
    else:
        percent = int(tutor.get("commission_percent", 25))
        async with pool.acquire() as conn:
            lessons = await conn.fetchval(
                """SELECT COUNT(*) FROM bookings
                   WHERE tutor_id=$1 AND status='completed'
                     AND EXTRACT(YEAR FROM to_date(date,'DD.MM.YYYY'))=$2
                     AND EXTRACT(MONTH FROM to_date(date,'DD.MM.YYYY'))=$3""",
                tutor_id, year, month
            )
        lessons = int(lessons or 0)

    # Важно: доход берём из зафиксированной суммы booking.amount, а не из текущей цены предмета.
    async with pool.acquire() as conn:
        total_income = await conn.fetchval(
            """SELECT COALESCE(SUM(
                       CASE WHEN b.amount > 0 THEN b.amount / 100.0 ELSE s.price::numeric END
                   ),0)
               FROM bookings b
               LEFT JOIN subjects s ON s.tutor_id=b.tutor_id AND s.name=b.subject
               WHERE b.tutor_id=$1 AND b.status='completed'
                 AND EXTRACT(YEAR FROM to_date(b.date,'DD.MM.YYYY'))=$2
                 AND EXTRACT(MONTH FROM to_date(b.date,'DD.MM.YYYY'))=$3""",
            tutor_id, year, month
        )
        total_income = float(total_income or 0)
        commission = total_income * percent / 100
        net = total_income - commission
        await conn.execute(
            """INSERT INTO monthly_stats
               (tutor_id,year,month,lessons_count,total_income,commission_amount,net_income,
                commission_mode,commission_percent)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
               ON CONFLICT(tutor_id,year,month) DO UPDATE SET
                 lessons_count=EXCLUDED.lessons_count,
                 total_income=EXCLUDED.total_income,
                 commission_amount=EXCLUDED.commission_amount,
                 net_income=EXCLUDED.net_income,
                 commission_mode=EXCLUDED.commission_mode,
                 commission_percent=EXCLUDED.commission_percent""",
            tutor_id, year, month, lessons, total_income, commission, net,
            tutor.get("commission_mode", "manual"), percent
        )


async def get_tutor_financials(tutor_id: int, year: int = None, month: int = None) -> dict:
    await _ensure_pool()
    if year is not None and month is not None:
        await recalculate_monthly_stats(tutor_id, year, month)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM monthly_stats WHERE tutor_id=$1 AND year=$2 AND month=$3",
                tutor_id, year, month
            )
        if not row:
            return {"total_lessons": 0, "total_income": 0.0, "commission_amount": 0.0,
                    "net_income": 0.0, "commission_percent": 0}
        return {
            "total_lessons": row["lessons_count"],
            "total_income": float(row["total_income"]),
            "commission_amount": float(row["commission_amount"]),
            "net_income": float(row["net_income"]),
            "commission_percent": float(row["commission_percent"]),
        }

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) FILTER (WHERE b.status='completed') AS lessons,
                      COALESCE(SUM(
                        CASE WHEN b.status='completed' THEN
                          CASE WHEN b.amount>0 THEN b.amount/100.0 ELSE s.price::numeric END
                        ELSE 0 END
                      ),0) AS total_income
               FROM bookings b
               LEFT JOIN subjects s ON s.tutor_id=b.tutor_id AND s.name=b.subject
               WHERE b.tutor_id=$1""",
            tutor_id
        )
    lessons = int(row["lessons"] or 0)
    total_income = float(row["total_income"] or 0)
    tutors = await get_all_tutors()
    tutor = tutors.get(tutor_id, {})
    percent = int(tutor.get("commission_percent", 25))
    commission = total_income * percent / 100
    return {
        "total_lessons": lessons,
        "total_income": float(total_income),
        "commission_amount": float(commission),
        "net_income": float(total_income - commission),
        "commission_percent": percent,
    }


async def get_all_tutors_stats():
    tutors = await get_all_tutors()
    result = []
    for tid, tutor in tutors.items():
        fin = await get_tutor_financials(tid)
        result.append({
            "tutor_id": tid, "name": tutor["name"], "total_lessons": fin["total_lessons"],
            "total_income": fin["total_income"], "commission": fin["commission_amount"],
            "net_income": fin["net_income"]
        })
    return result


async def get_all_tutors_stats_by_month(year, month):
    tutors = await get_all_tutors()
    result = []
    for tid, tutor in tutors.items():
        fin = await get_tutor_financials(tid, year, month)
        result.append({
            "tutor_id": tid, "name": tutor["name"], "total_lessons": fin["total_lessons"],
            "total_income": fin["total_income"], "commission": fin["commission_amount"],
            "net_income": fin["net_income"]
        })
    return result


async def get_students_stats():
    await _ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, MAX(username) AS username,
                   COUNT(*) FILTER (WHERE status='completed') AS completed_lessons
            FROM bookings GROUP BY user_id
        """)
    result = []
    for row in rows:
        subs = await get_student_subscriptions(row["user_id"])
        result.append({
            "user_id": row["user_id"],
            "username": row["username"],
            "completed_lessons": int(row["completed_lessons"] or 0),
            "remaining_subscription_lessons": sum(s["remaining_lessons"] for s in subs)
        })
    return result


async def get_students_stats_by_month(year, month):
    await _ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT user_id, MAX(username) AS username, COUNT(*) AS cnt
               FROM bookings
               WHERE status='completed'
                 AND EXTRACT(YEAR FROM to_date(date,'DD.MM.YYYY'))=$1
                 AND EXTRACT(MONTH FROM to_date(date,'DD.MM.YYYY'))=$2
               GROUP BY user_id""",
            year, month
        )
    result = []
    for row in rows:
        subs = await get_student_subscriptions(row["user_id"])
        result.append({
            "user_id": row["user_id"],
            "username": row["username"],
            "completed_lessons": int(row["cnt"]),
            "remaining_subscription_lessons": sum(s["remaining_lessons"] for s in subs)
        })
    return result


async def cleanup_old_bookings():
    """Переводит только paid-записи после окончания слота в completed."""
    await _ensure_pool()
    now = datetime.now(MSK).replace(tzinfo=None)
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id,tutor_id,date,time_slot FROM bookings WHERE status='paid'")
        completed = []
        affected = set()
        for row in rows:
            try:
                end_part = row["time_slot"].split("-")[-1].replace(".", ":")
                dt = datetime.strptime(f"{row['date']} {end_part}", "%d.%m.%Y %H:%M")
            except (ValueError, AttributeError):
                logging.warning("Некорректная дата/время у брони %s", row["id"])
                continue
            if dt < now:
                completed.append(row["id"])
                affected.add(row["tutor_id"])

        if completed:
            await conn.execute(
                "UPDATE bookings SET status='completed', updated_at=NOW() WHERE id=ANY($1::int[])",
                completed
            )

    for tid in affected:
        for bid in completed:
            pass
        await recalculate_monthly_stats(tid, now.year, now.month)
