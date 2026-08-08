import aiosqlite
from typing import Dict, List, Optional
from datetime import datetime, timedelta

DB_PATH = "bot.db"


# ------------------------------------------------------------
# Инициализация БД и создание таблиц
# ------------------------------------------------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS tutors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            photo TEXT DEFAULT '',
            telegram_id INTEGER,
            description TEXT DEFAULT '',
            commission_percent INTEGER DEFAULT 25,
            commission_mode TEXT DEFAULT 'manual'
        );

        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tutor_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            FOREIGN KEY (tutor_id) REFERENCES tutors(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS schedule_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tutor_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            time_slot TEXT NOT NULL,
            FOREIGN KEY (tutor_id) REFERENCES tutors(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tutor_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            subject TEXT NOT NULL,
            date TEXT NOT NULL,
            time_slot TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            reminded INTEGER DEFAULT 0,
            channel_msg_id INTEGER DEFAULT NULL,
            FOREIGN KEY (tutor_id) REFERENCES tutors(id) ON DELETE CASCADE
        );


        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tutor_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            total_lessons INTEGER NOT NULL DEFAULT 0,
            remaining_lessons INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (tutor_id) REFERENCES tutors(id) ON DELETE CASCADE
        );


        CREATE TABLE IF NOT EXISTS blocked_days (
            tutor_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            PRIMARY KEY (tutor_id, day),
            FOREIGN KEY (tutor_id) REFERENCES tutors(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS monthly_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tutor_id INTEGER NOT NULL,
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

        """)
        await db.commit()


async def migrate_database():
    async with aiosqlite.connect("bot.db") as db:
        cursor = await db.execute("PRAGMA table_info(tutors)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "commission_mode" not in columns:
            await db.execute("ALTER TABLE tutors ADD COLUMN commission_mode TEXT DEFAULT 'manual'")
        cursor1 = await db.execute("PRAGMA table_info(bookings)")
        cols1 = [row[1] for row in await cursor1.fetchall()]
        if "amount" not in cols1:
            await db.execute("ALTER TABLE bookings ADD COLUMN amount INTEGER DEFAULT 0")
        if "commission_percent" not in cols1:
            await db.execute("ALTER TABLE bookings ADD COLUMN commission_percent INTEGER DEFAULT 0")
        if "tinkoff_payment_id" not in cols1:
            await db.execute("ALTER TABLE bookings ADD COLUMN tinkoff_payment_id TEXT")

        # Поле ИНН для tutors
        cursor2 = await db.execute("PRAGMA table_info(tutors)")
        cols2 = [row[1] for row in await cursor2.fetchall()]
        if "inn" not in cols2:
            await db.execute("ALTER TABLE tutors ADD COLUMN inn TEXT DEFAULT ''")

        await db.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        email TEXT DEFAULT ''
                    )
                """)

        await db.commit()
        print("Миграция базы данных завершена.")


# ------------------------------------------------------------
# TUTORS
# ------------------------------------------------------------
async def get_all_tutors() -> Dict[int, dict]:
    """Возвращает словарь {id: {name, photo, telegram_id, description, subjects: {}}}"""
    tutors = {}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tutors")
        async for row in cursor:
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
        cursor2 = await db.execute("SELECT * FROM subjects")
        async for row in cursor2:
            tid = row["tutor_id"]
            if tid in tutors:
                tutors[tid]["subjects"][row["name"]] = row["price"]
    return tutors


async def add_tutor(name, photo, telegram_id, description, commission_percent=25, commission_mode='manual', inn=''):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO tutors (name, photo, telegram_id, description, commission_percent, commission_mode, inn) VALUES (?,?,?,?,?,?,?)",
            (name, photo, telegram_id, description, commission_percent, commission_mode, inn)
        )
        await db.commit()
        return cur.lastrowid


async def update_tutor(tutor_id: int, **kwargs):
    """kwargs: name, photo, telegram_id, description, commission_percent, commission_mode, inn"""
    fields = {k: v for k, v in kwargs.items() if v is not None}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [tutor_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE tutors SET {set_clause} WHERE id = ?", values)
        await db.commit()


async def delete_tutor(tutor_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tutors WHERE id = ?", (tutor_id,))
        await db.commit()


async def get_tutor_first_lesson_date(tutor_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT MIN(date) FROM bookings WHERE tutor_id=? AND status='completed'",
            (tutor_id,)
        )
        row = await cursor.fetchone()
        if row and row[0]:
            return datetime.strptime(row[0], "%d.%m.%Y")
        return None


async def calculate_auto_commission(tutor_id: int, year: int, month: int):
    """Возвращает (commission_percent, lessons_count_for_month)."""
    # Считаем количество завершённых занятий в указанном месяце
    start_date = f"{year}-{month:02d}-01"
    end_date = f"{year}-{month:02d}-31"  # SQLite проигнорирует лишние дни
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM bookings WHERE tutor_id=? AND status='completed' "
            "AND date BETWEEN ? AND ?",
            (tutor_id, start_date, end_date)
        )
        lessons = (await cursor.fetchone())[0]

    # Стаж в полных календарных месяцах до начала текущего месяца
    first_lesson = await get_tutor_first_lesson_date(tutor_id)
    if not first_lesson:
        return (25, lessons)

    first_of_current_month = datetime(year, month, 1)
    months_diff = (first_of_current_month.year - first_lesson.year) * 12 + \
                  (first_of_current_month.month - first_lesson.month)

    # Занятия за первые 60 дней работы
    first_60_days_end = first_lesson + timedelta(days=60)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM bookings WHERE tutor_id=? AND status='completed' "
            "AND date BETWEEN ? AND ?",
            (tutor_id, first_lesson.strftime("%d.%m.%Y"),
             first_60_days_end.strftime("%d.%m.%Y"))
        )
        first_60_days_lessons = (await cursor.fetchone())[0]

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
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM bookings WHERE tutor_id=? AND status='completed' "
                "AND strftime('%Y', date)=? AND strftime('%m', date)=?",
                (tutor_id, str(year), f"{month:02d}")
            )
            lessons = (await cursor.fetchone())[0]

    # Суммарный доход (цена предмета) за месяц
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT SUM(s.price) FROM bookings b "
            "JOIN subjects s ON s.tutor_id = b.tutor_id AND s.name = b.subject "
            "WHERE b.tutor_id=? AND b.status='completed' "
            "AND strftime('%Y', b.date)=? AND strftime('%m', b.date)=?",
            (tutor_id, str(year), f"{month:02d}")
        )
        total_income = (await cursor.fetchone())[0] or 0.0

    commission = total_income * percent / 100
    net = total_income - commission

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO monthly_stats (tutor_id, year, month, lessons_count, total_income, "
            "commission_amount, net_income, commission_mode, commission_percent) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tutor_id, year, month) DO UPDATE SET "
            "lessons_count=excluded.lessons_count, total_income=excluded.total_income, "
            "commission_amount=excluded.commission_amount, net_income=excluded.net_income, "
            "commission_mode=excluded.commission_mode, commission_percent=excluded.commission_percent",
            (tutor_id, year, month, lessons, total_income, commission, net, mode, percent)
        )
        await db.commit()


# ------------------------------------------------------------
# SUBJECTS
# ------------------------------------------------------------
async def add_subject(tutor_id: int, name: str, price: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO subjects (tutor_id, name, price) VALUES (?,?,?)",
                         (tutor_id, name, price))
        await db.commit()


async def update_subject(tutor_id: int, old_name: str, new_name: str = None, new_price: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if new_name is not None:
            await db.execute("UPDATE subjects SET name = ? WHERE tutor_id = ? AND name = ?",
                             (new_name, tutor_id, old_name))
        if new_price is not None:
            await db.execute("UPDATE subjects SET price = ? WHERE tutor_id = ? AND name = ?",
                             (new_price, tutor_id, old_name if new_name is None else new_name))
        await db.commit()


async def delete_subject(tutor_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subjects WHERE tutor_id = ? AND name = ?", (tutor_id, name))
        await db.commit()


async def add_subscription(user_id, tutor_id, subject, total_lessons):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO subscriptions (user_id, tutor_id, subject, total_lessons, remaining_lessons) VALUES (?,?,?,?,?)",
            (user_id, tutor_id, subject, total_lessons, total_lessons)
        )
        await db.commit()


async def get_student_subscriptions(user_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM subscriptions WHERE user_id=? AND active=1 AND remaining_lessons>0",
            (user_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]


# ------------------------------------------------------------
# SCHEDULE
# ------------------------------------------------------------
async def get_all_schedules() -> Dict[int, Dict[str, List[str]]]:
    """Возвращает {tutor_id: {day: [slots]}}"""
    schedules = {}
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT tutor_id, day, time_slot FROM schedule_slots")
        async for row in cursor:
            tid, day, slot = row
            schedules.setdefault(tid, {}).setdefault(day, []).append(slot)
    return schedules


async def get_schedule(tutor_id: int) -> Dict[str, List[str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT day, time_slot FROM schedule_slots WHERE tutor_id=?", (tutor_id,))
        schedule = {}
        async for day, slot in cursor:
            schedule.setdefault(day, []).append(slot)
        return schedule


async def add_schedule_slot(tutor_id: int, day: str, time_slot: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO schedule_slots (tutor_id, day, time_slot) VALUES (?,?,?)",
                         (tutor_id, day, time_slot))
        await db.commit()


async def delete_schedule_slot(tutor_id: int, day: str, time_slot: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM schedule_slots WHERE tutor_id=? AND day=? AND time_slot=?",
                         (tutor_id, day, time_slot))
        await db.commit()


async def block_day(tutor_id: int, day: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO blocked_days (tutor_id, day) VALUES (?, ?)",
            (tutor_id, day)
        )
        await db.commit()


async def unblock_day(tutor_id: int, day: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM blocked_days WHERE tutor_id = ? AND day = ?",
            (tutor_id, day)
        )
        await db.commit()


async def is_day_blocked(tutor_id: int, day: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM blocked_days WHERE tutor_id = ? AND day = ?",
            (tutor_id, day)
        )
        row = await cursor.fetchone()
        return row is not None


# ------------------------------------------------------------
# BOOKINGS
# ------------------------------------------------------------
async def get_all_bookings() -> Dict[int, dict]:
    bookings = {}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM bookings")
        async for row in cursor:
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
    async with aiosqlite.connect("bot.db") as db:
        cursor = await db.execute(
            "INSERT INTO bookings (tutor_id, user_id, username, subject, date, time_slot, status, reminded, channel_msg_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)",
            (tutor_id, user_id, username, subject, date, time_slot, channel_msg_id)
        )
        await db.commit()
        return cursor.lastrowid


async def update_booking(booking_id, **kwargs):
    async with aiosqlite.connect("bot.db") as db:
        fields = [f"{key}=?" for key in kwargs]
        values = list(kwargs.values())
        values.append(booking_id)
        await db.execute(
            f"UPDATE bookings SET {', '.join(fields)} WHERE id=?",
            values
        )
        await db.commit()


async def delete_booking(booking_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        await db.commit()


async def get_tutor_financials(tutor_id: int, year: int = None, month: int = None) -> dict:
    if year is not None and month is not None:
        # Пытаемся взять из кеша
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM monthly_stats WHERE tutor_id=? AND year=? AND month=?",
                (tutor_id, year, month)
            )
            row = await cursor.fetchone()
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
        # Повторный запрос
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM monthly_stats WHERE tutor_id=? AND year=? AND month=?",
                (tutor_id, year, month)
            )
            row = await cursor.fetchone()
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
        # За всё время – суммируем по monthly_stats
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT SUM(lessons_count), SUM(total_income), SUM(commission_amount), SUM(net_income) "
                "FROM monthly_stats WHERE tutor_id=?", (tutor_id,)
            )
            row = await cursor.fetchone()
            return {
                "total_lessons": row[0] or 0,
                "total_income": row[1] or 0.0,
                "commission_amount": row[2] or 0.0,
                "net_income": row[3] or 0.0,
                "commission_percent": "—"
            }


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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT user_id, username FROM bookings")
        students = {row[0]: row[1] async for row in cursor}
    result = []
    for uid, name in students.items():
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM bookings WHERE user_id=? AND status='completed'", (uid,))
            completed = (await cursor.fetchone())[0]
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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, username, COUNT(*) as cnt FROM bookings WHERE status='completed' AND strftime('%Y', date)=? AND strftime('%m', date)=? GROUP BY user_id",
            (str(year), f"{month:02d}")
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            uid, name, cnt = row
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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT email FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None

async def set_user_email(user_id: int, email: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, email) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET email=excluded.email",
            (user_id, email)
        )
        await db.commit()




async def add_lesson_to_balance(user_id: int, tutor_id: int, subject: str):
    """После успешной оплаты добавляет ученику 1 занятие в абонемент."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Есть ли уже активная подписка на этого репетитора и предмет?
        cursor = await db.execute(
            "SELECT id, remaining_lessons FROM subscriptions WHERE user_id=? AND tutor_id=? AND subject=? AND active=1",
            (user_id, tutor_id, subject)
        )
        row = await cursor.fetchone()
        if row:
            # Увеличиваем оставшиеся занятия
            await db.execute(
                "UPDATE subscriptions SET remaining_lessons = remaining_lessons + 1 WHERE id=?",
                (row[0],)
            )
        else:
            # Создаём новую подписку на 1 занятие
            await db.execute(
                "INSERT INTO subscriptions (user_id, tutor_id, subject, total_lessons, remaining_lessons) VALUES (?,?,?,1,1)",
                (user_id, tutor_id, subject)
            )
        await db.commit()




# ------------------------------------------------------------
# Вспомогательная функция для поиска tutor_id по telegram_id
# ------------------------------------------------------------
async def get_tutor_by_telegram_id(telegram_id: int) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM tutors WHERE telegram_id = ?", (telegram_id,))
        row = await cursor.fetchone()
        return row[0] if row else None
