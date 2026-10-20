"""Tutor acceptance survives missing email, bank errors and repeated clicks."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from booking_visibility_rules import can_offer_separate_payment, payment_delivery_note
import email_payment_safety_hardening as safety
import telegram_email_hardening as email_flow
import tutor_confirmation_hardening as confirmation


class TutorConfirmationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.booking = dict(id=7, tutor_id=2, user_id=123, username="Ученица",
                            subject="Химия", date="20.10.2026", time_slot="12:00-13:00",
                            status="pending", user_platform="telegram", booking_type="regular",
                            tinkoff_payment_id=None, payment_delivery_state=None)
        self.pending = {}
        self.emails = {}
        self.legacy = SimpleNamespace(create_and_send_payment=AsyncMock(), send_to_user=AsyncMock(),
                                      logging=Mock())
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

        async def change_status(booking_id, status, **kwargs):
            self.assertEqual(booking_id, 7)
            if self.booking["status"] not in kwargs["expected_statuses"]:
                return False, dict(self.booking)
            self.booking["status"] = status
            return True, dict(self.booking)

        async def delivery_state(booking_id, state, **kwargs):
            if self.booking["status"] == "confirmed":
                expected = kwargs.get("expected_states")
                if expected is None or self.booking["payment_delivery_state"] in expected:
                    self.booking["payment_delivery_state"] = state
                    return True
            return False

        async def set_pending(platform, user_id, booking_id):
            self.assertEqual(self.booking["status"], "confirmed")
            self.pending[platform, user_id] = booking_id
            await delivery_state(booking_id, "awaiting_email")

        async def resolve(*args, **kwargs):
            self.assertEqual(self.booking["status"], "confirmed")
            self.assertEqual(kwargs["allowed_statuses"], {"confirmed"})
            return None

        replacements = {
            "change_booking_status": AsyncMock(side_effect=change_status),
            "set_booking_payment_delivery_state": AsyncMock(side_effect=delivery_state),
            "get_student_email": AsyncMock(side_effect=lambda p, u: self.emails.get((p, u))),
            "get_booking": AsyncMock(side_effect=lambda bid: dict(self.booking)),
            "_sync_booking_record_safely": AsyncMock(),
        }
        self.db = SimpleNamespace(**replacements)
        self.stack.enter_context(patch.multiple(confirmation._db, **replacements))
        self.resolver = self.stack.enter_context(patch.object(
            confirmation, "resolve_booking_subscription", AsyncMock(side_effect=resolve)))
        self.stack.enter_context(patch.object(confirmation.accounts, "set_pending_email_request",
                                             AsyncMock(side_effect=set_pending)))

    async def confirm(self, booking=None):
        return await confirmation._confirm_regular(
            self.legacy, booking or dict(self.booking), 456, telegram_bot=object(), source=object())

    async def test_new_student_is_confirmed_before_email_request_without_a_payment(self):
        result = await self.confirm()
        self.assertEqual(result, {"kind": "email_required"})
        self.assertEqual(self.booking["status"], "confirmed")
        self.assertEqual(self.booking["payment_delivery_state"], "awaiting_email")
        self.assertIsNone(self.booking["tinkoff_payment_id"])
        self.assertEqual(self.pending["telegram", 123], 7)
        self.assertTrue(can_offer_separate_payment(self.booking))
        self.legacy.create_and_send_payment.assert_not_awaited()
        self.assertIn("Занятие подтверждено", self.legacy.send_to_user.await_args.args[2])
        self.db.change_booking_status.assert_awaited_once()

    async def test_existing_email_creates_payment_for_the_same_confirmed_booking(self):
        self.emails["telegram", 123] = "student@example.com"

        async def pay(source, bot, booking, email, booking_id):
            self.assertEqual(self.booking["status"], "confirmed")
            self.assertEqual(booking["status"], "confirmed")
            self.assertEqual((email, booking_id), ("student@example.com", 7))
            return {"payment_id": "payment-1", "link_sent": True}

        self.legacy.create_and_send_payment.side_effect = pay
        self.assertEqual((await self.confirm())["kind"], "payment_created")
        self.assertEqual(self.pending, {})

    async def test_bank_failure_does_not_undo_acceptance(self):
        self.emails["telegram", 123] = "student@example.com"
        self.legacy.create_and_send_payment.side_effect = RuntimeError("Bank unavailable")
        self.assertEqual((await self.confirm())["kind"], "payment_failed")
        self.assertEqual(self.booking["status"], "confirmed")
        self.assertEqual(self.booking["payment_delivery_state"], "not_sent")
        self.assertTrue(can_offer_separate_payment(self.booking))

    async def test_repeated_concurrent_click_only_requests_email_once(self):
        snapshot = dict(self.booking)
        results = await asyncio.gather(self.confirm(snapshot), self.confirm(snapshot))
        self.assertCountEqual([r["kind"] for r in results], ["email_required", "already_processed"])
        self.legacy.send_to_user.assert_awaited_once()

    async def test_package_conflicts_do_not_undo_acceptance_or_create_separate_payment(self):
        self.resolver.side_effect = None
        for reason in ("subscription_payment_pending", "legacy_subscription_requires_migration",
                       "ordinary_payment_already_created"):
            with self.subTest(reason=reason):
                self.booking["status"] = "pending"
                self.resolver.return_value = {"error": reason}
                result = await self.confirm()
                self.assertEqual(result["kind"], "subscription_blocked")
                self.assertEqual(self.booking["status"], "confirmed")
        self.legacy.create_and_send_payment.assert_not_awaited()

    async def test_package_can_pay_a_confirmed_lesson_without_email(self):
        async def reserve(*args, **kwargs):
            self.assertEqual(self.booking["status"], "confirmed")
            self.booking["status"] = "paid"
            return {"remaining_lessons": 11}
        self.resolver.side_effect = reserve
        self.assertEqual((await self.confirm())["kind"], "subscription")
        self.assertEqual(self.booking["status"], "paid")
        self.assertEqual(self.pending, {})
        self.legacy.create_and_send_payment.assert_not_awaited()

    async def test_closed_or_paid_booking_is_not_reopened(self):
        for status in ("cancelled", "paid", "completed"):
            self.booking["status"] = status
            self.assertEqual((await self.confirm())["kind"], "already_processed")
            self.assertEqual(self.booking["status"], status)
        self.resolver.assert_not_awaited()

    async def test_email_reply_after_fsm_reset_resumes_the_same_booking(self):
        await self.confirm()
        registered = []
        def register():
            def decorate(fn):
                registered.append(fn)
                return fn
            return decorate
        self.legacy.dp = SimpleNamespace(message=register)
        self.legacy.Message = self.legacy.Bot = self.legacy.FSMContext = object
        self.legacy.valid_email = lambda value: "@" in value
        email_flow.install_telegram_email_hardening(SimpleNamespace(legacy=self.legacy))
        state = SimpleNamespace(clear=AsyncMock())  # No pending_booking_id in FSM.
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), text="student@example.com",
                                  answer=AsyncMock())
        original = AsyncMock(return_value={"payment_id": "payment-1", "link_sent": True})
        async def create(*args):
            self.assertEqual(args[-1], 7)
            return await safety._track_payment_delivery(7, lambda: original(*args))
        self.legacy.create_and_send_payment.side_effect = create
        with patch.object(email_flow.accounts, "get_pending_email_request", AsyncMock(return_value=7)), \
             patch.object(email_flow.accounts, "delete_pending_email_request", AsyncMock()), \
             patch.object(email_flow._db, "account_owns_booking", AsyncMock(return_value=True)), \
             patch.object(email_flow._db, "set_student_email", AsyncMock()) as save_email:
            await registered[0](message, object(), state)
        save_email.assert_awaited_once_with("telegram", 123, "student@example.com")
        self.assertEqual(self.booking["status"], "confirmed")
        self.assertEqual(self.booking["payment_delivery_state"], "sent")
        self.db.change_booking_status.assert_awaited_once()
        original.assert_awaited_once()

    async def test_delivery_failure_clears_email_wait_but_keeps_lesson_payable(self):
        await self.confirm()
        result = await safety._track_payment_delivery(
            7, AsyncMock(return_value={"payment_id": "payment-1", "link_sent": False}))
        self.assertFalse(result["link_sent"])
        self.assertEqual(self.booking["payment_delivery_state"], "not_sent")
        self.assertEqual(self.booking["status"], "confirmed")
        self.assertTrue(can_offer_separate_payment(self.booking))

    def test_waiting_email_is_visible_only_for_confirmed_regular_lessons(self):
        booking = dict(self.booking, status="confirmed", payment_delivery_state="awaiting_email")
        self.assertIn("e-mail", payment_delivery_note(booking))
        for status in ("pending", "paid", "completed", "cancelled"):
            self.assertEqual(payment_delivery_note(dict(booking, status=status)), "")
        self.assertEqual(payment_delivery_note(dict(booking, booking_type="trial")), "")
        self.assertEqual(payment_delivery_note(dict(booking, payment_delivery_state=None)), "")


if __name__ == "__main__":
    unittest.main()
