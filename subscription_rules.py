"""Pure rules for subscription price allocation, independent of database/network code."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


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


def _validated_occupied_indices(total_lessons: int, occupied_indices: Iterable[int]) -> set[int]:
    total_lessons = int(total_lessons)
    if total_lessons <= 0:
        raise ValueError("total_lessons must be positive")
    occupied = {int(index) for index in occupied_indices}
    if any(index < 1 or index > total_lessons for index in occupied):
        raise ValueError("active subscription unit index is outside package bounds")
    return occupied


def next_available_unit_index(total_lessons: int, occupied_indices: Iterable[int]) -> int | None:
    """Return the smallest free package slot, ignoring released historical usages.

    Callers pass only currently active (reserved/consumed) unit indices. Reusing the
    smallest free index means repeated reserve/cancel cycles cannot push a 12-lesson
    package to unit 13 while the released rows remain available for audit. Corrupt
    active indices outside 1..N fail closed instead of being silently ignored.
    """
    total_lessons = int(total_lessons)
    occupied = _validated_occupied_indices(total_lessons, occupied_indices)
    for unit_index in range(1, total_lessons + 1):
        if unit_index not in occupied:
            return unit_index
    return None


def remaining_lessons_from_occupied(total_lessons: int, occupied_indices: Iterable[int]) -> int:
    """Derive the available balance from active reserved/consumed ledger slots."""
    total_lessons = int(total_lessons)
    occupied = _validated_occupied_indices(total_lessons, occupied_indices)
    return total_lessons - len(occupied)
