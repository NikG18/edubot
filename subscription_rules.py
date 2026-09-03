"""Pure rules for subscription price allocation, independent of database/network code."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def rub_to_kop(value) -> int:
    return int((Decimal(str(value or 0)) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def allocated_unit_amount(total_kop: int, total_lessons: int, unit_index: int) -> int:
    """Split a package total exactly across lessons without losing kopecks."""
    total_kop = int(total_kop)
    total_lessons = int(total_lessons)
    unit_index = int(unit_index)
    if total_kop <= 0 or total_lessons <= 0 or not 1 <= unit_index <= total_lessons:
        raise ValueError("invalid subscription allocation")
    base, remainder = divmod(total_kop, total_lessons)
    return base + (1 if unit_index <= remainder else 0)
