"""Admin no-show actions retain the full pre-October lifecycle."""
import os
from datetime import datetime
from html import escape
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://unused")

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import completion_hardening as completion


def booking(**changes):
    return {**dict(id=123, status="paid", tutor_id=7, username="Ученик",
                   subject="Химия", date="20.10.2026", time_slot="10:00-11:00",
                   amount=200000, updated_at=None), **changes}


def legacy():
    return SimpleNamespace(
        now_msk_naive=lambda: datetime(2026, 10, 20, 12),
        html=SimpleNamespace(quote=escape), format_dt=lambda value: "—",
        InlineKeyboardButton=InlineKeyboardButton,
        InlineKeyboardMarkup=InlineKeyboardMarkup, safe_answer=AsyncMock(),
    )


def call(user_id=1):
    return SimpleNamespace(from_user=SimpleNamespace(id=user_id),
                           message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()))


class CompletionActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_button_visible_only_for_paid_ended_lesson(self):
        for status, slot, visible in (
            ("paid", "10:00-11:00", True),
            ("paid", "12:00-13:00", False),
            ("paid", "invalid", False),
            ("pending", "10:00-11:00", False),
            ("confirmed", "10:00-11:00", False),
            ("cancelled", "10:00-11:00", False),
            ("completed", "10:00-11:00", False),
        ):
            with self.subTest(status=status, slot=slot):
                row = booking()
                row.update(status=status, time_slot=slot)
                event = call()
                with patch.object(completion._db, "get_booking", AsyncMock(return_value=row)), \
                     patch.object(completion._db, "get_all_tutors", AsyncMock(return_value={})):
                    await completion._render_admin_booking_with_completion(legacy(), event, 123)
                markup = event.message.edit_text.await_args.kwargs["reply_markup"]
                callbacks = [b.callback_data for line in markup.inline_keyboard for b in line]
                self.assertEqual("admin_booking_late_cancel_123" in callbacks, visible)
                self.assertEqual("admin_booking_complete_123" in callbacks,
                                 visible or status == "completed")

    async def test_no_show_and_held_finalize_with_distinct_outcomes(self):
        for outcome, prefix in (("late_student_cancel", "неявка засчитана"),
                                ("held", "Проведение подтверждено")):
            with self.subTest(outcome=outcome):
                event = call()
                app = SimpleNamespace(_show_admin_booking=AsyncMock())
                with patch.object(completion._db, "get_booking", AsyncMock(return_value=booking())), \
                     patch.object(completion, "_mark_completed", AsyncMock()) as mark, \
                     patch.object(completion, "_finalize_completed_booking", AsyncMock(return_value={"ok": True})) as finalize:
                    await completion._finalize_admin_outcome(app, legacy(), event, 123, outcome)
                mark.assert_awaited_once_with(123, 1, outcome=outcome)
                finalize.assert_awaited_once_with(123)
                self.assertIn(prefix, event.message.answer.await_args.args[0])
                app._show_admin_booking.assert_awaited_once_with(event, 123)

    async def test_old_callback_cannot_finalize_early_or_invalid_status(self):
        for status, slot in (("paid", "12:00-13:00"), ("paid", "invalid"),
                             ("pending", "10:00-11:00"), ("confirmed", "10:00-11:00"),
                             ("cancelled", "10:00-11:00")):
            with self.subTest(status=status, slot=slot):
                row = booking()
                row.update(status=status, time_slot=slot)
                runtime = legacy()
                with patch.object(completion._db, "get_booking", AsyncMock(return_value=row)), \
                     patch.object(completion, "_mark_completed", AsyncMock()) as mark, \
                     patch.object(completion, "_finalize_completed_booking", AsyncMock()) as finalize:
                    await completion._finalize_admin_outcome(None, runtime, call(), 123, "late_student_cancel")
                mark.assert_not_awaited()
                finalize.assert_not_awaited()
                runtime.safe_answer.assert_awaited_once()

    async def test_missing_booking_does_not_finalize(self):
        event = call()
        with patch.object(completion._db, "get_booking", AsyncMock(return_value=None)), \
             patch.object(completion, "_finalize_completed_booking", AsyncMock()) as finalize:
            await completion._finalize_admin_outcome(None, legacy(), event, 123, "late_student_cancel")
        finalize.assert_not_awaited()
        self.assertIn("не найдено", event.message.edit_text.await_args.args[0])

    async def test_receipt_retry_does_not_mark_completed_again(self):
        row = booking()
        row["status"] = "completed"
        event = call()
        with patch.object(completion._db, "get_booking", AsyncMock(return_value=row)), \
             patch.object(completion, "_mark_completed", AsyncMock()) as mark, \
             patch.object(completion, "_finalize_completed_booking", AsyncMock(return_value={"ok": False, "reason": "closing_receipt_status_unknown"})):
            await completion._finalize_admin_outcome(SimpleNamespace(_show_admin_booking=AsyncMock()),
                                                     legacy(), event, 123, "late_student_cancel")
        mark.assert_not_awaited()
        self.assertIn("требует проверки", event.message.answer.await_args.args[0])

    async def test_subscription_and_single_payment_use_correct_receipt_paths(self):
        for usage in (None, {"status": "reserved"}):
            with self.subTest(subscription=bool(usage)):
                with patch.object(completion.subs, "get_booking_usage", AsyncMock(return_value=usage)), \
                     patch.object(completion.subs, "consume_booking_unit", AsyncMock(return_value=True)) as consume, \
                     patch.object(completion.subs, "send_subscription_closing_receipt", AsyncMock(return_value={"ok": True})) as package_receipt, \
                     patch.object(completion.fiscal_receipts, "send_booking_closing_receipt", AsyncMock(return_value={"ok": True})) as single_receipt, \
                     patch.object(completion._db, "get_booking", AsyncMock(return_value=booking())), \
                     patch.object(completion._db, "_recalculate_booking_stats", AsyncMock()) as stats, \
                     patch.object(completion._db, "_sync_booking_record_safely", AsyncMock()) as sync:
                    self.assertTrue((await completion._finalize_completed_booking(123))["ok"])
                if usage:
                    consume.assert_awaited_once_with(123)
                    package_receipt.assert_awaited_once_with(123)
                    single_receipt.assert_not_awaited()
                else:
                    consume.assert_not_awaited()
                    package_receipt.assert_not_awaited()
                    single_receipt.assert_awaited_once_with(123)
                stats.assert_awaited_once()
                sync.assert_awaited_once_with(123)

    async def test_failed_consumption_never_sends_receipt(self):
        with patch.object(completion.subs, "get_booking_usage", AsyncMock(return_value={"status": "released"})), \
             patch.object(completion.subs, "consume_booking_unit", AsyncMock(return_value=False)), \
             patch.object(completion.subs, "send_subscription_closing_receipt", AsyncMock()) as receipt:
            result = await completion._finalize_completed_booking(123)
        self.assertEqual(result["reason"], "subscription_unit_not_consumed")
        receipt.assert_not_awaited()

    async def test_registered_no_show_callback_checks_admin_and_keeps_old_payload(self):
        handlers = []
        class Filter:
            def regexp(self, pattern):
                return pattern
        def register(pattern):
            def decorate(fn):
                handlers.append((pattern, fn))
                return fn
            return decorate
        runtime = legacy()
        runtime.F = SimpleNamespace(data=Filter())
        runtime.dp = SimpleNamespace(callback_query=register)
        runtime.CallbackQuery = object
        runtime.ADMING_ID = 1
        app = SimpleNamespace(legacy=runtime)
        completion.install_telegram_completion_hardening(app)
        handler = next(fn for pattern, fn in handlers if "late_cancel" in pattern)
        with patch.object(completion, "_finalize_admin_outcome", AsyncMock()) as finalize:
            event = call(user_id=2)
            event.data = "admin_booking_late_cancel_123"
            await handler(event)
            finalize.assert_not_awaited()
            event = call(user_id=1)
            event.data = "admin_booking_late_cancel_123"
            await handler(event)
            finalize.assert_awaited_once_with(app, runtime, event, 123, "late_student_cancel")

    async def test_unsupported_outcome_cannot_write_to_database(self):
        with patch.object(completion._db, "_ensure_pool", AsyncMock()) as ensure:
            with self.assertRaises(ValueError):
                await completion._mark_completed(123, 1, outcome="unsupported")
        ensure.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
