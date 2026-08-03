import json
import asyncio
import aiosqlite

DB_PATH = "bot.db"

async def migrate():
    # Загружаем старые данные
    with open("tutors.json", encoding="utf-8") as f:
        tutors = json.load(f)
    with open("schedules.json", encoding="utf-8") as f:
        schedules = json.load(f)
    with open("bookings.json", encoding="utf-8") as f:
        bookings = json.load(f)

    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем внешние ключи
        await db.execute("PRAGMA foreign_keys = ON")
        
        # Миграция репетиторов и предметов
        for tid_str, t in tutors.items():
            tid = int(tid_str)
            await db.execute(
                "INSERT INTO tutors (id, name, photo, telegram_id, description) VALUES (?,?,?,?,?)",
                (tid, t["name"], t["photo"], t.get("telegram_id"), t["description"])
            )
            for subj, price in t["subjects"].items():
                await db.execute(
                    "INSERT INTO subjects (tutor_id, name, price) VALUES (?,?,?)",
                    (tid, subj, price)
                )

        # Миграция расписания
        for tid_str, days in schedules.items():
            tid = int(tid_str)
            for day, slots in days.items():
                for slot in slots:
                    await db.execute(
                        "INSERT INTO schedule_slots (tutor_id, day, time_slot) VALUES (?,?,?)",
                        (tid, day, slot)
                    )

        # Миграция бронирований
        for bid_str, b in bookings.items():
            bid = int(bid_str)
            await db.execute(
                "INSERT INTO bookings (id, tutor_id, user_id, username, subject, date, time_slot, status, reminded) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (bid, b["tutor_id"], b["user_id"], b["username"], b["subject"],
                 b["date"], b["time_slot"], b.get("status", "pending"), 0)
            )

        await db.commit()
        print("Миграция успешно завершена!")

asyncio.run(migrate())
