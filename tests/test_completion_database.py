"""Real PostgreSQL checks for no-show audit and exactly-once package consumption."""
import asyncio
import json
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://unused")

import asyncpg
import completion_hardening as completion


@unittest.skipUnless(os.getenv("TAX_TEST_DATABASE_URL"), "requires disposable PostgreSQL test database")
class CompletionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "completion_test_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(os.environ["TAX_TEST_DATABASE_URL"])
        await self.admin.execute(f"CREATE SCHEMA {self.schema}")
        self.pool = await asyncpg.create_pool(
            os.environ["TAX_TEST_DATABASE_URL"], min_size=1, max_size=6,
            server_settings={"search_path": self.schema})
        self.patches = [patch.object(completion._db._legacy, "pool", self.pool),
                        patch.object(completion._db, "_ensure_pool", AsyncMock()),
                        patch.object(completion.subs, "ensure_subscription_schema", AsyncMock())]
        for item in self.patches:
            item.start()
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE bookings(id INT PRIMARY KEY, status TEXT,
                    stats_counted BOOLEAN DEFAULT FALSE, updated_at TIMESTAMPTZ);
                CREATE TABLE booking_events(id BIGSERIAL PRIMARY KEY, booking_id INT,
                    event_type TEXT, old_status TEXT, new_status TEXT,
                    actor_type TEXT, actor_id BIGINT, details JSONB);
                CREATE TABLE subscription_usages(id INT PRIMARY KEY, booking_id INT UNIQUE,
                    status TEXT, consumed_at TIMESTAMPTZ);
                INSERT INTO bookings(id,status) VALUES (123,'paid');
                INSERT INTO subscription_usages VALUES (1,123,'reserved',NULL);
            """)

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.pool.close()
        await self.admin.execute(f"DROP SCHEMA {self.schema} CASCADE")
        await self.admin.close()

    async def test_parallel_no_show_records_one_outcome_and_consumes_one_reservation(self):
        results = await asyncio.gather(*(
            completion._mark_completed(123, 1, outcome="late_student_cancel")
            for _ in range(4)))
        self.assertEqual(sum(changed for changed, _ in results), 1)
        consumed = await asyncio.gather(*(
            completion.subs.consume_booking_unit(123) for _ in range(4)))
        self.assertTrue(all(consumed))
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=123")
            events = await conn.fetch("SELECT * FROM booking_events WHERE booking_id=123")
            usage = await conn.fetchrow("SELECT * FROM subscription_usages WHERE booking_id=123")
        self.assertEqual(row["status"], "completed")
        self.assertTrue(row["stats_counted"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "late_student_cancel_counted")
        self.assertEqual((events[0]["actor_type"], events[0]["actor_id"]), ("admin", 1))
        self.assertEqual(json.loads(events[0]["details"])["outcome"], "late_student_cancel")
        self.assertEqual(usage["status"], "consumed")
        self.assertIsNotNone(usage["consumed_at"])

    async def test_retry_as_held_cannot_overwrite_recorded_no_show(self):
        await completion._mark_completed(123, 1, outcome="late_student_cancel")
        changed, row = await completion._mark_completed(123, 2, outcome="held")
        self.assertFalse(changed)
        self.assertEqual(row["status"], "completed")
        async with self.pool.acquire() as conn:
            events = await conn.fetch("SELECT * FROM booking_events")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "late_student_cancel_counted")

    async def test_unpaid_or_cancelled_booking_cannot_be_counted(self):
        for status in ("pending", "confirmed", "cancelled"):
            with self.subTest(status=status):
                async with self.pool.acquire() as conn:
                    await conn.execute("UPDATE bookings SET status=$1 WHERE id=123", status)
                changed, row = await completion._mark_completed(123, 1, outcome="late_student_cancel")
                self.assertFalse(changed)
                self.assertEqual(row["status"], status)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT COUNT(*) FROM booking_events"), 0)


if __name__ == "__main__":
    unittest.main()
