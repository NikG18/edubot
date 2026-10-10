"""Paginate VK inline keyboards at the final transport boundary.

Run after all booking/payment keyboard rewrites. Original actions and payloads
are stored unchanged, so existing authorization and FSM handlers still apply.
Page snapshots live in PostgreSQL, shared by the Telegram and VK processes.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import secrets

from vkbottle import API

PAGE_COMMAND = "keyboard_page"
_schema_ready = False
_schema_lock = asyncio.Lock()


def _payload(button):
    value = button.get("action", {}).get("payload", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def split_keyboard(keyboard):
    """Return complete pages; reserve a row for navigation and keep Back visible."""
    if hasattr(keyboard, "get_json"):
        keyboard = keyboard.get_json()
    data = json.loads(keyboard) if isinstance(keyboard, str) else deepcopy(keyboard)
    if not data.get("inline"):
        return [data]
    rows = [row for row in data.get("buttons", []) if row]
    if len(rows) <= 6 and sum(map(len, rows)) <= 10 and all(len(r) <= 5 for r in rows):
        return [data]

    # Three per row also leaves enough width for links and long slot labels.
    rows = [row[i:i + 3] for row in rows for i in range(0, len(row), 3)]
    footer = []
    if rows and len(rows[-1]) == 1:
        button = rows[-1][0]
        label = str(button.get("action", {}).get("label", ""))
        command = str(_payload(button).get("cmd", ""))
        if "назад" in label.casefold() or label.startswith("🔙") or command.startswith("back_"):
            footer = rows.pop()
    # At most ten buttons and six rows INCLUDING page controls and Back.
    row_limit = 4 if footer else 5
    button_limit = 7 if footer else 8
    pages, current, count = [], [], 0
    for row in rows:
        if current and (len(current) == row_limit or count + len(row) > button_limit):
            pages.append(current)
            current, count = [], 0
        current.append(row)
        count += len(row)
    if current or not pages:
        pages.append(current)
    return [dict(data, buttons=page + ([footer] if footer else []), one_time=False) for page in pages]


def page_keyboard(pages, token, page):
    if not 0 <= page < len(pages):
        raise ValueError("Invalid keyboard page")
    data = deepcopy(pages[page])
    if len(pages) > 1:
        controls = []
        for index, label in ((page - 1, "◀ Назад"), (page + 1, "Далее ▶")):
            if 0 <= index < len(pages):
                controls.append({"action": {
                    "type": "callback", "label": f"{label} {index + 1}/{len(pages)}",
                    "payload": {"cmd": PAGE_COMMAND, "token": token, "page": index},
                }, "color": "secondary"})
        data["buttons"].append(controls)
    return json.dumps(data, ensure_ascii=False)


async def _ensure_schema():
    global _schema_ready
    import database as db
    await db._ensure_pool()
    if not _schema_ready:
        async with _schema_lock:
            if not _schema_ready:
                async with db._legacy.pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('vk-keyboard-pages-schema', 0))")
                        await conn.execute("""CREATE TABLE IF NOT EXISTS vk_keyboard_pages (
                            token TEXT PRIMARY KEY, peer_id BIGINT NOT NULL,
                            snapshot JSONB NOT NULL,
                            expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '7 days'
                        )""")
                        await conn.execute("CREATE INDEX IF NOT EXISTS vk_keyboard_pages_expiry ON vk_keyboard_pages(expires_at)")
                _schema_ready = True
    return db._legacy.pool


async def save_pages(peer_id, snapshot):
    pool = await _ensure_schema()
    token = secrets.token_hex(16)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM vk_keyboard_pages WHERE expires_at < NOW()")
            await conn.execute(
                "INSERT INTO vk_keyboard_pages(token, peer_id, snapshot) VALUES($1,$2,$3::jsonb)",
                token, int(peer_id), json.dumps(snapshot, ensure_ascii=False),
            )
    return token


async def load_pages(token, peer_id):
    pool = await _ensure_schema()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT snapshot FROM vk_keyboard_pages WHERE token=$1 AND peer_id=$2 AND expires_at > NOW()",
            token, int(peer_id),
        )
    return json.loads(value) if isinstance(value, str) else value


async def prepare_message(data):
    """Preserve the caller's data; only replace an oversized inline keyboard."""
    if not data.get("keyboard"):
        return data
    pages = split_keyboard(data["keyboard"])
    if len(pages) == 1:
        return dict(data, keyboard=json.dumps(pages[0], ensure_ascii=False))
    peer_id = data.get("peer_id") or data.get("user_id")
    if not peer_id:
        raise ValueError("Paginated VK keyboards require an individual peer_id or user_id")
    snapshot = {"pages": pages, "message": data.get("message") or "Выберите действие:"}
    if data.get("attachment"):
        snapshot["attachment"] = data["attachment"]
    token = await save_pages(peer_id, snapshot)
    return dict(data, keyboard=page_keyboard(pages, token, 0))


class PaginatedAPI(API):
    async def request(self, method, data, version=None):
        if method in {"messages.send", "messages.edit"}:
            data = await prepare_message(data)
        return await super().request(method, data, version)


async def show_page(legacy, event):
    payload = event.payload or {}
    try:
        page = int(payload.get("page", -1))
        token = payload.get("token")
        if not isinstance(token, str) or len(token) != 32 or event.peer_id != event.user_id:
            raise ValueError("Invalid page request")
        snapshot = await load_pages(token, event.peer_id)
        if not snapshot:
            raise ValueError("Expired page request")
        keyboard = page_keyboard(snapshot["pages"], token, page)
    except (ValueError, TypeError):
        await legacy.answer_event(event, "Список устарел. Откройте раздел заново.", snackbar=True)
        return
    data = dict(peer_id=event.peer_id, conversation_message_id=event.conversation_message_id,
                message=snapshot["message"], keyboard=keyboard)
    if snapshot.get("attachment"):
        data["attachment"] = snapshot["attachment"]
    # Bypass flow-specific editing wrappers: payloads were already rewritten
    # before this snapshot was saved. Paging must not clear or alter the FSM.
    await legacy.bot.api.messages.edit(**data)
