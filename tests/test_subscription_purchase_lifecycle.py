import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import subscription_hardening as subscriptions
import subscription_cancel_hardening as cancellation
import subscription_purchase_hardening as purchase
import webhook_server


class _AsyncContext:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _WebhookConnection:
    def __init__(self, events):
        self.events = events

    def transaction(self):
        return _AsyncContext()

    async def execute(self, query, *args):
        self.events.append("lock")

    async def fetchrow(self, query, *args):
        return {
            "user_id": 123,
            "user_platform": "vk",
            "total_lessons": 12,
        }


class _PurchaseConnection:
    def __init__(self):
        self.executions = []

    def transaction(self):
        return _AsyncContext()

    async def execute(self, query, *args):
        self.executions.append((query, args))

    async def fetch(self, query, *args):
        return []


class _Pool:
    def __init__(self, connection, events=None):
        self.connection = connection
        self.events = events

    def acquire(self):
        if self.events is not None:
            self.events.append("acquire")
        return _AsyncContext(self.connection)


class _Request:
    def __init__(self, *, json_value=None, json_error=None, form_value=None):
        self.json_value = json_value
        self.json_error = json_error
        self.form_value = form_value

    async def json(self):
        if self.json_error:
            raise self.json_error
        return self.json_value

    async def post(self):
        return self.form_value


class SubscriptionWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_is_ready_before_webhook_transaction(self):
        events = []
        connection = _WebhookConnection(events)

        async def ensure_schema():
            events.append("schema")

        async def ensure_pool():
            events.append("pool")

        async def activate(payment_id):
            events.append("activate")
            return True

        with (
            patch.object(subscriptions, "ensure_subscription_schema", ensure_schema),
            patch.object(subscriptions, "activate_subscription", activate),
            patch.object(webhook_server._db, "_ensure_pool", ensure_pool),
            patch.object(
                webhook_server._db._legacy,
                "pool",
                _Pool(connection, events),
            ),
        ):
            handled, notification = await webhook_server._handle_subscription_notification(
                "pay-1", "CONFIRMED"
            )

        self.assertTrue(handled)
        self.assertEqual(events[0], "schema")
        self.assertLess(events.index("schema"), events.index("acquire"))
        self.assertIn("activate", events)
        self.assertEqual(notification[0:2], (123, "vk"))

    async def test_notification_payload_accepts_json(self):
        payload = {"PaymentId": "1", "Status": "CONFIRMED"}
        request = _Request(json_value=payload)
        self.assertEqual(
            await webhook_server._read_notification_payload(request), payload
        )

    async def test_notification_payload_accepts_form_data(self):
        payload = {"PaymentId": "2", "Status": "AUTHORIZED"}
        request = _Request(
            json_error=ValueError("not json"), form_value=payload
        )
        self.assertEqual(
            await webhook_server._read_notification_payload(request), payload
        )


class SubscriptionPurchaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_purchase_snapshots_fiscal_details_before_payment(self):
        connection = _PurchaseConnection()
        create_payment = AsyncMock(return_value=("https://pay", "payment-7"))
        set_email = AsyncMock()

        with (
            patch.object(
                purchase._db,
                "get_all_tutors",
                AsyncMock(return_value={
                    7: {
                        "name": "Иван Иванов",
                        "inn": "123456789012",
                        "subjects": {"Математика": 1000},
                    }
                }),
            ),
            patch.object(purchase._db, "get_student_id", AsyncMock(return_value=55)),
            patch.object(purchase._db, "set_student_email", set_email),
            patch.object(purchase._db, "_ensure_pool", AsyncMock()),
            patch.object(purchase._db._legacy, "pool", _Pool(connection)),
            patch.object(purchase.subs, "ensure_subscription_schema", AsyncMock()),
            patch.object(purchase, "get_tutor_phone", AsyncMock(return_value="+79990000000")),
            patch.object(purchase._payments, "is_operator_tutor", return_value=False),
            patch.object(purchase._payments, "create_payment", create_payment),
        ):
            result = await purchase.create_or_reuse_subscription_payment(
                platform="vk",
                platform_user_id=99,
                tutor_id=7,
                subject="Математика",
                lessons_count=12,
                customer_email="student@example.com",
            )

        self.assertTrue(result["ok"])
        create_payment.assert_awaited_once()
        payment_args = create_payment.await_args.kwargs
        self.assertEqual(payment_args["supplier_phone"], "+79990000000")
        insert_query, insert_args = next(
            (query, args)
            for query, args in connection.executions
            if "INSERT INTO pending_subscriptions" in query
        )
        self.assertIn("customer_email", insert_query)
        self.assertEqual(insert_args[-4:], (
            "student@example.com",
            "Иван Иванов",
            "123456789012",
            "+79990000000",
        ))


class SubscriptionCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_central_status_change_releases_reserved_unit(self):
        async def change_status(booking_id, new_status, *args, **kwargs):
            return True, {"id": booking_id, "status": new_status}

        async def cancel_record(booking_id, *args, **kwargs):
            return await cancellation._db.change_booking_status(
                booking_id, "cancelled", *args, **kwargs
            )

        async def update_booking(booking_id, **kwargs):
            return {"id": booking_id, **kwargs}

        release = AsyncMock(return_value=True)
        legacy = SimpleNamespace(
            change_booking_status=change_status,
            cancel_booking_record=cancel_record,
            update_booking=update_booking,
        )
        app = SimpleNamespace(legacy=legacy)

        with (
            patch.object(cancellation._db, "change_booking_status", change_status),
            patch.object(cancellation._db, "cancel_booking_record", cancel_record),
            patch.object(cancellation._db, "update_booking", update_booking),
            patch.object(
                cancellation._db,
                "_subscription_cancel_release_installed",
                False,
                create=True,
            ),
            patch.object(cancellation.subs, "release_booking_unit", release),
        ):
            cancellation.install_subscription_cancel_release(app)
            changed, _booking = await cancellation._db.change_booking_status(
                77,
                "cancelled",
                actor_type="admin",
            )

        self.assertTrue(changed)
        release.assert_awaited_once_with(77)


if __name__ == "__main__":
    unittest.main()
