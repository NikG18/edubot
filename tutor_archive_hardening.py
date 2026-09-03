"""Archive tutors without deleting historical bookings, receipts or statistics."""

from __future__ import annotations

import database as _db

_SCHEMA_READY = False


async def ensure_tutor_archive_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE tutors ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;
            ALTER TABLE tutors ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
            """
        )
    _SCHEMA_READY = True


async def archive_tutor(tutor_id: int):
    await ensure_tutor_archive_schema()
    async with _db._legacy.pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE tutors
            SET active=FALSE, archived_at=COALESCE(archived_at,NOW())
            WHERE id=$1
            RETURNING id,name,active,archived_at
            """,
            int(tutor_id),
        )


async def _active_tutor_by_platform(platform: str, platform_id: int):
    await ensure_tutor_archive_schema()
    column = "telegram_id" if platform == "telegram" else "vk_id"
    async with _db._legacy.pool.acquire() as conn:
        return await conn.fetchval(
            f"SELECT id FROM tutors WHERE {column}=$1 AND active=TRUE",
            int(platform_id),
        )


async def _enrich_active(tutors: dict) -> dict:
    await ensure_tutor_archive_schema()
    if not tutors:
        return tutors
    async with _db._legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id,active,archived_at FROM tutors WHERE id=ANY($1::int[])",
            [int(tid) for tid in tutors],
        )
    meta = {int(row["id"]): row for row in rows}
    for tid, tutor in tutors.items():
        row = meta.get(int(tid))
        tutor["active"] = bool(row["active"]) if row else True
        tutor["archived_at"] = row["archived_at"] if row else None
    return tutors


def install_tutor_archive_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_tutor_archive_hardening_installed", False):
        return

    original_legacy_get_all = legacy.get_all_tutors
    original_keyboard = getattr(legacy, "make_tutors_keyboard", None)

    async def get_all_with_archive_status():
        return await _enrich_active(await original_legacy_get_all())

    async def active_tg(telegram_id: int):
        return await _active_tutor_by_platform("telegram", telegram_id)

    async def active_vk(vk_id: int):
        return await _active_tutor_by_platform("vk", vk_id)

    async def safe_archive(tutor_id: int):
        return await archive_tutor(tutor_id)

    legacy.get_all_tutors = get_all_with_archive_status
    legacy.delete_tutor = safe_archive
    _db.delete_tutor = safe_archive

    # A tutor loses role access after archive, while historical rows keep tutor_id.
    legacy.get_tutor_by_telegram_id = active_tg
    legacy.get_tutor_by_vk_id = active_vk

    if original_keyboard is not None:
        # Reimplement the two known helper shapes to filter only active tutors while
        # retaining each platform's native keyboard type.
        name = getattr(legacy, "__name__", "")
        if name.endswith("Bot_test_legacy"):
            async def active_keyboard(callback_prefix: str, back_callback: str = "back_to_menu"):
                tutors = await legacy.get_all_tutors()
                buttons = []
                for tid, tdata in tutors.items():
                    if not tdata.get("active", True):
                        continue
                    buttons.append([legacy.InlineKeyboardButton(
                        text=tdata["name"], callback_data=f"{callback_prefix}_{tid}"
                    )])
                buttons.append([legacy.InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)])
                return legacy.InlineKeyboardMarkup(inline_keyboard=buttons)
            legacy.make_tutors_keyboard = active_keyboard
        elif name.endswith("vk_bot_legacy"):
            async def active_keyboard(callback_prefix: str, back_callback: str = "back_to_menu"):
                tutors = await legacy.get_all_tutors()
                kb = legacy.Keyboard(inline=True)
                for tid, tdata in tutors.items():
                    if not tdata.get("active", True):
                        continue
                    kb.add(legacy.Callback(tdata["name"], payload={"cmd": f"{callback_prefix}_{tid}"}))
                    kb.row()
                kb.add(legacy.Callback("🔙 Назад", payload={"cmd": back_callback}))
                return kb.get_json()
            legacy.make_tutors_keyboard = active_keyboard

    legacy._tutor_archive_hardening_installed = True
