"""Exercise both payment entrypoints without starting bots or contacting T-Bank."""
import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import payments

ROOT = Path(__file__).resolve().parents[1]


class PaymentActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_confirmed_expiry_allows_a_new_payment(self):
        cases = {
            "DEADLINE_EXPIRED": "renew", "NEW": "reuse", "FORM_SHOWED": "reuse",
            "AUTHORIZED": "paid", "CONFIRMED": "paid", "AUTHORIZING": "blocked",
            "CONFIRMING": "blocked", "REFUNDED": "blocked", "CANCELED": "blocked",
            "REJECTED": "blocked", "UNKNOWN": "blocked",
        }
        for status, expected in cases.items():
            with self.subTest(status=status), patch.object(
                payments, "check_payment", AsyncMock(return_value={"Success": True, "Status": status}),
            ):
                self.assertEqual(await payments.booking_payment_action("old"), expected)
        with patch.object(payments, "check_payment", AsyncMock(return_value={"Success": False})):
            self.assertEqual(await payments.booking_payment_action("old"), "unavailable")


class PaymentRenewalTests(unittest.IsolatedAsyncioTestCase):
    def flow(self, platform, *, action="renew", attached=True, url="new-url", pid="new"):
        booking = dict(id=7, tutor_id=2, user_id=123, username="Ученик", subject="Химия",
                       date="20.10.2026", time_slot="12:00-13:00", status="confirmed",
                       amount=250000, commission_percent=20, tinkoff_payment_id="old",
                       reminded=True, user_platform=platform, booking_type="regular")
        db = SimpleNamespace(get_booking=AsyncMock(return_value=booking),
                             replace_expired_booking_payment=AsyncMock(return_value=attached))
        bank = SimpleNamespace(booking_payment_action=AsyncMock(return_value=action),
                               is_operator_tutor=lambda inn: False, OPERATOR_PHONE="+79990000000",
                               create_payment=AsyncMock(return_value=(url, pid)),
                               cancel_full_payment=AsyncMock())
        legacy = SimpleNamespace(
            get_all_tutors=AsyncMock(return_value={2: dict(name="Репетитор", inn="123456789012",
                                                       subjects={"Химия": 9999}, commission_percent=25)}),
            update_booking=AsyncMock(), send_to_tutor=AsyncMock(), send_to_user=AsyncMock(),
            process_booking_payment_status=AsyncMock(), now_msk_naive=lambda: None, logging=logging,
        )
        namespace = dict(asyncio=asyncio, legacy=legacy, _db=db, _payments=bank,
                         _booking_locks={}, get_tutor_phone=AsyncMock(return_value="+79990000000"),
                         snapshot_booking_prepayment=AsyncMock())
        tree = ast.parse((ROOT / f"payment_reuse_{platform}.py").read_text())
        definitions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "<payment-flow>", "exec"), namespace)
        namespace.update(_respond=AsyncMock(), _save_payment_url=AsyncMock(),
                         _send_payment_link=AsyncMock(return_value=True),
                         _sbp_link_for_existing=AsyncMock(return_value="existing-url"))
        async def run():
            args = (None, None, booking, "student@example.ru", 7) if platform == "telegram" else (
                None, booking, "student@example.ru", 7)
            return await namespace["_idempotent_create_and_send_payment"](*args)
        return run, namespace, db, bank, legacy, booking

    async def test_renewal_preserves_lesson_and_price_without_tutor_notification(self):
        for platform in ("telegram", "vk"):
            with self.subTest(platform=platform):
                run, ns, db, bank, legacy, booking = self.flow(platform)
                before = dict(booking)
                result = await run()
                self.assertTrue(result["renewed"])
                self.assertTrue(result["link_sent"])
                bank.create_payment.assert_awaited_once()
                self.assertEqual(bank.create_payment.await_args.kwargs["booking_id"], 7)
                self.assertEqual(bank.create_payment.await_args.kwargs["amount_kop"], 250000)
                db.replace_expired_booking_payment.assert_awaited_once_with(7, "old", "new")
                legacy.update_booking.assert_not_awaited()
                legacy.send_to_tutor.assert_not_awaited()
                ns["snapshot_booking_prepayment"].assert_awaited_once()
                self.assertEqual(booking, before)

    async def test_active_payment_is_reused(self):
        for platform in ("telegram", "vk"):
            run, ns, db, bank, legacy, _ = self.flow(platform, action="reuse")
            self.assertTrue((await run())["reused"])
            bank.create_payment.assert_not_awaited()
            db.replace_expired_booking_payment.assert_not_awaited()
            ns["_sbp_link_for_existing"].assert_awaited_once_with("old")

    async def test_paid_or_uncertain_payment_never_creates_new_session(self):
        for platform in ("telegram", "vk"):
            for action in ("paid", "blocked", "unavailable"):
                run, ns, db, bank, legacy, _ = self.flow(platform, action=action)
                self.assertFalse((await run())["link_sent"])
                bank.create_payment.assert_not_awaited()
                if action == "paid":
                    legacy.process_booking_payment_status.assert_awaited_once_with(7, "CONFIRMED")

    async def test_failed_init_keeps_old_payment(self):
        for platform in ("telegram", "vk"):
            run, ns, db, bank, legacy, _ = self.flow(platform, pid=None, url=None)
            self.assertEqual((await run())["reason"], "payment_init_failed")
            db.replace_expired_booking_payment.assert_not_awaited()
            legacy.update_booking.assert_not_awaited()

    async def test_booking_changed_during_init_does_not_get_reopened(self):
        for platform in ("telegram", "vk"):
            run, ns, db, bank, legacy, _ = self.flow(platform, attached=False)
            self.assertEqual((await run())["reason"], "booking_changed")
            bank.cancel_full_payment.assert_awaited_once_with("new")
            ns["_send_payment_link"].assert_not_awaited()

    async def test_missing_qr_keeps_new_id_for_next_attempt(self):
        for platform in ("telegram", "vk"):
            run, ns, db, bank, legacy, _ = self.flow(platform, url=None)
            ns["_sbp_link_for_existing"].return_value = None
            self.assertEqual((await run())["reason"], "no_sbp_url")
            db.replace_expired_booking_payment.assert_awaited_once_with(7, "old", "new")
            ns["_send_payment_link"].assert_not_awaited()

    async def test_two_clicks_create_only_one_replacement(self):
        for platform in ("telegram", "vk"):
            run, ns, db, bank, legacy, booking = self.flow(platform)
            bank.booking_payment_action.side_effect = ["renew", "reuse"]
            async def attach(bid, old, new):
                booking["tinkoff_payment_id"] = new
                return True
            db.replace_expired_booking_payment.side_effect = attach
            first, second = await asyncio.gather(run(), run())
            self.assertTrue(first["renewed"])
            self.assertTrue(second["reused"])
            bank.create_payment.assert_awaited_once()
            ns["_sbp_link_for_existing"].assert_awaited_once_with("new")


if __name__ == "__main__":
    unittest.main()
