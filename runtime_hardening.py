"""Low-risk runtime guards shared by Telegram and VK entrypoints.

This module intentionally does not change user-facing booking/payment flows. It only
serializes operations that must be unique across the two bot processes and disables
legacy VK admin callbacks that are not part of the supported VK admin surface.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import database as _db

MSK = ZoneInfo("Europe/Moscow")
_JOB_SCHEMA_READY = False


async def _ensure_job_claim_schema() -> None:
    global _JOB_SCHEMA_READY
    if _JOB_SCHEMA_READY:
        return
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_job_claims (
                job_name TEXT NOT NULL,
                bucket TEXT NOT NULL,
                claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (job_name, bucket)
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_job_claims_claimed_at
                ON runtime_job_claims(claimed_at);
            DELETE FROM runtime_job_claims
                WHERE claimed_at < NOW() - INTERVAL '14 days';
            """
        )
    _JOB_SCHEMA_READY = True


async def _claim_job_once(job_name: str, bucket: str) -> bool:
    """Return True for exactly one process for a logical time bucket."""
    await _ensure_job_claim_schema()
    async with _db._legacy.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO runtime_job_claims(job_name, bucket)
            VALUES($1, $2)
            ON CONFLICT(job_name, bucket) DO NOTHING
            RETURNING job_name
            """,
            job_name,
            bucket,
        )
    return row is not None


@asynccontextmanager
async def _distributed_lock(name: str):
    """Hold a PostgreSQL advisory transaction lock while the wrapped operation runs."""
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                str(name),
            )
            yield


def _install_payment_guard(legacy, platform: str) -> None:
    if getattr(legacy, "_distributed_payment_guard_installed", False):
        return

    original = legacy.create_and_send_payment

    if platform == "telegram":
        async def guarded(source, bot, booking, email, booking_id):
            async with _distributed_lock(f"booking-payment:{int(booking_id)}"):
                return await original(source, bot, booking, email, booking_id)
    elif platform == "vk":
        async def guarded(source, booking, email, booking_id):
            async with _distributed_lock(f"booking-payment:{int(booking_id)}"):
                return await original(source, booking, email, booking_id)
    else:
        raise ValueError("unsupported platform")

    legacy.create_and_send_payment = guarded
    legacy._distributed_payment_guard_installed = True


def _install_background_job_guards(legacy) -> None:
    """Prevent Telegram and VK processes from running the same notification job twice."""
    if getattr(legacy, "_background_job_guards_installed", False):
        return

    if hasattr(legacy, "send_reminders"):
        original_send_reminders = legacy.send_reminders

        async def guarded_send_reminders(*args, **kwargs):
            bucket = datetime.now(MSK).strftime("%Y%m%d%H%M")
            if not await _claim_job_once("lesson-reminders", bucket):
                return None
            return await original_send_reminders(*args, **kwargs)

        legacy.send_reminders = guarded_send_reminders

    if hasattr(legacy, "send_pending_reminders"):
        original_send_pending = legacy.send_pending_reminders

        async def guarded_send_pending(*args, **kwargs):
            # The legacy loops call this only at the scheduled 09/15/21 MSK hours.
            bucket = datetime.now(MSK).strftime("%Y%m%d%H")
            if not await _claim_job_once("pending-booking-reminders", bucket):
                return None
            return await original_send_pending(*args, **kwargs)

        legacy.send_pending_reminders = guarded_send_pending

    legacy._background_job_guards_installed = True


def _install_booking_record_guard() -> None:
    import booking_records

    if getattr(booking_records, "_distributed_sync_guard_installed", False):
        return
    original_sync = booking_records.sync_booking_record

    async def guarded_sync(booking_id: int) -> bool:
        async with _distributed_lock(f"booking-record:{int(booking_id)}"):
            return await original_sync(booking_id)

    booking_records.sync_booking_record = guarded_sync
    booking_records._distributed_sync_guard_installed = True


def _install_vk_stats_only_admin_guard(legacy) -> None:
    """VK admin UI is intentionally statistics-only; deny stale legacy callbacks too."""
    if getattr(legacy, "_vk_stats_only_admin_guard_installed", False):
        return

    async def stats_only(event):
        try:
            await legacy.answer_event(
                event,
                "В VK админ-панели доступна только статистика. Остальные действия выполняются в Telegram.",
                snackbar=True,
            )
        except Exception:
            logging.exception("Не удалось показать VK stats-only admin guard")

    blocked_callbacks = (
        "admin_add_start",
        "admin_edit_list",
        "edit_tutor_choice",
        "edit_field_choice",
        "manage_subjects",
        "back_to_edit_tutor",
        "add_subject_start",
        "edit_subject_menu",
        "edit_subject_name_start",
        "edit_subject_price_start",
        "delete_subject_confirm",
        "confirm_delete_subject",
        "back_to_subjects_list",
        "toggle_commission_mode",
        "admin_delete_list",
        "delete_tutor_confirm",
        "confirm_delete",
        "add_another_subject",
        "finish_adding_subjects",
    )
    for name in blocked_callbacks:
        if hasattr(legacy, name):
            setattr(legacy, name, stats_only)

    legacy._vk_stats_only_admin_guard_installed = True


def install_telegram_hardening(app) -> None:
    legacy = app.legacy
    _install_payment_guard(legacy, "telegram")
    _install_background_job_guards(legacy)
    _install_booking_record_guard()


def install_vk_hardening(app) -> None:
    legacy = app.legacy
    _install_payment_guard(legacy, "vk")
    _install_background_job_guards(legacy)
    _install_booking_record_guard()
    _install_vk_stats_only_admin_guard(legacy)
