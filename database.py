import asyncpg
import os
from typing import Dict, List, Optional
from datetime import datetime, timedelta

# Подставьте свою строку подключения от Neon или задайте через переменную окружения
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://botuser:ваш_пароль@ep-xxxx.us-east-2.aws.neon.tech/botdb?sslmode=require")

pool = None

# ------------------------------------------------------------
# Инициализация и создание таблиц
# ------------------------------------------------------------
async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    # Создаём таблицы, если их ещё нет (на случай первого запуска)
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS tutors (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            photo TEXT DEFAULT '',
            telegram_id BIGINT,
            description TEXT DEFAULT '',
            commission_percent INTEGER DEFAULT 25,
            commission_mode TEXT DEFAULT 'manual',
            inn TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS subjects (
            id SERIAL PRIMARY KEY,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS schedule_slots (
            id SERIAL PRIMARY KEY,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            time_slot TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            username TEXT NOT NULL,
            subject TEXT NOT NULL,
            date TEXT NOT NULL,
            time_slot TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            reminded INTEGER DEFAULT 0,
            channel_msg_id BIGINT DEFAULT NULL,
            amount INTEGER DEFAULT 0,
            commission_percent INTEGER DEFAULT 0,
            tinkoff_payment_id TEXT
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE CASCADE,
            subject TEXT NOT NULL,
            total_lessons INTEGER NOT NULL DEFAULT 0,
            remaining_lessons INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
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
            total_income REAL DEFAULT 0.0,
            commission_amount REAL DEFAULT 0.0,
            net_income REAL DEFAULT 0.0,
            commission_mode TEXT DEFAULT 'manual',
            commission_percent REAL DEFAULT 15,
            UNIQUE(tutor_id, year, month)
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            email TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS pending_email_requests (
            user_id BIGINT PRIMARY KEY,
            booking_id INTEGER NOT NULL
        );
        """)

async def close_db():
    global pool
    if pool:
        await pool.close()

# ------------------------------------------------------------
# TUTORS
# ------------------------------------------------------------
async def get_all_tutors() -> Dict[int, dict]:
    """Возвращает словарь {id: {name, photo, telegram_id, description, subjects: {}}}"""
    tutors = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM tutors")
        for row in rows:
            tid = row["id"]
            tutors[tid] = {
                "name": row["name"],
                "photo": row["photo"],
                "telegram_id": row["telegram_id"],
                "description": row["description"],
                "commission_percent": row["commission_percent"],
                "commission_mode": row["commission_mode"],
                "inn": row["inn"],
                "subjects": {}
            }
        subjects_rows = await conn.fetch("SELECT * FROM subjects")
        for s in subjects_rows:
            tid = s["tutor_id"]
            if tid in tutors:
                tutors[tid]["subjects"][s["name"]] = s["price"]
    return tutors

async def add_tutor(name, photo, telegram_id, description, commission_percent=25, commission_mode='manual', inn=''):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO tutors (name, photo, telegram_id, description, commission_percent, commission_mode, inn) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
            name, photo, telegram_id, description, commission_percent, commission_mode, inn
        )

async def update_tutor(tutor_id: int, **kwargs):
    """kwargs: name, photo, telegram_id, description, commission_percent, commission_mode, inn"""
    fields = {k: v for k, v in kwargs.items() if v is not None}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(fields))
    values = list(fields.values()) + [tutor_id]
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE tutors SET {set_clause} WHERE id = ${len(values)}",
            *values
        )

async def delete_tutor(tutor_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tutors WHERE id = $1", tutor_id)

async def get_tutor_first_lesson_date(tutor_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT MIN(date) FROM bookings WHERE tutor_id=$1 AND status='completed'",
            tutor_id
        )
        if row and row[0]:
            return datetime.strptime(row[0], "%d.%m.%Y")
        return None

async def calculate_auto_commission(tutor_id: int, year: int, month: int):
    """Возвращает (commission_percent, lessons_count_for_month)."""
    # Считаем количество завершённых занятий в указанном месяце
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) FROM bookings WHERE tutor_id=$1 AND status='completed' "
            "AND RIGHT(date, 4) = $2 AND SUBSTRING(date, 4, 2) = $3",
            tutor_id, str(year), f"{month:02d}"
        )
        lessons = row[0] if row else 0

    # Стаж в полных календарных месяцах до начала текущего месяца
    first_lesson = await get_tutor_first_lesson_date(tutor_id)
    if not first_lesson:
        return (25, lessons)

    first_of_current_month = datetime(year, month, 1)
    months_diff = (first_of_current_month.year - first_lesson.year) * 12 + \
                  (first_of_current_month.month - first_lesson.month)

    # Занятия за первые 60 дней работы
    first_60_days_end = first_lesson + timedelta(days=60)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) FROM bookings WHERE tutor_id=$1 AND status='completed' "
            "AND date BETWEEN $2 AND $3",
            tutor_id,
            first_lesson.strftime("%d.%m.%Y"),
            first_60_days_end.strftime("%d.%m.%Y")
        )
        first_60_days_lessons = row[0] if row else 0

    if lessons >= 41 and months_diff >= 4 and first_60_days_lessons > 100:
        return (15, lessons)
    elif lessons >= 21 and months_diff >= 2:
        return (20, lessons)
    else:
        return (25, lessons)

async def recalculate_monthly_stats(tutor_id: int, year: int, month: int):
    tutors = await get_all_tutors()
    tutor = tutors.get(tutor_id)
    if not tutor:
        return

    mode = tutor.get("commission_mode", "manual")
    if mode == "auto":
        percent, lessons = await calculate_auto_commission(tutor_id, year, month)
    else:
        percent = tutor.get("commission_percent", 15)
        # Количество занятий в этом месяце
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) FROM bookings WHERE tutor_id=$1 AND status='completed' "
                "AND RIGHT(date, 4) = $2 AND SUBSTRING(date, 4, 2) = $3",
                tutor_id, str(year), f"{month:02d}"
            )
            lessons = row[0] if row else 0

    # Суммарный доход (цена предмета) за месяц
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT SUM(s.price) FROM bookings b "
            "JOIN subjects s ON s.tutor_id = b.tutor_id AND s.name = b.subject "
            "WHERE b.tutor_id=$1 AND b.status='completed' "
            "AND RIGHT(b.date, 4) = $2 AND SUBSTRING(b.date, 4, 2) = $3",
            tutor_id, str(year), f"{month:02d}"
        )
        total_income = row[0] or 0.0

    commission = total_income * percent / 100
    net = total_income - commission

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO monthly_stats (tutor_id, year, month, lessons_count, total_income, "
            "commission_amount, net_income, commission_mode, commission_percent) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) "
            "ON CONFLICT(tutor_id, year, month) DO UPDATE SET "
            "lessons_count=EXCLUDED.lessons_count, total_income=EXCLUDED.total_income, "
            "commission_amount=EXCLUDED.commission_amount, net_income=EXCLUDED.net_income, "
            "commission_mode=EXCLUDED.commission_mode, commission_percent=EXCLUDED.commission_percent",
            tutor_id, year, month, lessons, total_income, commission, net, mode, percent
        )

# ------------------------------------------------------------
# SUBJECTS
# ------------------------------------------------------------
async def add_subject(tutor_id: int, name: str, price: int):
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO subjects (tutor_id, name, price) VALUES ($1, $2, $3)",
                           tutor_id, name, price)

async def update_subject(tutor_id: int, old_name: str, new_name: str = None, new_price: int = None):
    async with pool.acquire() as conn:
        if new_name is not None:
            await conn.execute("UPDATE subjects SET name = $1 WHERE tutor_id = $2 AND name = $3",
                               new_name, tutor_id, old_name)
        if new_price is not None:
            await conn.execute("UPDATE subjects SET price = $1 WHERE tutor_id = $2 AND name = $3",
                               new_price, tutor_id, old_name if new_name is None else new_name)

async def delete_subject(tutor_id: int, name: str):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM subjects WHERE tutor_id = $1 AND name = $2", tutor_id, name)

async def add_subscription(user_id, tutor_id, subject, total_lessons):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO subscriptions (user_id, tutor_id, subject, total_lessons, remaining_lessons) "
            "VALUES ($1, $2, $3, $4, $5)",
            user_id, tutor_id, subject, total_lessons, total_lessons
        )

async def get_student_subscriptions(user_id: int) -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM subscriptions WHERE user_id=$1 AND active=1 AND remaining_lessons>0",
            user_id
        )
        return [dict(row) for row in rows]

# ------------------------------------------------------------
# SCHEDULE
# ------------------------------------------------------------
async def get_all_schedules() -> Dict[int, Dict[str, List[str]]]:
    schedules = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tutor_id, day, time_slot FROM schedule_slots")
        for row in rows:
            tid, day, slot = row["tutor_id"], row["day"], row["time_slot"]
            schedules.setdefault(tid, {}).setdefault(day, []).append(slot)
    return schedules

async def get_schedule(tutor_id: int) -> Dict[str, List[str]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT day, time_slot FROM schedule_slots WHERE tutor_id=$1", tutor_id)
        schedule = {}
        for row in rows:
            day, slot = row["day"], row["time_slot"]
            schedule.setdefault(day, []).append(slot)
        return schedule

async def add_schedule_slot(tutor_id: int, day: str, time_slot: str):
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO schedule_slots (tutor_id, day, time_slot) VALUES ($1, $2, $3)",
                           tutor_id, day, time_slot)

async def delete_schedule_slot(tutor_id: int, day: str, time_slot: str):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM schedule_slots WHERE tutor_id=$1 AND day=$2 AND time_slot=$3",
                           tutor_id, day, time_slot)

async def block_day(tutor_id: int, day: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO blocked_days (tutor_id, day) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            tutor_id, day
        )

async def unblock_day(tutor_id: int, day: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM blocked_days WHERE tutor_id = $1 AND day = $2",
            tutor_id, day
        )

async def is_day_blocked(tutor_id: int, day: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM blocked_days WHERE tutor_id = $1 AND day = $2",
            tutor_id, day
        )
        return row is not None

# ------------------------------------------------------------
# BOOKINGS
# ------------------------------------------------------------
async def get_all_bookings() -> Dict[int, dict]:
    bookings = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM bookings")
        for row in rows:
            bid = row["id"]
            bookings[bid] = {
                "tutor_id": row["tutor_id"],
                "user_id": row["user_id"],
                "username": row["username"],
                "subject": row["subject"],
                "date": row["date"],
                "time_slot": row["time_slot"],
                "status": row["status"],
                "reminded": bool(row["reminded"]),
                "channel_msg_id": row["channel_msg_id"]
            }
    return bookings

async def add_booking(tutor_id, user_id, username, subject, date, time_slot, channel_msg_id=None):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO bookings (tutor_id, user_id, username, subject, date, time_slot, status, reminded, channel_msg_id) "
            "VALUES ($1, $2, $3, $4, $5, $6, 'pending', 0, $7) RETURNING id",
            tutor_id, user_id, username, subject, date, time_slot, channel_msg_id
        )

async def update_booking(booking_id, **kwargs):
    async with pool.acquire() as conn:
        fields = [f"{key}=${i+1}" for i, key in enumerate(kwargs)]
        values = list(kwargs.values()) + [booking_id]
        await conn.execute(
            f"UPDATE bookings SET {', '.join(fields)} WHERE id=${len(values)}",
            *values
        )

async def delete_booking(booking_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM bookings WHERE id = $1", booking_id)

async def get_tutor_financials(tutor_id: int, year: int = None, month: int = None) -> dict:
    if year is not None and month is not None:
        # Пытаемся взять из кеша
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM monthly_stats WHERE tutor_id=$1 AND year=$2 AND month=$3",
                tutor_id, year, month
            )
            if row:
                return {
                    "total_lessons": row["lessons_count"],
                    "total_income": row["total_income"],
                    "commission_amount": row["commission_amount"],
                    "net_income": row["net_income"],
                    "commission_percent": row["commission_percent"]
                }
        # Нет в кеше – пересчитываем
        await recalculate_monthly_stats(tutor_id, year, month)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM monthly_stats WHERE tutor_id=$1 AND year=$2 AND month=$3",
                tutor_id, year, month
            )
            if row:
                return {
                    "total_lessons": row["lessons_count"],
                    "total_income": row["total_income"],
                    "commission_amount": row["commission_amount"],
                    "net_income": row["net_income"],
                    "commission_percent": row["commission_percent"]
                }
        return {"total_lessons": 0, "total_income": 0, "commission_amount": 0, "net_income": 0, "commission_percent": 0}
    else:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT SUM(lessons_count), SUM(total_income), SUM(commission_amount), SUM(net_income) "
                "FROM monthly_stats WHERE tutor_id=$1", tutor_id
            )
            if row:
                return {
                    "total_lessons": row[0] or 0,
                    "total_income": row[1] or 0.0,
                    "commission_amount": row[2] or 0.0,
                    "net_income": row[3] or 0.0,
                    "commission_percent": "—"
                }
            return {"total_lessons": 0, "total_income": 0.0, "commission_amount": 0.0, "net_income": 0.0, "commission_percent": "—"}

async def get_all_tutors_stats():
    tutors = await get_all_tutors()
    stats = []
    for tid, tutor in tutors.items():
        fin = await get_tutor_financials(tid)
        stats.append({
            "tutor_id": tid,
            "name": tutor["name"],
            "total_lessons": fin["total_lessons"],
            "total_income": fin["total_income"],
            "commission": fin["commission_amount"],
            "net_income": fin["net_income"]
        })
    return stats

async def get_all_tutors_stats_by_month(year, month):
    tutors = await get_all_tutors()
    stats = []
    for tid, tutor in tutors.items():
        fin = await get_tutor_financials(tid, year, month)
        stats.append({
            "tutor_id": tid,
            "name": tutor["name"],
            "total_lessons": fin["total_lessons"],
            "total_income": fin["total_income"],
            "commission": fin["commission_amount"],
            "net_income": fin["net_income"]
        })
    return stats

async def get_students_stats():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT user_id, username FROM bookings")
        students = {row["user_id"]: row["username"] for row in rows}
    result = []
    for uid, name in students.items():
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) FROM bookings WHERE user_id=$1 AND status='completed'", uid)
            completed = row[0] if row else 0
        subs = await get_student_subscriptions(uid)
        remaining = sum(s["remaining_lessons"] for s in subs)
        result.append({
            "user_id": uid,
            "username": name,
            "completed_lessons": completed,
            "remaining_subscription_lessons": remaining
        })
    return result

async def get_students_stats_by_month(year, month):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, username, COUNT(*) as cnt FROM bookings "
            "WHERE status='completed' AND RIGHT(date, 4) = $1 AND SUBSTRING(date, 4, 2) = $2 "
            "GROUP BY user_id, username",
            str(year), f"{month:02d}"
        )
        result = []
        for row in rows:
            uid, name, cnt = row["user_id"], row["username"], row["cnt"]
            subs = await get_student_subscriptions(uid)
            remaining = sum(s["remaining_lessons"] for s in subs)
            result.append({
                "user_id": uid,
                "username": name,
                "completed_lessons": cnt,
                "remaining_subscription_lessons": remaining
            })
        return result

# ------------------------------------------------------------
# USERS (email)
# ------------------------------------------------------------
async def get_user_email(user_id: int) -> Optional[str]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT email FROM users WHERE user_id=$1", user_id)
        return row["email"] if row and row["email"] else None

async def set_user_email(user_id: int, email: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, email) VALUES ($1, $2) ON CONFLICT(user_id) DO UPDATE SET email = EXCLUDED.email",
            user_id, email
        )

async def set_pending_email_request(user_id: int, booking_id: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pending_email_requests (user_id, booking_id) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET booking_id = EXCLUDED.booking_id",
            user_id, booking_id
        )

async def get_pending_email_request(user_id: int) -> Optional[int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT booking_id FROM pending_email_requests WHERE user_id=$1", user_id)
        return row["booking_id"] if row else None

async def delete_pending_email_request(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM pending_email_requests WHERE user_id=$1", user_id)

async def add_lesson_to_balance(user_id: int, tutor_id: int, subject: str):
    """После успешной оплаты добавляет ученику 1 занятие в абонемент."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, remaining_lessons FROM subscriptions WHERE user_id=$1 AND tutor_id=$2 AND subject=$3 AND active=1",
            user_id, tutor_id, subject
        )
        if row:
            await conn.execute(
                "UPDATE subscriptions SET remaining_lessons = remaining_lessons + 1 WHERE id=$1",
                row["id"]
            )
        else:
            await conn.execute(
                "INSERT INTO subscriptions (user_id, tutor_id, subject, total_lessons, remaining_lessons) "
                "VALUES ($1, $2, $3, 1, 1)",
                user_id, tutor_id, subject
            )

# ------------------------------------------------------------
# Вспомогательная функция
# ------------------------------------------------------------
async def get_tutor_by_telegram_id(telegram_id: int) -> Optional[int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM tutors WHERE telegram_id = $1", telegram_id)
        return row["id"] if row else None
