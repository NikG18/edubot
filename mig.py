import asyncio
import aiosqlite
import asyncpg
import os
SQLITE_DB = "bot.db"   # ваш текущий файл
PG_DSN = ""

async def migrate():
    sqlite_conn = await aiosqlite.connect(SQLITE_DB)
    sqlite_conn.row_factory = aiosqlite.Row
    pg_conn = await asyncpg.connect(PG_DSN)

    # 1. Создание таблиц в Neon (если ещё не созданы)
    await pg_conn.execute("""
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

    # 2. Перенос данных (каждая таблица)
    # tutors
    cursor = await sqlite_conn.execute("SELECT * FROM tutors ORDER BY id")
    rows = await cursor.fetchall()
    for r in rows:
        await pg_conn.execute(
            """INSERT INTO tutors (id, name, photo, telegram_id, description, commission_percent, commission_mode, inn)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (id) DO NOTHING""",
            r["id"], r["name"], r["photo"], r["telegram_id"], r["description"],
            r["commission_percent"], r["commission_mode"], r["inn"]
        )
    if rows:
        await pg_conn.execute("SELECT setval('tutors_id_seq', (SELECT MAX(id) FROM tutors))")

    # subjects
    cursor = await sqlite_conn.execute("SELECT * FROM subjects ORDER BY id")
    rows = await cursor.fetchall()
    for r in rows:
        await pg_conn.execute(
            "INSERT INTO subjects (id, tutor_id, name, price) VALUES ($1,$2,$3,$4) ON CONFLICT (id) DO NOTHING",
            r["id"], r["tutor_id"], r["name"], r["price"]
        )
    if rows:
        await pg_conn.execute("SELECT setval('subjects_id_seq', (SELECT MAX(id) FROM subjects))")

    # schedule_slots
    cursor = await sqlite_conn.execute("SELECT * FROM schedule_slots ORDER BY id")
    rows = await cursor.fetchall()
    for r in rows:
        await pg_conn.execute(
            "INSERT INTO schedule_slots (id, tutor_id, day, time_slot) VALUES ($1,$2,$3,$4) ON CONFLICT (id) DO NOTHING",
            r["id"], r["tutor_id"], r["day"], r["time_slot"]
        )
    if rows:
        await pg_conn.execute("SELECT setval('schedule_slots_id_seq', (SELECT MAX(id) FROM schedule_slots))")

    # bookings
    cursor = await sqlite_conn.execute("SELECT * FROM bookings ORDER BY id")
    rows = await cursor.fetchall()
    for r in rows:
        await pg_conn.execute(
            """INSERT INTO bookings (id, tutor_id, user_id, username, subject, date, time_slot,
               status, reminded, channel_msg_id, amount, commission_percent, tinkoff_payment_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT (id) DO NOTHING""",
            r["id"], r["tutor_id"], r["user_id"], r["username"], r["subject"],
            r["date"], r["time_slot"], r["status"], r["reminded"], r["channel_msg_id"],
            r["amount"], r["commission_percent"], r["tinkoff_payment_id"]
        )
    if rows:
        await pg_conn.execute("SELECT setval('bookings_id_seq', (SELECT MAX(id) FROM bookings))")

    # subscriptions
    cursor = await sqlite_conn.execute("SELECT * FROM subscriptions ORDER BY id")
    rows = await cursor.fetchall()
    for r in rows:
        await pg_conn.execute(
            "INSERT INTO subscriptions (id, user_id, tutor_id, subject, total_lessons, remaining_lessons, active) VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (id) DO NOTHING",
            r["id"], r["user_id"], r["tutor_id"], r["subject"], r["total_lessons"], r["remaining_lessons"], r["active"]
        )
    if rows:
        await pg_conn.execute("SELECT setval('subscriptions_id_seq', (SELECT MAX(id) FROM subscriptions))")

    # blocked_days
    cursor = await sqlite_conn.execute("SELECT * FROM blocked_days")
    rows = await cursor.fetchall()
    for r in rows:
        await pg_conn.execute(
            "INSERT INTO blocked_days (tutor_id, day) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            r["tutor_id"], r["day"]
        )

    # monthly_stats
    cursor = await sqlite_conn.execute("SELECT * FROM monthly_stats ORDER BY id")
    rows = await cursor.fetchall()
    for r in rows:
        await pg_conn.execute(
            """INSERT INTO monthly_stats (id, tutor_id, year, month, lessons_count, total_income,
               commission_amount, net_income, commission_mode, commission_percent)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT (id) DO NOTHING""",
            r["id"], r["tutor_id"], r["year"], r["month"], r["lessons_count"],
            r["total_income"], r["commission_amount"], r["net_income"],
            r["commission_mode"], r["commission_percent"]
        )
    if rows:
        await pg_conn.execute("SELECT setval('monthly_stats_id_seq', (SELECT MAX(id) FROM monthly_stats))")

    # users
    cursor = await sqlite_conn.execute("SELECT * FROM users")
    rows = await cursor.fetchall()
    for r in rows:
        await pg_conn.execute(
            "INSERT INTO users (user_id, email) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            r["user_id"], r["email"]
        )

    # pending_email_requests
    cursor = await sqlite_conn.execute("SELECT * FROM pending_email_requests")
    rows = await cursor.fetchall()
    for r in rows:
        await pg_conn.execute(
            "INSERT INTO pending_email_requests (user_id, booking_id) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            r["user_id"], r["booking_id"]
        )

    await sqlite_conn.close()
    await pg_conn.close()
    print("Миграция в Neon завершена успешно!")

if __name__ == "__main__":
    asyncio.run(migrate())
