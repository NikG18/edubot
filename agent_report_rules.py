from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


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
    pct = Decimal(str(percent))
    return int((Decimal(amount) * pct / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
