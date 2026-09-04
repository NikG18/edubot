"""Financial correctness layer installed on top of the legacy database API.

Automatic commission is a MONTHLY tier for a third-party tutor. Direct owner lessons
remain zero-commission. Manual mode keeps the immutable booking snapshots so later
admin edits do not rewrite historical months.
"""

from __future__ import annotations

from datetime import date

import database as _db
import payments
from financial_rules import (
    booking_commission_rub,
    booking_revenue_rub,
    commission_rate,
    early_fifteen_unlock_date,
)


async def _first_lesson_date(tutor_id: int):
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT MIN(to_date(date,'DD.MM.YYYY'))
            FROM bookings
            WHERE tutor_id=$1 AND stats_counted=TRUE AND booking_type<>'trial'
            """,
            int(tutor_id),
        )


def _full_months_since(first: date, year: int, month: int) -> int:
    return max(0, (int(year) - first.year) * 12 + (int(month) - first.month))


async def _month_lesson_count(conn, tutor_id: int, year: int, month: int) -> int:
    return int(await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM bookings
        WHERE tutor_id=$1
          AND stats_counted=TRUE
          AND booking_type<>'trial'
          AND EXTRACT(YEAR FROM to_date(date,'DD.MM.YYYY'))=$2
          AND EXTRACT(MONTH FROM to_date(date,'DD.MM.YYYY'))=$3
        """,
        int(tutor_id), int(year), int(month),
    ) or 0)


async def _early_unlock_date(conn, tutor_id: int, first_lesson: date):
    rows = await conn.fetch(
        """
        SELECT to_date(date,'DD.MM.YYYY') AS lesson_date
        FROM bookings
        WHERE tutor_id=$1
          AND stats_counted=TRUE
          AND booking_type<>'trial'
          AND to_date(date,'DD.MM.YYYY') >= $2
          AND to_date(date,'DD.MM.YYYY') < ($2 + INTERVAL '4 months')
        ORDER BY lesson_date, id
        """,
        int(tutor_id), first_lesson,
    )
    return early_fifteen_unlock_date([row["lesson_date"] for row in rows], first_lesson)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if int(month) == 1:
        return int(year) - 1, 12
    return int(year), int(month) - 1


def _month_end_date(year: int, month: int) -> date:
    if int(month) == 12:
        return date(int(year) + 1, 1, 1)
    return date(int(year), int(month) + 1, 1)


async def calculate_auto_commission(tutor_id: int, year: int, month: int):
    """Progressive MONTHLY rate with permanent early unlock and one retention month."""
    await _db._ensure_pool()
    first = await _first_lesson_date(int(tutor_id))
    async with _db._legacy.pool.acquire() as conn:
        lessons = await _month_lesson_count(conn, tutor_id, year, month)
        if not first:
            return 25, lessons

        unlock_date = await _early_unlock_date(conn, tutor_id, first)
        target_month_end = _month_end_date(year, month)
        early_unlocked = bool(unlock_date and unlock_date < target_month_end)

        py, pm = _previous_month(year, month)
        previous_lessons = await _month_lesson_count(conn, tutor_id, py, pm)
        previous_month_end = _month_end_date(py, pm)
        previous_early_unlocked = bool(unlock_date and unlock_date < previous_month_end)
        previous_natural = commission_rate(
            lessons_this_month=previous_lessons,
            full_months_since_first_lesson=_full_months_since(first, py, pm),
            early_fifteen_unlocked=previous_early_unlocked,
            previous_month_percent=None,
        )

        # Always pass the previous month's NATURAL rate. This matters when the
        # current natural rate is 20% but last month naturally achieved 15%: the
        # 15% rate must still be retained for this one following month. Because we
        # never feed a previously-retained rate back in, retention cannot chain.
        decision = commission_rate(
            lessons_this_month=lessons,
            full_months_since_first_lesson=_full_months_since(first, year, month),
            early_fifteen_unlocked=early_unlocked,
            previous_month_percent=previous_natural.percent,
        )
        return decision.percent, lessons


async def _month_rows(conn, tutor_id: int, year: int, month: int):
    return await conn.fetch(
        """
        SELECT b.*, s.price AS fallback_price
        FROM bookings b
        LEFT JOIN subjects s ON s.tutor_id=b.tutor_id AND s.name=b.subject
        WHERE b.tutor_id=$1
          AND b.stats_counted=TRUE
          AND EXTRACT(YEAR FROM to_date(b.date,'DD.MM.YYYY'))=$2
          AND EXTRACT(MONTH FROM to_date(b.date,'DD.MM.YYYY'))=$3
        ORDER BY b.id
        """,
        int(tutor_id), int(year), int(month),
    )


async def recalculate_monthly_stats(tutor_id: int, year: int, month: int):
    await _db._ensure_pool()
    tutors = await _db.get_all_tutors()
    tutor = tutors.get(int(tutor_id))
    if not tutor:
        return

    async with _db._legacy.pool.acquire() as conn:
        rows = await _month_rows(conn, tutor_id, year, month)
        paid_rows = [dict(row) for row in rows if row["booking_type"] != "trial"]
        lessons = len(paid_rows)
        total_income = sum(
            booking_revenue_rub(booking, booking.get("fallback_price"))
            for booking in paid_rows
        )

        direct_owner = payments.is_operator_tutor(tutor.get("inn"))
        if direct_owner:
            display_percent = 0
            commission = 0.0
        elif tutor.get("commission_mode") == "auto":
            display_percent, _ = await calculate_auto_commission(tutor_id, year, month)
            # The achieved auto tier applies to the entire tutor month.
            commission = total_income * float(display_percent) / 100.0
        else:
            display_percent = int(tutor.get("commission_percent", 25))
            # Manual mode preserves the rate snapshot on each booking so an admin
            # edit today does not rewrite already-completed historical lessons.
            commission = 0.0
            for booking in paid_rows:
                revenue = booking_revenue_rub(booking, booking.get("fallback_price"))
                commission += booking_commission_rub(booking, revenue)

        net = total_income - commission
        await conn.execute(
            """
            INSERT INTO monthly_stats
                (tutor_id,year,month,lessons_count,total_income,commission_amount,net_income,
                 commission_mode,commission_percent)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT(tutor_id,year,month) DO UPDATE SET
                lessons_count=EXCLUDED.lessons_count,
                total_income=EXCLUDED.total_income,
                commission_amount=EXCLUDED.commission_amount,
                net_income=EXCLUDED.net_income,
                commission_mode=EXCLUDED.commission_mode,
                commission_percent=EXCLUDED.commission_percent
            """,
            int(tutor_id), int(year), int(month), lessons,
            total_income, commission, net,
            tutor.get("commission_mode", "manual"), display_percent,
        )


async def get_tutor_financials(tutor_id: int, year: int = None, month: int = None) -> dict:
    await _db._ensure_pool()
    if year is not None and month is not None:
        await recalculate_monthly_stats(tutor_id, year, month)
        async with _db._legacy.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM monthly_stats WHERE tutor_id=$1 AND year=$2 AND month=$3",
                int(tutor_id), int(year), int(month),
            )
        if not row:
            return {"total_lessons": 0, "total_income": 0.0, "commission_amount": 0.0,
                    "net_income": 0.0, "commission_percent": 0}
        return {
            "total_lessons": int(row["lessons_count"] or 0),
            "total_income": float(row["total_income"] or 0),
            "commission_amount": float(row["commission_amount"] or 0),
            "net_income": float(row["net_income"] or 0),
            "commission_percent": float(row["commission_percent"] or 0),
        }

    # Rebuild every month before all-time aggregation so auto tiers are applied to
    # complete months rather than stale per-booking snapshots.
    async with _db._legacy.pool.acquire() as conn:
        periods = await conn.fetch(
            """
            SELECT DISTINCT
                EXTRACT(YEAR FROM to_date(date,'DD.MM.YYYY'))::int AS year,
                EXTRACT(MONTH FROM to_date(date,'DD.MM.YYYY'))::int AS month
            FROM bookings
            WHERE tutor_id=$1 AND stats_counted=TRUE AND booking_type<>'trial'
            ORDER BY year, month
            """,
            int(tutor_id),
        )
    for period in periods:
        await recalculate_monthly_stats(tutor_id, period["year"], period["month"])

    async with _db._legacy.pool.acquire() as conn:
        totals = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(lessons_count),0) AS lessons,
                   COALESCE(SUM(total_income),0) AS income,
                   COALESCE(SUM(commission_amount),0) AS commission,
                   COALESCE(SUM(net_income),0) AS net
            FROM monthly_stats WHERE tutor_id=$1
            """,
            int(tutor_id),
        )
    tutors = await _db.get_all_tutors()
    tutor = tutors.get(int(tutor_id), {})
    display_percent = 0 if payments.is_operator_tutor(tutor.get("inn")) else int(tutor.get("commission_percent", 25))
    return {
        "total_lessons": int(totals["lessons"] or 0),
        "total_income": float(totals["income"] or 0),
        "commission_amount": float(totals["commission"] or 0),
        "net_income": float(totals["net"] or 0),
        "commission_percent": display_percent,
    }


def install_financial_hardening(app) -> None:
    legacy = app.legacy
    targets = (_db, _db._legacy, legacy)
    for target in targets:
        target.calculate_auto_commission = calculate_auto_commission
        target.recalculate_monthly_stats = recalculate_monthly_stats
        target.get_tutor_financials = get_tutor_financials
