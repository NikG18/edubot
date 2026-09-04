"""Pure financial rules shared by statistics and payment code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CommissionDecision:
    percent: int
    reason: str


def add_calendar_months(value: date, months: int) -> date:
    """Add calendar months without external dependencies."""
    month_index = value.month - 1 + int(months)
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    month_lengths = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return date(year, month, min(value.day, month_lengths[month - 1]))


def early_fifteen_unlock_date(
    lesson_dates: list[date] | tuple[date, ...],
    first_lesson_date: date,
    *,
    threshold: int = 100,
    window_days: int = 60,
    eligibility_months: int = 4,
) -> date | None:
    """Return the first date when the 4-month wait is permanently bypassed.

    During the first four calendar months of work, a tutor can unlock access to the
    15% tier early by completing at least ``threshold`` lessons in any rolling
    ``window_days``-day window. Once reached, the unlock is permanent; future months
    use the ordinary 41+ lesson condition exactly as if four months had elapsed.
    """
    if not first_lesson_date or threshold <= 0 or window_days <= 0:
        return None

    cutoff = add_calendar_months(first_lesson_date, eligibility_months)
    dates = sorted(
        d for d in lesson_dates
        if isinstance(d, date) and first_lesson_date <= d < cutoff
    )
    left = 0
    for right, current in enumerate(dates):
        # A 60-day inclusive window allows a maximum date difference of 59 days.
        while left <= right and (current - dates[left]).days >= window_days:
            left += 1
        if right - left + 1 >= int(threshold):
            return current
    return None


def commission_rate(
    *,
    lessons_this_month: int,
    full_months_since_first_lesson: int,
    early_fifteen_unlocked: bool = False,
    previous_month_percent: int | None = None,
) -> CommissionDecision:
    """Calculate progressive agent commission.

    Rules:
    - 25% base rate;
    - 20% from 21 lessons after two full months of work;
    - 15% from 41 lessons after four full months of work;
    - the four-month wait for 15% is permanently bypassed if, during those first
      four months, the tutor completes at least 100 lessons in any 60-day window;
    - after that early unlock, the tutor follows the normal 15% rule: 41+ lessons
      in a month, without needing to repeat the 100-in-60 achievement;
    - a naturally achieved 20%/15% rate is retained for one following calendar
      month if the current month's natural rate would be higher.

    ``previous_month_percent`` must be the previous month's *natural* rate, not a
    rate that was itself retained from an even earlier month. This prevents an
    achieved tier from being carried forward indefinitely.
    """
    lessons = max(0, int(lessons_this_month or 0))
    months = max(0, int(full_months_since_first_lesson or 0))

    if lessons >= 41 and (months >= 4 or bool(early_fifteen_unlocked)):
        natural = CommissionDecision(
            15,
            "41+ lessons after early 15% unlock"
            if months < 4 and early_fifteen_unlocked
            else "41+ lessons after 4 full months",
        )
    elif lessons >= 21 and months >= 2:
        natural = CommissionDecision(20, "21+ lessons after 2 full months")
    else:
        natural = CommissionDecision(25, "base rate")

    previous = int(previous_month_percent) if previous_month_percent is not None else None
    if previous in {15, 20} and previous < natural.percent:
        return CommissionDecision(previous, "retained for one calendar month")
    return natural


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
