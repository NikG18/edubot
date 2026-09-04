import unittest

import subscription_hardening as subs


class FakeConn:
    def __init__(self, existing_status):
        self.existing_status = existing_status
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO subscription_closing_receipts"):
            return None
        if normalized.startswith("SELECT * FROM subscription_closing_receipts"):
            return {
                "id": 77,
                "booking_id": 123,
                "status": self.existing_status,
                "attempt_count": 1,
                "response": {},
            }
        if normalized.startswith("UPDATE subscription_closing_receipts"):
            return {
                "id": 77,
                "booking_id": 123,
                "status": "sending",
                "attempt_count": 2,
                "response": {},
            }
        raise AssertionError(f"Unexpected SQL: {normalized}")


class SubscriptionReceiptRetryTests(unittest.IsolatedAsyncioTestCase):
    usage = {
        "subscription_id": 5,
        "payment_id": "pay-1",
        "amount_kop": 12345,
    }

    async def test_explicit_failed_receipt_is_claimed_for_retry(self):
        conn = FakeConn("failed")
        claimed, reason = await subs._claim_subscription_closing_receipt(
            conn, 123, self.usage
        )
        self.assertIsNone(reason)
        self.assertEqual(claimed["status"], "sending")
        self.assertEqual(claimed["attempt_count"], 2)
        self.assertTrue(any(
            "attempt_count=attempt_count+1" in " ".join(sql.split())
            for sql, _args in conn.calls
        ))

    async def test_unknown_receipt_is_not_blindly_retried(self):
        conn = FakeConn("unknown")
        claimed, reason = await subs._claim_subscription_closing_receipt(
            conn, 123, self.usage
        )
        self.assertIsNone(claimed)
        self.assertEqual(reason, "closing_receipt_status_unknown")
        self.assertFalse(any(
            "UPDATE subscription_closing_receipts" in sql
            for sql, _args in conn.calls
        ))

    async def test_submitted_receipt_is_idempotent(self):
        conn = FakeConn("submitted")
        claimed, reason = await subs._claim_subscription_closing_receipt(
            conn, 123, self.usage
        )
        self.assertIsNone(claimed)
        self.assertEqual(reason, "already_submitted")

    async def test_in_progress_receipt_is_not_reclaimed(self):
        conn = FakeConn("sending")
        claimed, reason = await subs._claim_subscription_closing_receipt(
            conn, 123, self.usage
        )
        self.assertIsNone(claimed)
        self.assertEqual(reason, "closing_receipt_in_progress")


if __name__ == "__main__":
    unittest.main()
