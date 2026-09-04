"""Read-only integrity audit for the hardened subscription ledger.

Run on the server after database migrations, before enabling real subscription
traffic or merging the hardening PR:

    python subscription_audit.py

The script never modifies data. Exit code 0 means no issues were found, 2 means
integrity issues need review, and 1 means the hardened schema is not ready yet.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict

import asyncpg


_REQUIRED_COLUMNS = {
    "subscriptions": {
        "payment_id", "total_price", "customer_email", "supplier_name",
        "supplier_inn", "supplier_phone", "student_id", "user_platform",
    },
    "bookings": {
        "subscription_id", "subscription_unit_index", "subscription_unit_amount",
    },
}
_REQUIRED_TABLES = {"subscription_usages", "subscription_closing_receipts"}


async def _schema_problems(conn) -> list[dict]:
    issues: list[dict] = []
    rows = await conn.fetch(
        """
        SELECT table_name,column_name
        FROM information_schema.columns
        WHERE table_schema='public'
          AND table_name=ANY($1::text[])
        """,
        list(_REQUIRED_COLUMNS),
    )
    columns = defaultdict(set)
    for row in rows:
        columns[row["table_name"]].add(row["column_name"])
    for table, required in _REQUIRED_COLUMNS.items():
        missing = sorted(required - columns.get(table, set()))
        if missing:
            issues.append({
                "kind": "schema_missing_columns",
                "table": table,
                "missing": missing,
            })

    existing_tables = set(await conn.fetchval(
        """
        SELECT COALESCE(array_agg(tablename), ARRAY[]::name[])
        FROM pg_tables
        WHERE schemaname='public' AND tablename=ANY($1::text[])
        """,
        list(_REQUIRED_TABLES),
    ) or [])
    for table in sorted(_REQUIRED_TABLES - existing_tables):
        issues.append({"kind": "schema_missing_table", "table": table})
    return issues


async def collect_subscription_integrity_issues(conn) -> list[dict]:
    """Return data-integrity findings without changing any row."""
    schema_issues = await _schema_problems(conn)
    if schema_issues:
        return schema_issues

    issues: list[dict] = []

    legacy_rows = await conn.fetch(
        """
        SELECT id,student_id,tutor_id,subject,total_lessons,remaining_lessons,active
        FROM subscriptions
        WHERE remaining_lessons>0 AND payment_id IS NULL
        ORDER BY id
        """
    )
    for row in legacy_rows:
        issues.append({
            "kind": "legacy_balance_without_payment_snapshot",
            "subscription_id": int(row["id"]),
            "student_id": int(row["student_id"]) if row["student_id"] is not None else None,
            "tutor_id": int(row["tutor_id"]),
            "subject": row["subject"],
            "total_lessons": int(row["total_lessons"]),
            "remaining_lessons": int(row["remaining_lessons"]),
            "active": int(row["active"]),
        })

    incomplete_rows = await conn.fetch(
        """
        SELECT id,student_id,tutor_id,subject,payment_id,total_price
        FROM subscriptions
        WHERE payment_id IS NOT NULL
          AND (
            total_price IS NULL OR total_price<=0 OR
            NULLIF(BTRIM(customer_email),'') IS NULL OR
            NULLIF(BTRIM(supplier_name),'') IS NULL OR
            NULLIF(BTRIM(supplier_inn),'') IS NULL OR
            NULLIF(BTRIM(supplier_phone),'') IS NULL
          )
        ORDER BY id
        """
    )
    for row in incomplete_rows:
        issues.append({
            "kind": "incomplete_fiscal_snapshot",
            "subscription_id": int(row["id"]),
            "student_id": int(row["student_id"]) if row["student_id"] is not None else None,
            "tutor_id": int(row["tutor_id"]),
            "subject": row["subject"],
            "payment_id": row["payment_id"],
            "total_price": str(row["total_price"]) if row["total_price"] is not None else None,
        })

    balance_rows = await conn.fetch(
        """
        WITH active_ledger AS (
            SELECT s.id,
                   COUNT(u.id)::int AS occupied
            FROM subscriptions s
            LEFT JOIN subscription_usages u
              ON u.subscription_id=s.id
             AND u.status IN ('reserved','consumed')
            WHERE s.payment_id IS NOT NULL
            GROUP BY s.id
        )
        SELECT s.id,s.total_lessons,s.remaining_lessons,s.active,
               a.occupied,
               GREATEST(s.total_lessons-a.occupied,0) AS expected_remaining,
               CASE WHEN s.total_lessons-a.occupied>0 THEN 1 ELSE 0 END AS expected_active
        FROM subscriptions s
        JOIN active_ledger a ON a.id=s.id
        WHERE s.payment_id IS NOT NULL
          AND (
            s.remaining_lessons<>GREATEST(s.total_lessons-a.occupied,0)
            OR s.active<>CASE WHEN s.total_lessons-a.occupied>0 THEN 1 ELSE 0 END
          )
        ORDER BY s.id
        """
    )
    for row in balance_rows:
        issues.append({
            "kind": "balance_mismatch",
            "subscription_id": int(row["id"]),
            "total_lessons": int(row["total_lessons"]),
            "occupied": int(row["occupied"]),
            "remaining_lessons": int(row["remaining_lessons"]),
            "expected_remaining": int(row["expected_remaining"]),
            "active": int(row["active"]),
            "expected_active": int(row["expected_active"]),
        })

    invalid_units = await conn.fetch(
        """
        SELECT s.id AS subscription_id,s.total_lessons,u.id AS usage_id,
               u.booking_id,u.unit_index,u.status
        FROM subscriptions s
        JOIN subscription_usages u ON u.subscription_id=s.id
        WHERE u.status IN ('reserved','consumed')
          AND (u.unit_index<1 OR u.unit_index>s.total_lessons)
        ORDER BY s.id,u.id
        """
    )
    for row in invalid_units:
        issues.append({
            "kind": "active_unit_out_of_bounds",
            "subscription_id": int(row["subscription_id"]),
            "usage_id": int(row["usage_id"]),
            "booking_id": int(row["booking_id"]),
            "unit_index": int(row["unit_index"]),
            "total_lessons": int(row["total_lessons"]),
            "status": row["status"],
        })

    usage_rows = await conn.fetch(
        """
        SELECT u.id AS usage_id,u.subscription_id,u.booking_id,u.unit_index,
               u.amount_kop,u.status,
               b.status AS booking_status,b.subscription_id AS booking_subscription_id,
               b.subscription_unit_index,b.subscription_unit_amount
        FROM subscription_usages u
        JOIN bookings b ON b.id=u.booking_id
        WHERE
             b.subscription_id IS DISTINCT FROM u.subscription_id
          OR b.subscription_unit_index IS DISTINCT FROM u.unit_index
          OR b.subscription_unit_amount IS DISTINCT FROM u.amount_kop
          OR (u.status='reserved' AND b.status<>'paid')
          OR (u.status='consumed' AND b.status<>'completed')
          OR (u.status='released' AND b.status<>'cancelled')
        ORDER BY u.subscription_id,u.id
        """
    )
    for row in usage_rows:
        issues.append({
            "kind": "usage_booking_mismatch",
            "usage_id": int(row["usage_id"]),
            "subscription_id": int(row["subscription_id"]),
            "booking_id": int(row["booking_id"]),
            "usage_status": row["status"],
            "booking_status": row["booking_status"],
            "unit_index": int(row["unit_index"]),
            "booking_unit_index": (
                int(row["subscription_unit_index"])
                if row["subscription_unit_index"] is not None else None
            ),
            "amount_kop": int(row["amount_kop"]),
            "booking_amount_kop": (
                int(row["subscription_unit_amount"])
                if row["subscription_unit_amount"] is not None else None
            ),
        })

    return issues


def _print_report(issues: list[dict]) -> None:
    if not issues:
        print("OK: subscription ledger integrity issues not found")
        return
    counts = defaultdict(int)
    for issue in issues:
        counts[issue["kind"]] += 1
    print(f"FOUND {len(issues)} subscription integrity issue(s):")
    for kind in sorted(counts):
        print(f"  {kind}: {counts[kind]}")
    print("\nDetails:")
    for issue in issues:
        fields = ", ".join(f"{key}={value}" for key, value in issue.items() if key != "kind")
        print(f"- {issue['kind']}: {fields}")


async def _main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set")
        return 1
    conn = await asyncpg.connect(database_url, command_timeout=30)
    try:
        issues = await collect_subscription_integrity_issues(conn)
    finally:
        await conn.close()

    _print_report(issues)
    if any(issue["kind"].startswith("schema_missing_") for issue in issues):
        return 1
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
