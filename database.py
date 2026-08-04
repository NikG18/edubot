import aiosqlite
from typing import Dict, List, Optional

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
            commission_percent INTEGER DEFAULT 15
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

        """)
        await db.commit()
        
#async def migrate_database():
#    async with aiosqlite.connect("bot.db") as db:
#        cursor = await db.execute("PRAGMA table_info(bookings)")
#        columns = [row[1] for row in await cursor.fetchall()]
#        if "channel_msg_id" not in columns:
 #           await db.execute("ALTER TABLE bookings ADD COLUMN channel_msg_id INTEGER DEFAULT NULL")
 ##           await db.commit()
#        print("Миграция базы данных завершена.")
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
                "subjects": {}
            }
        cursor2 = await db.execute("SELECT * FROM subjects")
        async for row in cursor2:
            tid = row["tutor_id"]
            if tid in tutors:
                tutors[tid]["subjects"][row["name"]] = row["price"]
    return tutors

async def add_tutor(name, photo, telegram_id, description, commission_percent=15):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO tutors (name, photo, telegram_id, description, commission_percent) VALUES (?,?,?,?,?)",
            (name, photo, telegram_id, description, commission_percent)
        )
        await db.commit()
        return cur.lastrowid
        
async def update_tutor(tutor_id: int, **kwargs):
    """kwargs: name, photo, telegram_id, description"""
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
                "reminded": bool(row["reminded"])
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
    tutors = await get_all_tutors()
    tutor = tutors.get(tutor_id)
    commission_percent = tutor.get("commission_percent", 15) if tutor else 15

    query = """
        SELECT b.subject, b.date, s.price 
        FROM bookings b 
        JOIN subjects s ON s.tutor_id = b.tutor_id AND s.name = b.subject 
        WHERE b.tutor_id=? AND b.status='completed'
    """
    params = [tutor_id]
    if year is not None and month is not None:
        query += " AND strftime('%Y', b.date) = ? AND strftime('%m', b.date) = ?"
        params += [str(year), f"{month:02d}"]
    elif year is not None:
        query += " AND strftime('%Y', b.date) = ?"
        params.append(str(year))
    elif month is not None:
        query += " AND strftime('%m', b.date) = ?"
        params.append(f"{month:02d}")

    total_lessons = 0
    total_income = 0.0
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query, params)
        async for row in cursor:
            total_lessons += 1
            total_income += row[2]

    commission_amount = total_income * commission_percent / 100
    net_income = total_income - commission_amount
    return {
        "total_lessons": total_lessons,
        "total_income": total_income,
        "commission_amount": commission_amount,
        "net_income": net_income,
        "commission_percent": commission_percent
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
# Вспомогательная функция для поиска tutor_id по telegram_id
# ------------------------------------------------------------
async def get_tutor_by_telegram_id(telegram_id: int) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM tutors WHERE telegram_id = ?", (telegram_id,))
        row = await cursor.fetchone()
        return row[0] if row else None
