import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://unused")

import payments
from bot_common import booking_needs_payment_poll, process_booking_payment_status
from database import _stats_counted_after_status_change


class StatsTransitionTests(unittest.TestCase):
    def test_completed_admin_cancel_stays_counted_until_refund(self):
        self.assertTrue(
            _stats_counted_after_status_change("completed", "cancelled", True, True)
        )

    def test_unfinished_cancel_is_not_counted(self):
        self.assertFalse(
            _stats_counted_after_status_change("paid", "cancelled", False, True)
        )

    def test_completion_enters_statistics(self):
        self.assertTrue(
            _stats_counted_after_status_change("paid", "completed", False, False)
        )


class PollSelectionTests(unittest.TestCase):
    def test_confirmed_payment_is_polled(self):
        self.assertTrue(booking_needs_payment_poll({
            "status": "confirmed", "tinkoff_payment_id": "1"
        }))

    def test_cancelled_pending_refund_is_polled(self):
        self.assertTrue(booking_needs_payment_poll({
            "status": "cancelled",
            "refund_status": "pending",
            "tinkoff_payment_id": "1",
        }))

    def test_completed_refund_is_not_polled(self):
        self.assertFalse(booking_needs_payment_poll({
            "status": "cancelled",
            "refund_status": "refunded",
            "tinkoff_payment_id": "1",
        }))


class PaymentStatusTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _fake_database(**overrides):
        fake_database = types.ModuleType("database")
        fake_database.add_booking_event = AsyncMock()
        fake_database.get_booking = AsyncMock()
        fake_database.mark_booking_paid_once = AsyncMock()
        fake_database.mark_booking_payment_failed = AsyncMock()
        fake_database.update_booking = AsyncMock()
        fake_database.confirm_booking_refunded = AsyncMock()
        for name, value in overrides.items():
            setattr(fake_database, name, value)
        return fake_database

    async def test_refunded_webhook_confirms_database_refund(self):
        fake_database = self._fake_database(
            confirm_booking_refunded=AsyncMock(
                return_value=(True, {"refund_status": "refunded"})
            )
        )

        with patch.dict(sys.modules, {"database": fake_database}):
            changed, booking = await process_booking_payment_status(17, "refunded")

        self.assertTrue(changed)
        self.assertEqual(booking["refund_status"], "refunded")
        fake_database.confirm_booking_refunded.assert_awaited_once_with(
            17, actor_type="payment"
        )

    async def test_partial_refund_status_is_ignored(self):
        fake_database = self._fake_database()

        with patch.dict(sys.modules, {"database": fake_database}):
            changed, booking = await process_booking_payment_status(17, "PARTIAL_REFUNDED")

        self.assertFalse(changed)
        self.assertIsNone(booking)
        fake_database.confirm_booking_refunded.assert_not_awaited()

    async def test_late_confirmed_does_not_undo_pending_refund(self):
        booking = {
            "status": "cancelled",
            "refund_status": "pending",
            "tinkoff_payment_id": "123",
            "amount": 200000,
        }
        fake_database = self._fake_database(
            get_booking=AsyncMock(return_value=booking)
        )

        with patch.dict(sys.modules, {"database": fake_database}):
            changed, current = await process_booking_payment_status(17, "CONFIRMED")

        self.assertFalse(changed)
        self.assertIs(current, booking)
        fake_database.update_booking.assert_not_awaited()


class BankRefundTests(unittest.IsolatedAsyncioTestCase):
    async def test_already_refunded_does_not_send_cancel_again(self):
        with (
            patch.object(
                payments,
                "check_payment",
                AsyncMock(return_value={"Success": True, "Status": "REFUNDED"}),
            ),
            patch.object(payments, "cancel_full_payment", AsyncMock()) as cancel,
        ):
            result = await payments.refund_full_payment("123")

        self.assertTrue(result["success"])
        self.assertTrue(result["already_refunded"])
        cancel.assert_not_awaited()

    async def test_accepted_asynchronous_refund_is_pending(self):
        with (
            patch.object(
                payments,
                "check_payment",
                AsyncMock(side_effect=[
                    {"Success": True, "Status": "CONFIRMED"},
                    {"Success": True, "Status": "REFUNDING"},
                ]),
            ),
            patch.object(
                payments,
                "cancel_full_payment",
                AsyncMock(return_value={"Success": True, "Status": "REFUNDING"}),
            ),
        ):
            result = await payments.refund_full_payment("123")

        self.assertFalse(result["success"])
        self.assertTrue(result["pending"])
        self.assertEqual(result["status"], "REFUNDING")


if __name__ == "__main__":
    unittest.main()
