"""Pure financial rules shared by statistics and payment code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommissionDecision:
    percent: int
    reason: str


def commission_rate(
    *,
    lessons_this_month: int,
    full_months_since_first_lesson: int,
    first_60_days_lessons: int,
    previous_month_percent: int | None = None,
) -> CommissionDecision:
    """Calculate progressive agent commission.

    Rules:
    - 25% base rate;
    - 20% from 21 lessons after two full months of work;
    - 15% from 41 lessons after four full months *and* more than 100 lessons
      during the first 60 days;
    - an achieved 20%/15% rate is retained for one following calendar month
      if the current month's volume alone would move it upward.
    """
    lessons = max(0, int(lessons_this_month or 0))
    months = max(0, int(full_months_since_first_lesson or 0))
    first_60 = max(0, int(first_60_days_lessons or 0))

    if lessons >= 41 and months >= 4 and first_60 > 100:
        return CommissionDecision(15, "41+ lessons, 4+ full months, first 60 days >100 lessons")
    if lessons >= 21 and months >= 2:
        return CommissionDecision(20, "21+ lessons after 2 full months")

    previous = int(previous_month_percent) if previous_month_percent is not None else None
    if previous == 15:
        return CommissionDecision(15, "retained for one calendar month")
    if previous == 20:
        return CommissionDecision(20, "retained for one calendar month")
    return CommissionDecision(25, "base rate")


def booking_revenue_rub(booking: dict, fallback_price_rub: int | float | None = None) -> float:
    """Revenue of one statistically counted booking.

    Free trials never create revenue even if their subject has a normal list price.
    For paid/legacy regular bookings prefer the immutable amount snapshot; only old
    regular rows without an amount may fall back to the current subject price.
    """
    if not booking or not booking.get("stats_counted"):
        return 0.0
    if booking.get("booking_type") == "trial":
        return 0.0
    amount = int(booking.get("amount") or 0)
    if amount > 0:
        return amount / 100.0
    return float(fallback_price_rub or 0)


def booking_commission_rub(booking: dict, revenue_rub: float) -> float:
    """Use the commission snapshot fixed on the booking, never a later tutor rate."""
    if booking.get("booking_type") == "trial":
        return 0.0
    percent = float(booking.get("commission_percent") or 0)
    return float(revenue_rub) * percent / 100.0
