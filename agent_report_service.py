from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime

from aiogram.types import BufferedInputFile

import database as db
import payments
from agent_report_pdf import build_report_pdf
from agent_report_rules import (
    period_bounds,
    period_is_closed,
    period_label,
    percent_amount_kop,
    report_key,
)
from financial_hardening import calculate_auto_commission

_SCHEMA_READY = False


def reports_channel_id() -> int | None:
    raw = str(os.environ.get("REPORTS_CHANNEL_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logging.error("REPORTS_CHANNEL_ID must be an integer Telegram chat id, got %r", raw)
        return None


async def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await db._ensure_pool()
    async with db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('agent-reports-schema-v1', 0))"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_reports (
                    id BIGSERIAL PRIMARY KEY,
                    report_key TEXT NOT NULL UNIQUE,
                    tutor_id INTEGER NOT NULL REFERENCES tutors(id) ON DELETE RESTRICT,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),
                    period_no INTEGER NOT NULL CHECK(period_no IN (1,2)),
                    period_start DATE NOT NULL,
                    period_end DATE NOT NULL,
                    commission_mode TEXT NOT NULL,
                    applied_commission_percent NUMERIC(6,2),
                    lessons_count INTEGER NOT NULL,
                    gross_amount_kop BIGINT NOT NULL,
                    commission_adjustment_kop BIGINT NOT NULL DEFAULT 0,
                    commission_amount_kop BIGINT NOT NULL,
                    tutor_amount_kop BIGINT NOT NULL,
                    pdf_bytes BYTEA NOT NULL,
                    pdf_sha256 TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    archive_sent_at TIMESTAMPTZ,
                    tutor_sent_at TIMESTAMPTZ,
                    UNIQUE(tutor_id,year,month,period_no)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_reports_tutor_period
                    ON agent_reports(tutor_id,year,month,period_no);
                CREATE TABLE IF NOT EXISTS agent_report_items (
                    id BIGSERIAL PRIMARY KEY,
                    report_id BIGINT NOT NULL REFERENCES agent_reports(id) ON DELETE CASCADE,
                    booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE RESTRICT,
                    lesson_date TEXT NOT NULL,
                    time_slot TEXT NOT NULL,
                    student_name TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    gross_amount_kop BIGINT NOT NULL,
                    commission_percent NUMERIC(6,2) NOT NULL,
                    commission_amount_kop BIGINT NOT NULL,
                    tutor_amount_kop BIGINT NOT NULL,
                    UNIQUE(report_id,booking_id)
                );
                """
            )
    _SCHEMA_READY = True


async def _existing(conn, tutor_id: int, year: int, month: int, period_no: int):
    return await conn.fetchrow(
        "SELECT * FROM agent_reports WHERE tutor_id=$1 AND year=$2 AND month=$3 AND period_no=$4",
        int(tutor_id), int(year), int(month), int(period_no),
    )


async def _items(conn, report_id: int) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT * FROM agent_report_items WHERE report_id=$1
        ORDER BY to_date(lesson_date,'DD.MM.YYYY'),time_slot,id
        """,
        int(report_id),
    )
    return [dict(row) for row in rows]


async def get_report(tutor_id: int, year: int, month: int, period_no: int):
    await ensure_schema()
    async with db._legacy.pool.acquire() as conn:
        report = await _existing(conn, tutor_id, year, month, period_no)
        if not report:
            return None
        items = await _items(conn, int(report["id"]))
    return dict(report), items


async def available_months(tutor_id: int) -> list[tuple[int, int]]:
    await ensure_schema()
    async with db._legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT year,month FROM (
                SELECT DISTINCT EXTRACT(YEAR FROM to_date(date,'DD.MM.YYYY'))::int AS year,
                                EXTRACT(MONTH FROM to_date(date,'DD.MM.YYYY'))::int AS month
                FROM bookings WHERE tutor_id=$1 AND booking_type<>'trial'
                UNION
                SELECT year,month FROM agent_reports WHERE tutor_id=$1
            ) months ORDER BY year DESC,month DESC
            """,
            int(tutor_id),
        )
    return [(int(row["year"]), int(row["month"])) for row in rows]


async def _period_rows(conn, tutor_id: int, start, end):
    return await conn.fetch(
        """
        SELECT b.*, price_lookup.price AS fallback_price,
               EXISTS(
                   SELECT 1 FROM booking_events e
                   WHERE e.booking_id=b.id AND e.event_type='late_student_cancel_counted'
               ) AS late_cancel
        FROM bookings b
        LEFT JOIN LATERAL (
            SELECT s.price FROM subjects s
            WHERE s.tutor_id=b.tutor_id AND s.name=b.subject
            ORDER BY s.id DESC LIMIT 1
        ) price_lookup ON TRUE
        WHERE b.tutor_id=$1
          AND b.stats_counted=TRUE
          AND b.booking_type<>'trial'
          AND to_date(b.date,'DD.MM.YYYY') BETWEEN $2 AND $3
        ORDER BY to_date(b.date,'DD.MM.YYYY'),b.time_slot,b.id
        """,
        int(tutor_id), start, end,
    )


def _gross_kop(row: dict) -> int:
    amount = int(row.get("amount") or 0)
    if amount > 0:
        return amount
    return int(round(float(row.get("fallback_price") or 0) * 100))


def _outcome(row: dict) -> str:
    if bool(row.get("late_cancel")):
        return "Поздняя отмена / неявка"
    if str(row.get("status") or "") == "cancelled":
        return "Засчитано; возврат не завершён"
    return "Проведено"


async def create_snapshot(tutor_id: int, year: int, month: int, period_no: int):
    await ensure_schema()
    start, end = period_bounds(year, month, period_no)
    if not period_is_closed(datetime.now(db.MSK).date(), year, month, period_no):
        raise ValueError("report_period_not_closed")

    tutors = await db.get_all_tutors()
    tutor = tutors.get(int(tutor_id))
    if not tutor:
        raise ValueError("tutor_not_found")
    if payments.is_operator_tutor(tutor.get("inn")):
        raise ValueError("operator_tutor_has_no_agent_report")

    key = report_key(tutor_id, year, month, period_no)
    async with db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"agent-report:{key}"
            )
            existing = await _existing(conn, tutor_id, year, month, period_no)
            if existing:
                return dict(existing), await _items(conn, int(existing["id"])), False

            rows = [dict(row) for row in await _period_rows(conn, tutor_id, start, end)]
            if not rows:
                raise ValueError("report_has_no_lessons")

            mode = str(tutor.get("commission_mode") or "manual")
            applied_percent = None
            if mode == "auto":
                applied_percent, _ = await calculate_auto_commission(
                    tutor_id, year, month, period_no, conn=conn
                )
                applied_percent = float(applied_percent)

            items = []
            for row in rows:
                gross = _gross_kop(row)
                percent = (
                    float(applied_percent or 0)
                    if mode == "auto"
                    else float(row.get("commission_percent") or 0)
                )
                commission = percent_amount_kop(gross, percent)
                items.append({
                    "booking_id": int(row["id"]),
                    "lesson_date": str(row["date"]),
                    "time_slot": str(row["time_slot"]),
                    "student_name": str(row.get("username") or f"Ученик #{row['user_id']}"),
                    "subject": str(row.get("subject") or ""),
                    "outcome": _outcome(row),
                    "gross_kop": gross,
                    "commission_percent": percent,
                    "commission_kop": commission,
                    "tutor_kop": gross - commission,
                })

            gross_total = sum(item["gross_kop"] for item in items)
            item_commission = sum(item["commission_kop"] for item in items)
            adjustment = 0

            commission_total = item_commission + adjustment
            tutor_total = gross_total - commission_total
            created = datetime.now(db.MSK)
            pdf = build_report_pdf({
                "report_key": key,
                "period_label": period_label(year, month, period_no),
                "agent_name": payments.OPERATOR_NAME,
                "agent_inn": payments.OPERATOR_INN,
                "tutor_name": tutor.get("name") or f"Репетитор #{tutor_id}",
                "tutor_inn": tutor.get("inn") or "",
                "lessons_count": len(items),
                "gross_kop": gross_total,
                "commission_adjustment_kop": adjustment,
                "commission_kop": commission_total,
                "tutor_kop": tutor_total,
                "created_label": created.strftime("%d.%m.%Y %H:%M МСК"),
            }, items)
            report = await conn.fetchrow(
                """
                INSERT INTO agent_reports(
                    report_key,tutor_id,year,month,period_no,period_start,period_end,
                    commission_mode,applied_commission_percent,lessons_count,
                    gross_amount_kop,commission_adjustment_kop,commission_amount_kop,
                    tutor_amount_kop,pdf_bytes,pdf_sha256,created_at
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                RETURNING *
                """,
                key, int(tutor_id), int(year), int(month), int(period_no), start, end,
                mode, applied_percent, len(items), gross_total, adjustment,
                commission_total, tutor_total, pdf, hashlib.sha256(pdf).hexdigest(), created,
            )
            for item in items:
                await conn.execute(
                    """
                    INSERT INTO agent_report_items(
                        report_id,booking_id,lesson_date,time_slot,student_name,subject,outcome,
                        gross_amount_kop,commission_percent,commission_amount_kop,tutor_amount_kop
                    ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    """,
                    int(report["id"]), item["booking_id"], item["lesson_date"],
                    item["time_slot"], item["student_name"], item["subject"], item["outcome"],
                    item["gross_kop"], item["commission_percent"], item["commission_kop"],
                    item["tutor_kop"],
                )
            return dict(report), items, True


def summary_text(report: dict, tutor_name: str) -> str:
    def rub(kop):
        return f"{int(kop or 0) / 100:,.2f}".replace(",", " ")
    lines = [
        f"📄 Отчёт агента {report['report_key']}",
        f"👨‍🏫 {tutor_name}",
        f"📅 {report['period_start']:%d.%m.%Y}-{report['period_end']:%d.%m.%Y}",
        f"Занятий: {report['lessons_count']}",
        f"Стоимость услуг: {rub(report['gross_amount_kop'])} руб.",
        f"Вознаграждение агента: {rub(report['commission_amount_kop'])} руб.",
        f"К перечислению: {rub(report['tutor_amount_kop'])} руб.",
    ]
    adjustment = int(report.get("commission_adjustment_kop") or 0)
    if adjustment:
        lines.append(
            "Корректировка комиссии за 1-15: "
            + ("+" if adjustment > 0 else "-")
            + f"{rub(abs(adjustment))} руб."
        )
    return "\n".join(lines)


async def send_report(bot, report: dict) -> dict:
    tutors = await db.get_all_tutors()
    tutor = tutors.get(int(report["tutor_id"]))
    if not tutor:
        raise ValueError("tutor_not_found")
    if not tutor.get("telegram_id"):
        raise ValueError("tutor_has_no_telegram_id")
    channel_id = reports_channel_id()
    if channel_id is None:
        raise ValueError("reports_channel_not_configured")

    await ensure_schema()
    # Serialize clicks across both bot processes and always reload delivery flags.
    # A session lock lets each successful recipient be committed independently;
    # a failed tutor delivery must not roll back the successful archive delivery.
    async with db._legacy.pool.acquire() as conn:
        lock_key = f"agent-report-delivery:{int(report['id'])}"
        await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", lock_key)
        try:
            fresh = await conn.fetchrow("SELECT * FROM agent_reports WHERE id=$1", int(report["id"]))
            if not fresh:
                raise ValueError("report_not_found")
            current = dict(fresh)
            data = bytes(current["pdf_bytes"])
            if hashlib.sha256(data).hexdigest() != current["pdf_sha256"]:
                raise ValueError("report_checksum_mismatch")
            filename = f"agent_report_{current['report_key']}.pdf"
            caption = summary_text(current, str(tutor.get("name") or "Репетитор"))
            for column, recipient in (("archive_sent_at", channel_id),
                                      ("tutor_sent_at", tutor["telegram_id"])):
                if current.get(column):
                    continue
                await bot.send_document(
                    int(recipient), BufferedInputFile(data, filename=filename),
                    caption=caption, parse_mode=None,
                )
                await conn.execute(
                    f"UPDATE agent_reports SET {column}=NOW() WHERE id=$1", int(current["id"])
                )
                current[column] = True
            return {"archive_sent": bool(current["archive_sent_at"]),
                    "tutor_sent": bool(current["tutor_sent_at"])}
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", lock_key)
