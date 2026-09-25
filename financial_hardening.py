"""Financial correctness layer installed on top of the legacy database API.

Automatic commission is a half-month payout tier for a third-party tutor. Direct owner lessons
remain zero-commission. Manual mode keeps the immutable booking snapshots so later
admin edits do not rewrite historical months.
"""

from __future__ import annotations

from datetime import date, datetime

import database as _db
import payments
from agent_report_rules import percent_amount_kop
from financial_rules import (
    booking_revenue_rub,
    commission_rate,
    early_fifteen_unlock_date,
    payout_commission_rates,
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


async def _auto_period_rates(conn, tutor_id: int, year: int, month: int):
    first = await conn.fetchval(
        """SELECT MIN(to_date(date,'DD.MM.YYYY')) FROM bookings
           WHERE tutor_id=$1 AND stats_counted=TRUE AND booking_type<>'trial'""",
        int(tutor_id),
    )
    lessons = await _month_lesson_count(conn, tutor_id, year, month)
    if not first:
        return (25, 25), lessons
    unlock_date = await _early_unlock_date(conn, tutor_id, first)
    py, pm = _previous_month(year, month)
    previous_lessons = await _month_lesson_count(conn, tutor_id, py, pm)
    def natural(y, m, count):
        return commission_rate(
            lessons_this_month=count,
            full_months_since_first_lesson=_full_months_since(first, y, m),
            early_fifteen_unlocked=bool(unlock_date and unlock_date < _month_end_date(y, m)),
        ).percent
    return payout_commission_rates(
        natural(py, pm, previous_lessons), natural(year, month, lessons)
    ), lessons


async def calculate_auto_commission(tutor_id: int, year: int, month: int,
                                    period_no: int | None = None, *, conn=None):
    """Use an explicit payout half for reports, regardless of generation date.

    Payment callers without a half get today's applicable rate. For other months
    the default is the closing half. Current-month rates remain provisional until
    all counted lessons are known; reports freeze the final financial values.
    """
    if period_no is None:
        now = datetime.now(_db.MSK)
        period_no = 1 if (year, month) == (now.year, now.month) and now.day <= 15 else 2
    if period_no not in (1, 2):
        raise ValueError("period_no must be 1 or 2")
    if conn is None:
        await _db._ensure_pool()
        async with _db._legacy.pool.acquire() as connection:
            rates, lessons = await _auto_period_rates(connection, tutor_id, year, month)
    else:
        rates, lessons = await _auto_period_rates(conn, tutor_id, year, month)
    return rates[period_no - 1], lessons


async def _month_rows(conn, tutor_id: int, year: int, month: int):
    return await conn.fetch(
        """
        SELECT b.*, s.price AS fallback_price
        FROM bookings b
        LEFT JOIN LATERAL (
            SELECT price FROM subjects WHERE tutor_id=b.tutor_id AND name=b.subject
            ORDER BY id DESC LIMIT 1
        ) s ON TRUE
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
        direct_owner = payments.is_operator_tutor(tutor.get("inn"))
        rates = (0, 0)
        if not direct_owner and tutor.get("commission_mode") == "auto":
            rates, _ = await _auto_period_rates(conn, tutor_id, year, month)
        display_percent = 0 if direct_owner else int(tutor.get("commission_percent", 25))
        if not direct_owner and tutor.get("commission_mode") == "auto":
            now = datetime.now(_db.MSK)
            first_half_now = (year, month) == (now.year, now.month) and now.day <= 15
            display_percent = rates[0 if first_half_now else 1]
        commission_kop = 0
        # Existing PDF snapshots remain authoritative after rate/mode edits.
        reports = {}
        if await conn.fetchval("SELECT to_regclass('agent_reports')"):
            reports = {int(r["period_no"]): r for r in await conn.fetch(
                "SELECT * FROM agent_reports WHERE tutor_id=$1 AND year=$2 AND month=$3",
                int(tutor_id), int(year), int(month),
            )}
        gross_kop = 0
        lessons = 0
        for half in (1, 2):
            if half in reports:
                frozen = reports[half]
                gross_kop += int(frozen["gross_amount_kop"])
                commission_kop += int(frozen["commission_amount_kop"])
                lessons += int(frozen["lessons_count"])
                continue
            for booking in paid_rows:
                day = datetime.strptime(booking["date"], "%d.%m.%Y").day
                if (1 if day <= 15 else 2) != half:
                    continue
                gross = int(round(booking_revenue_rub(booking, booking.get("fallback_price")) * 100))
                percent = (0 if direct_owner else rates[half - 1]
                           if tutor.get("commission_mode") == "auto"
                           else float(booking.get("commission_percent") or 0))
                gross_kop += gross
                commission_kop += percent_amount_kop(gross, percent)
                lessons += 1
        total_income = gross_kop / 100
        commission = commission_kop / 100

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


async def _category_counts(tutor_id: int, year=None, month=None):
    async with _db._legacy.pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) FILTER (WHERE booking_type='trial') AS trial_lessons,
                      COUNT(*) FILTER (WHERE booking_type<>'trial') AS paid_lessons
               FROM bookings WHERE tutor_id=$1 AND stats_counted=TRUE
                 AND ($2::int IS NULL OR EXTRACT(YEAR FROM to_date(date,'DD.MM.YYYY'))=$2)
                 AND ($3::int IS NULL OR EXTRACT(MONTH FROM to_date(date,'DD.MM.YYYY'))=$3)""",
            int(tutor_id), year, month,
        )
        active = await conn.fetchval(
            """SELECT COUNT(*) FROM subscriptions
               WHERE tutor_id=$1 AND active=1 AND remaining_lessons>0""", int(tutor_id),
        )
    return {"trial_lessons": int(row["trial_lessons"] or 0),
            "paid_lessons": int(row["paid_lessons"] or 0),
            "active_subscriptions": int(active or 0)}


async def get_tutor_financials(tutor_id: int, year: int = None, month: int = None) -> dict:
    await _db._ensure_pool()
    categories = await _category_counts(tutor_id, year, month)
    if year is not None and month is not None:
        await recalculate_monthly_stats(tutor_id, year, month)
        async with _db._legacy.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM monthly_stats WHERE tutor_id=$1 AND year=$2 AND month=$3",
                int(tutor_id), int(year), int(month),
            )
        if not row:
            return {**categories, "total_lessons": 0, "total_income": 0.0, "commission_amount": 0.0,
                    "net_income": 0.0, "commission_percent": 0}
        return {
            **categories,
            "total_lessons": int(row["lessons_count"] or 0),
            "total_income": float(row["total_income"] or 0),
            "commission_amount": float(row["commission_amount"] or 0),
            "net_income": float(row["net_income"] or 0),
            "commission_percent": float(row["commission_percent"] or 0),
        }

    # Recalculate both periods that still have counted bookings and periods that
    # already have a cached monthly_stats row. The latter matters after a full
    # refund removes the last counted lesson from a month: without this union the
    # stale cached income would survive forever in the all-time total.
    async with _db._legacy.pool.acquire() as conn:
        periods = await conn.fetch(
            """
            SELECT year,month
            FROM (
                SELECT DISTINCT
                    EXTRACT(YEAR FROM to_date(date,'DD.MM.YYYY'))::int AS year,
                    EXTRACT(MONTH FROM to_date(date,'DD.MM.YYYY'))::int AS month
                FROM bookings
                WHERE tutor_id=$1 AND stats_counted=TRUE AND booking_type<>'trial'
                UNION
                SELECT year,month
                FROM monthly_stats
                WHERE tutor_id=$1
            ) periods
            ORDER BY year,month
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
    if payments.is_operator_tutor(tutor.get("inn")):
        display_percent = 0
    elif tutor.get("commission_mode") == "auto":
        now = datetime.now(_db.MSK)
        display_percent, _ = await calculate_auto_commission(int(tutor_id), now.year, now.month)
    else:
        display_percent = int(tutor.get("commission_percent", 25))
    return {
        **categories,
        "total_lessons": int(totals["lessons"] or 0),
        "total_income": float(totals["income"] or 0),
        "commission_amount": float(totals["commission"] or 0),
        "net_income": float(totals["net"] or 0),
        "commission_percent": float(display_percent),
    }


async def get_all_tutors_stats_by_month(year=None, month=None):
    result = []
    for tid, tutor in (await _db.get_all_tutors()).items():
        fin = await get_tutor_financials(tid, year, month)
        result.append({**fin, "tutor_id": tid, "name": tutor["name"],
                       "commission": fin["commission_amount"]})
    return result


async def get_all_tutors_stats():
    return await get_all_tutors_stats_by_month()


def install_financial_hardening(app) -> None:
    legacy = app.legacy
    targets = (_db, _db._legacy, legacy)
    for target in targets:
        target.calculate_auto_commission = calculate_auto_commission
        target.recalculate_monthly_stats = recalculate_monthly_stats
        target.get_tutor_financials = get_tutor_financials
        target.get_all_tutors_stats = get_all_tutors_stats
        target.get_all_tutors_stats_by_month = get_all_tutors_stats_by_month
