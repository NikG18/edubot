"""Platform-safe compatibility layer for legacy email/autopay helpers.

The legacy ``users`` and ``pending_email_requests`` tables use only a numeric
``user_id``. Telegram and VK IDs are independent namespaces, so equal numeric IDs
can collide. Email/autopay are student-profile properties, while a pending prompt
belongs to the concrete messenger account that initiated it.
"""

from __future__ import annotations

import asyncio

import database as _db

_schema_lock = asyncio.Lock()
_schema_ready = False


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


def install_student_account_hardening(app, platform: str) -> None:
    legacy = app.legacy
    platform = str(platform).lower()
    if platform not in {"telegram", "vk"}:
        raise ValueError("platform must be telegram or vk")
    marker = f"_student_account_hardening_{platform}_installed"
    if getattr(legacy, marker, False):
        return

    async def get_user_email(user_id: int):
        return await _db.get_student_email(platform, int(user_id))

    async def set_user_email(user_id: int, email: str):
        return await _db.set_student_email(platform, int(user_id), email)

    async def set_pending(user_id: int, booking_id: int):
        return await set_pending_email_request(platform, int(user_id), int(booking_id))

    async def get_pending(user_id: int):
        return await get_pending_email_request(platform, int(user_id))

    async def delete_pending(user_id: int):
        return await delete_pending_email_request(platform, int(user_id))

    async def is_autopay_enabled(user_id: int):
        return await get_autopay(platform, int(user_id))

    async def set_autopay_enabled(user_id: int, enabled: bool):
        return await set_autopay(platform, int(user_id), bool(enabled))

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
