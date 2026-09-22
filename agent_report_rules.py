from __future__ import annotations

import calendar
from datetime import date


def period_bounds(year: int, month: int, period_no: int) -> tuple[date, date]:
    year = int(year)
    month = int(month)
    period_no = int(period_no)
    if month < 1 or month > 12:
        raise ValueError("month must be 1..12")
    last_day = calendar.monthrange(year, month)[1]
    if period_no == 1:
        return date(year, month, 1), date(year, month, 15)
    if period_no == 2:
        return date(year, month, 16), date(year, month, last_day)
    raise ValueError("period_no must be 1 or 2")


def period_label(year: int, month: int, period_no: int) -> str:
    start, end = period_bounds(year, month, period_no)
    return f"{start:%d.%m.%Y}-{end:%d.%m.%Y}"


def period_is_closed(today: date, year: int, month: int, period_no: int) -> bool:
    _start, end = period_bounds(year, month, period_no)
    return today > end


def report_key(tutor_id: int, year: int, month: int, period_no: int) -> str:
    period_bounds(year, month, period_no)
    return f"AR-{int(year):04d}{int(month):02d}-P{int(period_no)}-T{int(tutor_id)}"


def percent_amount_kop(amount_kop: int, percent: int | float) -> int:
    amount = int(amount_kop)
    pct = float(percent)
    return int(round(amount * pct / 100.0))


def second_period_commission_adjustment_kop(
    *, first_gross_kop: int, first_commission_kop: int, final_percent: int | float
) -> int:
    target = percent_amount_kop(int(first_gross_kop), final_percent)
    return target - int(first_commission_kop)
