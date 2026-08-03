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
            description TEXT DEFAULT ''
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
        """)
        await db.commit()

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
                "subjects": {}
            }
        cursor2 = await db.execute("SELECT * FROM subjects")
        async for row in cursor2:
            tid = row["tutor_id"]
            if tid in tutors:
                tutors[tid]["subjects"][row["name"]] = row["price"]
    return tutors

async def add_tutor(name: str, photo: str, telegram_id: Optional[int], description: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO tutors (name, photo, telegram_id, description) VALUES (?,?,?,?)",
            (name, photo, telegram_id, description)
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

async def add_booking(tutor_id, user_id, username, subject, date, time_slot) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO bookings (tutor_id, user_id, username, subject, date, time_slot) VALUES (?,?,?,?,?,?)",
            (tutor_id, user_id, username, subject, date, time_slot))
        await db.commit()
        return cur.lastrowid

async def update_booking(booking_id: int, **kwargs):
    """kwargs: status, reminded (int 0/1)"""
    fields = {k: v for k, v in kwargs.items() if v is not None}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [booking_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE bookings SET {set_clause} WHERE id = ?", values)
        await db.commit()

async def delete_booking(booking_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        await db.commit()

# ------------------------------------------------------------
# Вспомогательная функция для поиска tutor_id по telegram_id
# ------------------------------------------------------------
async def get_tutor_by_telegram_id(telegram_id: int) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM tutors WHERE telegram_id = ?", (telegram_id,))
        row = await cursor.fetchone()
        return row[0] if row else None
