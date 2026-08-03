import json, asyncio, aiosqlite

async def migrate_json_to_db():
    with open("tutors.json") as f: tutors = json.load(f)
    with open("schedules.json") as f: schedules = json.load(f)
    with open("bookings.json") as f: bookings = json.load(f)
    
    async with aiosqlite.connect(DB_PATH) as db:
        for tid, t in tutors.items():
            await db.execute("INSERT INTO tutors (id, name, photo, telegram_id, description) VALUES (?,?,?,?,?)",
                             (tid, t["name"], t["photo"], t["telegram_id"], t["description"]))
            for subj, price in t["subjects"].items():
                await db.execute("INSERT INTO subjects (tutor_id, name, price) VALUES (?,?,?)",
                                 (tid, subj, price))
        for tid, days in schedules.items():
            for day, slots in days.items():
                for slot in slots:
                    await db.execute("INSERT INTO schedule_slots (tutor_id, day, time_slot) VALUES (?,?,?)",
                                     (tid, day, slot))
        for bid, b in bookings.items():
            await db.execute("INSERT INTO bookings (id, tutor_id, user_id, username, subject, date, time_slot, status) VALUES (?,?,?,?,?,?,?,?)",
                             (bid, b["tutor_id"], b["user_id"], b["username"], b["subject"], b["date"], b["time_slot"], b.get("status","pending")))
        await db.commit()
