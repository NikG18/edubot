"""Platform-safe compatibility layer for legacy email/autopay helpers.

The legacy ``users`` and ``pending_email_requests`` tables use only a numeric
``user_id``. Telegram and VK IDs are independent namespaces, so equal numeric IDs
can collide. Email/autopay are student-profile properties, while a pending prompt
belongs to the concrete messenger account that initiated it.

Tutor confirmations are special: the tutor can press a Telegram button for a
booking created in VK (or vice versa). A ContextVar carries the booking's original
student platform through that async call, so legacy one-argument helpers remain
compatible without cross-coroutine global mutation.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from types import FunctionType

import database as _db

_schema_lock = asyncio.Lock()
_schema_ready = False
_account_platform_context: ContextVar[str | None] = ContextVar(
    "student_account_platform", default=None
)


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with _schema_lock:
        if _schema_ready:
            return
        await _db._ensure_pool()
        async with _db._legacy.pool.acquire() as conn:
            await conn.execute(
                """
                ALTER TABLE student_profiles
                    ADD COLUMN IF NOT EXISTS autopay_enabled BOOLEAN NOT NULL DEFAULT FALSE;

                CREATE TABLE IF NOT EXISTS pending_email_requests_v2 (
                    platform TEXT NOT NULL CHECK (platform IN ('telegram','vk')),
                    platform_user_id BIGINT NOT NULL,
                    booking_id INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY(platform, platform_user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_pending_email_requests_v2_created
                    ON pending_email_requests_v2(created_at);
                DELETE FROM pending_email_requests_v2
                    WHERE created_at < NOW() - INTERVAL '2 days';
                """
            )
        _schema_ready = True


def _effective_platform(default_platform: str) -> str:
    contextual = _account_platform_context.get()
    return contextual if contextual in {"telegram", "vk"} else default_platform


async def get_pending_email_request(platform: str, platform_user_id: int):
    await _ensure_schema()
    async with _db._legacy.pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT booking_id FROM pending_email_requests_v2 WHERE platform=$1 AND platform_user_id=$2",
            platform,
            int(platform_user_id),
        )


async def set_pending_email_request(platform: str, platform_user_id: int, booking_id: int):
    await _ensure_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pending_email_requests_v2(platform,platform_user_id,booking_id,created_at)
            VALUES($1,$2,$3,NOW())
            ON CONFLICT(platform,platform_user_id)
            DO UPDATE SET booking_id=EXCLUDED.booking_id, created_at=NOW()
            """,
            platform,
            int(platform_user_id),
            int(booking_id),
        )


async def delete_pending_email_request(platform: str, platform_user_id: int):
    await _ensure_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM pending_email_requests_v2 WHERE platform=$1 AND platform_user_id=$2",
            platform,
            int(platform_user_id),
        )


async def get_autopay(platform: str, platform_user_id: int) -> bool:
    await _ensure_schema()
    student_id = await _db.get_student_id(platform, int(platform_user_id), create=True)
    async with _db._legacy.pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT autopay_enabled FROM student_profiles WHERE id=$1",
            int(student_id),
        )
    return bool(value)


async def set_autopay(platform: str, platform_user_id: int, enabled: bool):
    await _ensure_schema()
    student_id = await _db.get_student_id(platform, int(platform_user_id), create=True)
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            "UPDATE student_profiles SET autopay_enabled=$1 WHERE id=$2",
            bool(enabled),
            int(student_id),
        )
    return bool(enabled)


def _clone_function(fn):
    return FunctionType(
        fn.__code__, fn.__globals__, name=fn.__name__,
        argdefs=fn.__defaults__, closure=fn.__closure__,
    )


def install_student_account_hardening(app, platform: str) -> None:
    legacy = app.legacy
    platform = str(platform).lower()
    if platform not in {"telegram", "vk"}:
        raise ValueError("platform must be telegram or vk")
    marker = f"_student_account_hardening_{platform}_installed"
    if getattr(legacy, marker, False):
        return

    async def get_user_email(user_id: int):
        selected = _effective_platform(platform)
        return await _db.get_student_email(selected, int(user_id))

    async def set_user_email(user_id: int, email: str):
        selected = _effective_platform(platform)
        return await _db.set_student_email(selected, int(user_id), email)

    async def set_pending(user_id: int, booking_id: int):
        selected = _effective_platform(platform)
        return await set_pending_email_request(selected, int(user_id), int(booking_id))

    async def get_pending(user_id: int):
        selected = _effective_platform(platform)
        return await get_pending_email_request(selected, int(user_id))

    async def delete_pending(user_id: int):
        selected = _effective_platform(platform)
        return await delete_pending_email_request(selected, int(user_id))

    async def is_autopay_enabled(user_id: int):
        selected = _effective_platform(platform)
        return await get_autopay(selected, int(user_id))

    async def set_autopay_enabled(user_id: int, enabled: bool):
        selected = _effective_platform(platform)
        return await set_autopay(selected, int(user_id), bool(enabled))

    replacements = {
        "get_user_email": get_user_email,
        "set_user_email": set_user_email,
        "set_pending_email_request": set_pending,
        "get_pending_email_request": get_pending,
        "delete_pending_email_request": delete_pending,
        "is_autopay_enabled": is_autopay_enabled,
        "set_autopay_enabled": set_autopay_enabled,
    }
    for name, func in replacements.items():
        setattr(legacy, name, func)
        if hasattr(app, name):
            setattr(app, name, func)

    setattr(legacy, marker, True)


def install_booking_account_context(app, platform: str) -> None:
    """Wrap the final tutor-confirm handler with the booking's student platform."""
    legacy = app.legacy
    platform = str(platform).lower()
    marker = f"_booking_account_context_{platform}_installed"
    if getattr(legacy, marker, False):
        return

    current = legacy.tutor_confirm_booking

    if platform == "telegram":
        # Aiogram already registered this exact function object. Clone its current
        # subscription-aware implementation, then replace only its code object.
        original = _clone_function(current)
        legacy._booking_platform_original_confirm = original
        legacy._booking_platform_context_var = _account_platform_context
        legacy._booking_platform_db = _db

        async def contextual_confirm(call, bot, state):
            booking = None
            try:
                bid = int(call.data.rsplit("_", 1)[1])
                booking = await _booking_platform_db.get_booking(bid)
            except (AttributeError, TypeError, ValueError, IndexError):
                pass
            selected = (
                str(booking.get("user_platform") or "telegram").lower()
                if booking else "telegram"
            )
            token = _booking_platform_context_var.set(selected)
            try:
                return await _booking_platform_original_confirm(call, bot, state)
            finally:
                _booking_platform_context_var.reset(token)

        if current.__code__.co_freevars or contextual_confirm.__code__.co_freevars:
            raise RuntimeError("Telegram tutor confirmation context patch cannot use closures")
        current.__code__ = contextual_confirm.__code__
    else:
        # VK's universal dispatcher resolves the module global dynamically, so a
        # normal async wrapper is sufficient even when the subscription wrapper has
        # closure variables.
        async def contextual_confirm(event):
            booking = None
            try:
                bid = int(event.payload["cmd"].rsplit("_", 1)[1])
                booking = await _db.get_booking(bid)
            except (KeyError, TypeError, ValueError, IndexError):
                pass
            selected = (
                str(booking.get("user_platform") or "vk").lower()
                if booking else "vk"
            )
            token = _account_platform_context.set(selected)
            try:
                return await current(event)
            finally:
                _account_platform_context.reset(token)

        legacy.tutor_confirm_booking = contextual_confirm
        if hasattr(app, "tutor_confirm_booking"):
            app.tutor_confirm_booking = contextual_confirm

    setattr(legacy, marker, True)
