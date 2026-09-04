"""Resolve package coverage before an ordinary lesson payment can be created."""

from __future__ import annotations

import logging

import database as db
import payments
import subscription_booking as subscription_flow
import subscription_hardening as subs
from booking_visibility_rules import is_trial_booking


PENDING_PACKAGE_ERRORS = {"subscription_payment_pending", "payment_status_unknown"}


async def _matching_package_candidate_exists(booking: dict) -> bool:
    """Return whether this booking has any package state that could cover it.

    This is deliberately broader than "usable active subscription". If an ordinary
    lesson PaymentId already exists, any matching pending purchase, positive package
    balance, or existing package usage creates a potential double-charge conflict.
    In that situation package consumption must fail closed until the individual
    payment is resolved instead of silently switching the booking to a package.
    """
    student_id = booking.get("student_id")
    if not student_id:
        return False
    await subs.ensure_subscription_schema()
    await db._ensure_pool()
    async with db._legacy.pool.acquire() as conn:
        return bool(await conn.fetchval(
            """
            SELECT
                EXISTS(
                    SELECT 1 FROM pending_subscriptions p
                    WHERE p.student_id=$1 AND p.tutor_id=$2 AND p.subject=$3
                )
                OR EXISTS(
                    SELECT 1 FROM subscriptions s
                    WHERE s.student_id=$1 AND s.tutor_id=$2 AND s.subject=$3
                      AND s.remaining_lessons>0
                )
                OR EXISTS(
                    SELECT 1 FROM subscription_usages u
                    WHERE u.booking_id=$4 AND u.status IN ('reserved','consumed')
                )
            """,
            int(student_id), int(booking["tutor_id"]), str(booking["subject"]),
            int(booking["id"]),
        ))


async def _matching_pending_purchase(booking: dict) -> dict | None:
    student_id = booking.get("student_id")
    if not student_id:
        return None
    await subs.ensure_subscription_schema()
    await db._ensure_pool()
    async with db._legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM pending_subscriptions
            WHERE student_id=$1 AND tutor_id=$2 AND subject=$3
            ORDER BY id DESC
            """,
            int(student_id), int(booking["tutor_id"]), str(booking["subject"]),
        )
    if not rows:
        return None
    if len(rows) > 1:
        logging.error(
            "Multiple pending subscription purchases for booking=%s student=%s tutor=%s subject=%s",
            booking.get("id"), student_id, booking.get("tutor_id"), booking.get("subject"),
        )
        return {"error": "duplicate_pending_subscriptions"}

    pending = rows[0]
    payment_id = str(pending["payment_id"] or "")
    if not payment_id:
        return {"error": "subscription_payment_pending"}

    state = await payments.check_payment(payment_id)
    if not state.get("Success"):
        return {"error": "payment_status_unknown", "payment_id": payment_id}
    status = str(state.get("Status") or "").upper()
    if status in {"CONFIRMED", "AUTHORIZED"}:
        activated = await subs.activate_subscription(payment_id)
        if not activated:
            return {"error": "subscription_activation_failed", "payment_id": payment_id}
        return {"activated": True, "payment_id": payment_id}
    if status in {"REJECTED", "CANCELED"}:
        async with db._legacy.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pending_subscriptions WHERE id=$1", int(pending["id"])
            )
        return None
    return {
        "error": "subscription_payment_pending",
        "payment_id": payment_id,
        "payment_status": status,
    }


async def resolve_booking_subscription(
    legacy,
    booking_id: int,
    actor_id: int,
    *,
    actor_type: str,
    allowed_statuses: set[str],
) -> dict | None:
    """Return package reservation/error, or None only when ordinary payment is safe."""
    booking = await db.get_booking(int(booking_id))
    if (
        not booking
        or is_trial_booking(booking)
        or booking.get("status") not in set(allowed_statuses)
    ):
        return None

    # Never silently convert a booking to package payment after a separate T-Bank
    # payment has already been created. The old payment link may still be payable.
    # If no matching package state exists, returning None lets the idempotent
    # ordinary-payment layer safely reissue the same PaymentId instead of creating
    # another one.
    ordinary_payment_id = str(booking.get("tinkoff_payment_id") or "").strip()
    if ordinary_payment_id:
        if await _matching_package_candidate_exists(booking):
            return {
                "error": "ordinary_payment_already_created",
                "payment_id": ordinary_payment_id,
            }
        return None

    pending = await _matching_pending_purchase(booking)
    if pending and pending.get("error"):
        return pending

    commission_percent = await subscription_flow._commission_percent(legacy, booking)
    await subs.ensure_subscription_schema()
    await db._ensure_pool()
    async with db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM bookings WHERE id=$1 FOR UPDATE", int(booking_id)
            )
            if (
                not current
                or current["status"] not in set(allowed_statuses)
                or current["booking_type"] == "trial"
            ):
                return None
            old_status = str(current["status"])

            # Backstop the preflight under the booking row lock. This catches an
            # individual payment saved between the first read and this transaction.
            if current["tinkoff_payment_id"]:
                return {
                    "error": "ordinary_payment_already_created",
                    "payment_id": str(current["tinkoff_payment_id"]),
                }

            existing = await conn.fetchrow(
                "SELECT * FROM subscription_usages WHERE booking_id=$1 FOR UPDATE",
                int(booking_id),
            )
            if existing and existing["status"] in {"reserved", "consumed"}:
                subscription = await conn.fetchrow(
                    "SELECT remaining_lessons FROM subscriptions WHERE id=$1",
                    existing["subscription_id"],
                )
                await conn.execute(
                    """
                    UPDATE bookings
                    SET status='paid',subscription_id=$1,subscription_unit_index=$2,
                        subscription_unit_amount=$3,amount=$3,commission_percent=$4,
                        reminded=0,updated_at=NOW()
                    WHERE id=$5
                    """,
                    existing["subscription_id"], existing["unit_index"],
                    existing["amount_kop"], int(commission_percent), int(booking_id),
                )
                result = {
                    **dict(existing),
                    "remaining_lessons": int(subscription["remaining_lessons"] or 0) if subscription else 0,
                    "already_reserved": True,
                }
                if old_status != "paid":
                    await db._add_booking_event(
                        conn, int(booking_id), "subscription_reserved", old_status, "paid",
                        str(actor_type), int(actor_id),
                        {
                            "subscription_id": int(existing["subscription_id"]),
                            "unit_index": int(existing["unit_index"]),
                            "amount_kop": int(existing["amount_kop"]),
                            "remaining_lessons": result["remaining_lessons"],
                            "recovered_existing_reservation": True,
                        },
                    )
            elif existing and existing["status"] == "released":
                return {"error": "released_booking_usage"}
            else:
                subscription = await conn.fetchrow(
                    """
                    SELECT * FROM subscriptions
                    WHERE student_id=$1 AND tutor_id=$2 AND subject=$3
                      AND remaining_lessons>0
                    ORDER BY
                      CASE WHEN active=1 AND payment_id IS NOT NULL THEN 0 ELSE 1 END,
                      activated_at NULLS LAST,
                      id
                    LIMIT 1 FOR UPDATE
                    """,
                    current["student_id"], current["tutor_id"], current["subject"],
                )
                if not subscription:
                    return None
                if not subscription["payment_id"]:
                    return {"error": "legacy_subscription_requires_migration"}
                if int(subscription["active"] or 0) != 1:
                    return {"error": "subscription_quarantined"}
                if not subscription_flow._subscription_fiscal_snapshot_complete(subscription):
                    return {"error": "subscription_fiscal_snapshot_incomplete"}

                reservation = await subs.reserve_locked_subscription_unit(
                    conn, int(booking_id), subscription
                )
                if not reservation:
                    return {"error": "subscription_ledger_inconsistent"}
                await conn.execute(
                    """
                    UPDATE bookings
                    SET status='paid',subscription_id=$1,subscription_unit_index=$2,
                        subscription_unit_amount=$3,amount=$3,commission_percent=$4,
                        reminded=0,updated_at=NOW()
                    WHERE id=$5
                    """,
                    subscription["id"], reservation["unit_index"],
                    reservation["amount_kop"], int(commission_percent), int(booking_id),
                )
                await db._add_booking_event(
                    conn, int(booking_id), "subscription_reserved", old_status, "paid",
                    str(actor_type), int(actor_id),
                    {
                        "subscription_id": int(subscription["id"]),
                        "unit_index": int(reservation["unit_index"]),
                        "amount_kop": int(reservation["amount_kop"]),
                        "remaining_lessons": int(reservation["remaining_lessons"]),
                    },
                )
                result = {**reservation, "already_reserved": False}

    await db._sync_booking_record_safely(int(booking_id))
    return result
