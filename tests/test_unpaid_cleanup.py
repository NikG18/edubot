"""Unpaid regular bookings survive cleanup and remain eligible for payment."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from booking_visibility_rules import can_offer_separate_payment


class UnpaidCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_never_cancels_or_notifies_unpaid_lessons(self):
        now = datetime(2026, 9, 20, 12)
        bookings = {
            index: {"status": "confirmed", "booking_type": "regular", "start": now + delta}
            for index, delta in enumerate((timedelta(days=2), timedelta(hours=1), timedelta(days=-1)))
        }
        cleanup = AsyncMock(return_value=[99])
        cancel = AsyncMock(return_value=(True, {}))
        user_notice, tutor_notice = AsyncMock(), AsyncMock()
        namespace = {
            "_original_cleanup_old_bookings": cleanup,
            "_db": SimpleNamespace(get_all_bookings=AsyncMock(return_value=bookings), cancel_booking_record=cancel),
            "legacy": SimpleNamespace(now_msk_naive=lambda: now,
                                      parse_booking_time=lambda b: b["start"],
                                      send_to_user=user_notice, send_to_tutor=tutor_notice),
            "_is_trial": lambda b: False,
            "timedelta": timedelta,
        }
        path = Path(__file__).resolve().parents[1] / "Bot_test.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "_cleanup_with_unpaid_autocancel")
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), str(path), "exec"), namespace)
        self.assertEqual(await namespace[fn.name](), [99])
        cleanup.assert_awaited_once()
        cancel.assert_not_awaited()
        user_notice.assert_not_awaited()
        tutor_notice.assert_not_awaited()

    def test_elapsed_date_does_not_block_payment_but_closed_status_does(self):
        booking = dict(status="confirmed", booking_type="regular", subject="Химия",
                       date="01.01.2000", time_slot="12:00-13:00")
        self.assertTrue(can_offer_separate_payment(booking))
        for status in ("paid", "completed", "cancelled"):
            self.assertFalse(can_offer_separate_payment(dict(booking, status=status)))
