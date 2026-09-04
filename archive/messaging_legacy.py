import os
import json
import aiohttp
import logging
import random
import asyncio
from typing import Optional, Tuple

TIMEOUT = aiohttp.ClientTimeout(total=20, connect=5)
TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN")
VK_BOT_TOKEN = os.environ.get("VK_BOT_TOKEN")
VK_API_VERSION = "5.199"

_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    global _session
    async with _session_lock:
        if _session is None or _session.closed:
            _session = aiohttp.ClientSession(timeout=TIMEOUT)
        return _session


async def close_messaging():
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _clean_none(obj):
    if isinstance(obj, dict):
        return {k: _clean_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_clean_none(v) for v in obj]
    return obj


def _normalize_telegram_reply_markup(reply_markup):
    """Приводит aiogram/Pydantic/JSON-клавиатуру к обычному dict Telegram API."""
    if not reply_markup:
        return None
    if isinstance(reply_markup, str):
        try:
            reply_markup = json.loads(reply_markup)
        except json.JSONDecodeError:
            logging.warning("Некорректный Telegram reply_markup JSON")
            return None
    if hasattr(reply_markup, "model_dump"):
        try:
            reply_markup = reply_markup.model_dump(exclude_none=True)
        except TypeError:
            reply_markup = reply_markup.model_dump()
    elif hasattr(reply_markup, "dict") and not isinstance(reply_markup, dict):
        try:
            reply_markup = reply_markup.dict(exclude_none=True)
        except TypeError:
            reply_markup = reply_markup.dict()
    if not isinstance(reply_markup, dict):
        logging.warning("Неподдерживаемый тип Telegram reply_markup: %s", type(reply_markup).__name__)
        return None
    return _clean_none(reply_markup)


def _normalize_vk_keyboard(keyboard):
    """VK API ожидает JSON-строку клавиатуры; принимает vkbottle Keyboard/dict/str."""
    if not keyboard:
        return None
    if isinstance(keyboard, str):
        return keyboard
    if hasattr(keyboard, "get_json"):
        try:
            return keyboard.get_json()
        except Exception:
            logging.exception("Не удалось сериализовать VK Keyboard через get_json()")
            return None
    if isinstance(keyboard, dict):
        return json.dumps(_clean_none(keyboard), ensure_ascii=False)
    logging.warning("Неподдерживаемый тип VK keyboard: %s", type(keyboard).__name__)
    return None


async def _post_json(url, payload, retries=3):
    session = await _get_session()
    for attempt in range(retries):
        try:
            async with session.post(url, json=payload) as resp:
                text = await resp.text()
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                if resp.status == 200:
                    if parsed is not None:
                        return parsed
                    logging.error("Non-JSON response: %s", text[:500])
                    return None
                if resp.status in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if parsed is not None:
                    return parsed
                logging.warning("HTTP send failed: status=%s body=%s", resp.status, text[:500])
                return None
        except (aiohttp.ClientError, TimeoutError) as exc:
            if attempt == retries - 1:
                logging.error("Send failed after %s attempts: %s", retries, exc)
                return None
            await asyncio.sleep(2 ** attempt)


def _telegram_parse_error(result) -> bool:
    if not isinstance(result, dict) or result.get("ok") is not False:
        return False
    description = str(result.get("description") or "").lower()
    return any(marker in description for marker in (
        "can't parse entities", "can't find end tag", "unsupported start tag",
        "unexpected end tag", "entity", "parse",
    ))


async def send_telegram_message(chat_id: int, text: str, reply_markup=None) -> bool:
    ok, _ = await send_telegram_message_get_id(chat_id, text, reply_markup)
    return ok


async def send_telegram_message_get_id(chat_id: int, text: str, reply_markup=None) -> Tuple[bool, Optional[int]]:
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False, None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": str(text or ""), "parse_mode": "HTML"}
    normalized_markup = _normalize_telegram_reply_markup(reply_markup)
    if normalized_markup:
        payload["reply_markup"] = normalized_markup

    result = await _post_json(url, payload)
    if result and result.get("ok") and result.get("result"):
        return True, result["result"].get("message_id")

    # User-generated messages can contain raw <, > or &. Keep existing HTML behavior
    # for bot-generated text, but if Telegram rejects entity parsing, retry exactly
    # once without parse_mode instead of losing the message.
    if _telegram_parse_error(result):
        plain_payload = dict(payload)
        plain_payload.pop("parse_mode", None)
        plain_result = await _post_json(url, plain_payload)
        if plain_result and plain_result.get("ok") and plain_result.get("result"):
            return True, plain_result["result"].get("message_id")
        result = plain_result

    logging.warning("Telegram API sendMessage failed: %s", result)
    return False, None


async def send_vk_message(user_id: int, text: str, keyboard=None) -> bool:
    if not VK_BOT_TOKEN or not user_id:
        return False
    session = await _get_session()
    params = {
        "user_id": user_id,
        "message": text,
        "random_id": random.randint(1, 2**31 - 1),
        "access_token": VK_BOT_TOKEN,
        "v": VK_API_VERSION,
    }
    normalized_keyboard = _normalize_vk_keyboard(keyboard)
    if normalized_keyboard:
        params["keyboard"] = normalized_keyboard
    try:
        async with session.post("https://api.vk.com/method/messages.send", data=params) as resp:
            data = await resp.json(content_type=None)
            if "error" in data:
                logging.warning("VK send failed: %s", data["error"])
                return False
            return "response" in data
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        logging.error("VK send error: %s", exc)
        return False


async def send_to_user(user_id: int, platform: str, text: str, reply_markup_tg=None, keyboard_vk=None):
    platform = (platform or "").lower()
    if platform == "telegram":
        return await send_telegram_message(user_id, text, reply_markup_tg)
    if platform == "vk":
        return await send_vk_message(user_id, text, keyboard_vk)
    logging.error("Unknown platform: %r", platform)
    return False


async def send_to_tutor(tutor_id: int, text: str, reply_markup_tg=None, keyboard_vk=None,
                        db_get_tutors_func=None):
    if db_get_tutors_func is None:
        from database import get_all_tutors as db_get_tutors_func
    tutors = await db_get_tutors_func()
    tutor = tutors.get(tutor_id)
    if not tutor:
        return False
    results = []
    if tutor.get("telegram_id"):
        results.append(await send_telegram_message(tutor["telegram_id"], text, reply_markup_tg))
    if tutor.get("vk_id"):
        results.append(await send_vk_message(tutor["vk_id"], text, keyboard_vk))
    return any(results) if results else False
