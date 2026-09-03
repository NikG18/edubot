"""Cross-platform student statistics based on canonical student_id."""

from __future__ import annotations

import database as _db


async def _stats_rows(year: int | None = None, month: int | None = None):
    await _db._ensure_pool()
    date_filter = ""
    args = []
    if year is not None and month is not None:
        date_filter = (
            " AND EXTRACT(YEAR FROM to_date(b.date,'DD.MM.YYYY'))=$1"
            " AND EXTRACT(MONTH FROM to_date(b.date,'DD.MM.YYYY'))=$2"
        )
        args = [int(year), int(month)]

    async with _db._legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            WITH booking_stats AS (
                SELECT b.student_id,
                       MAX(b.username) AS username,
                       COUNT(*) FILTER (WHERE b.stats_counted=TRUE) AS completed_lessons
                FROM bookings b
                WHERE b.student_id IS NOT NULL {date_filter}
                GROUP BY b.student_id
            ), subscription_stats AS (
                SELECT s.student_id,
                       COALESCE(SUM(s.remaining_lessons) FILTER (
                           WHERE s.active=1 AND s.remaining_lessons>0
                       ),0) AS remaining_subscription_lessons
                FROM subscriptions s
                WHERE s.student_id IS NOT NULL
                GROUP BY s.student_id
            ), account_labels AS (
                SELECT a.student_id,
                       MAX(a.platform_user_id) FILTER (WHERE a.platform='telegram') AS telegram_id,
                       MAX(a.platform_user_id) FILTER (WHERE a.platform='vk') AS vk_id
                FROM student_accounts a
                GROUP BY a.student_id
            )
            SELECT bs.student_id,bs.username,bs.completed_lessons,
                   COALESCE(ss.remaining_subscription_lessons,0) AS remaining_subscription_lessons,
                   al.telegram_id,al.vk_id
            FROM booking_stats bs
            LEFT JOIN subscription_stats ss ON ss.student_id=bs.student_id
            LEFT JOIN account_labels al ON al.student_id=bs.student_id
            ORDER BY bs.student_id
            """,
            *args,
        )
    return rows


async def get_students_stats():
    rows = await _stats_rows()
    return [_to_result(row) for row in rows]


async def get_students_stats_by_month(year, month):
    rows = await _stats_rows(int(year), int(month))
    return [_to_result(row) for row in rows]


def _to_result(row) -> dict:
    # `user_id` is retained for legacy display compatibility. Prefer Telegram id,
    # then VK id, while exposing canonical student_id and both account ids too.
    display_id = row["telegram_id"] or row["vk_id"] or row["student_id"]
    return {
        "user_id": int(display_id),
        "student_id": int(row["student_id"]),
        "telegram_id": int(row["telegram_id"]) if row["telegram_id"] is not None else None,
        "vk_id": int(row["vk_id"]) if row["vk_id"] is not None else None,
        "username": row["username"] or f"Ученик #{row['student_id']}",
        "completed_lessons": int(row["completed_lessons"] or 0),
        "remaining_subscription_lessons": int(row["remaining_subscription_lessons"] or 0),
    }


def install_student_stats_hardening(app) -> None:
    legacy = app.legacy
    for target in (_db, _db._legacy, legacy):
        target.get_students_stats = get_students_stats
        target.get_students_stats_by_month = get_students_stats_by_month
