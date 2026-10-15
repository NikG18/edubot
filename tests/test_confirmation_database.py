"""Exercise confirmation and durable email requests in disposable PostgreSQL."""
import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

import asyncpg
import database as db
import student_account_hardening as accounts
import tutor_confirmation_hardening as confirmation


@unittest.skipUnless(os.getenv("TAX_TEST_DATABASE_URL"), "requires disposable PostgreSQL test database")
class ConfirmationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "confirmation_test_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(os.environ["TAX_TEST_DATABASE_URL"])
        await self.admin.execute(f"CREATE SCHEMA {self.schema}")
        self.pool = await asyncpg.create_pool(
            os.environ["TAX_TEST_DATABASE_URL"], min_size=1, max_size=5,
            server_settings={"search_path": self.schema})
        self.patches = [
            patch.object(db._legacy, "pool", None),
            patch.object(db._legacy.asyncpg, "create_pool", AsyncMock(return_value=self.pool)),
            patch.object(db, "_sync_booking_record_safely", AsyncMock()),
            patch.object(accounts, "_schema_ready", False),
            patch.object(confirmation, "resolve_booking_subscription", AsyncMock(return_value=None)),
        ]
        for p in self.patches:
            p.start()
        await db.init_db()
        self.legacy = SimpleNamespace(send_to_user=AsyncMock(return_value=True),
                                      create_and_send_payment=AsyncMock(), logging=Mock())
        async with self.pool.acquire() as conn:
            self.tutor_id = await conn.fetchval(
                "INSERT INTO tutors(name) VALUES('Tutor') RETURNING id")

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await self.pool.close()
        await self.admin.execute(f"DROP SCHEMA {self.schema} CASCADE")
        await self.admin.close()

    async def booking(self, user_id=123, time_slot="12:00-13:00"):
        bid = await db.add_booking(self.tutor_id, user_id, "Student", "Химия", "20.10.2026", time_slot)
        return await db.get_booking(bid)

    async def confirm(self, booking):
        return await confirmation._confirm_regular(
            self.legacy, booking, 456, telegram_bot=object(), source=object())

    async def test_confirmation_and_waiting_email_survive_database_reinitialization(self):
        booking = await self.booking()
        result = await self.confirm(booking)
        self.assertEqual(result["kind"], "email_required")
        await db.init_db()  # Re-running the migration must preserve current state.
        current = await db.get_booking(booking["id"])
        self.assertEqual(current["status"], "confirmed")
        self.assertEqual(current["payment_delivery_state"], "awaiting_email")
        self.assertFalse(current["tinkoff_payment_id"])
        self.assertEqual(await accounts.get_pending_email_request("telegram", 123), booking["id"])
        events = await db.get_booking_events(booking["id"])
        confirmed = [event for event in events if event["event_type"] == "confirmed"]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["actor_type"], "tutor")
        self.assertEqual(confirmed[0]["actor_id"], 456)
        self.legacy.create_and_send_payment.assert_not_awaited()

    async def test_new_clients_have_independent_durable_requests(self):
        first = await self.booking()
        second = await self.booking(124, "13:00-14:00")
        await self.confirm(first)
        await self.confirm(second)
        self.assertEqual(await accounts.get_pending_email_request("telegram", 123), first["id"])
        self.assertEqual(await accounts.get_pending_email_request("telegram", 124), second["id"])
        self.assertIsNone(await accounts.get_pending_email_request("vk", 123))

    async def test_concurrent_confirmations_only_process_the_winner(self):
        booking = await self.booking()
        results = await asyncio.gather(self.confirm(booking), self.confirm(booking))
        self.assertCountEqual([r["kind"] for r in results], ["email_required", "already_processed"])
        self.legacy.send_to_user.assert_awaited_once()

    async def test_late_delivery_updates_do_not_change_closed_lessons_or_trials(self):
        booking = await self.booking()
        await self.confirm(booking)
        bid = booking["id"]
        async with self.pool.acquire() as conn:
            for status in ("paid", "completed", "cancelled"):
                await conn.execute("UPDATE bookings SET status=$1 WHERE id=$2", status, bid)
                self.assertFalse(await db.set_booking_payment_delivery_state(bid, "sent"))
                current = await db.get_booking(bid)
                self.assertEqual(current["status"], status)
            await conn.execute("UPDATE bookings SET status='confirmed', booking_type='trial' WHERE id=$1", bid)
            self.assertFalse(await db.set_booking_payment_delivery_state(bid, "awaiting_email"))
            await conn.execute("UPDATE bookings SET booking_type='regular', tinkoff_payment_id='bank-1' WHERE id=$1", bid)
            self.assertFalse(await db.set_booking_payment_delivery_state(bid, "awaiting_email"))
        self.assertTrue(await db.set_booking_payment_delivery_state(bid, "sent"))
        self.assertFalse(await db.set_booking_payment_delivery_state(
            bid, "not_sent", expected_states={None, "awaiting_email"}))
        self.assertEqual((await db.get_booking(bid))["payment_delivery_state"], "sent")


if __name__ == "__main__":
    unittest.main()
