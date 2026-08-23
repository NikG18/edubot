import os
import json
import aiohttp
import logging
import random

TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN")  # токен телеграм-бота
VK_BOT_TOKEN = os.environ.get("VK_BOT_TOKEN")      # токен VK-бота
VK_API_VERSION = "5.131"

if not TELEGRAM_BOT_TOKEN:
    logging.warning("BOT_TOKEN не задан, отправка в Telegram невозможна")
if not VK_BOT_TOKEN:
    logging.warning("VK_BOT_TOKEN не задан, отправка в VK невозможна")

def _clean_none(obj):
    """Рекурсивно удаляет ключи со значением None."""
    if isinstance(obj, dict):
        return {k: _clean_none(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [_clean_none(item) for item in obj]
    return obj
async def send_telegram_message(chat_id: int, text: str, reply_markup=None):
    """Отправка сообщения в Telegram через HTTP API. reply_markup - JSON-строка или dict."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        if isinstance(reply_markup, str):
            try:
                reply_markup = _clean_none(json.loads(reply_markup))
            except Exception as e:
                logging.warning(f"Не удалось разобрать JSON reply_markup: {e}")
                reply_markup = None
        else:
            reply_markup = _clean_none(reply_markup)
        if reply_markup:
            payload["reply_markup"] = reply_markup
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logging.warning(f"TG send failed: {await resp.text()}")
                    return False
                return True
    except Exception as e:
        logging.error(f"TG send error: {e}")
        return False

async def send_telegram_message_get_id(chat_id: int, text: str, reply_markup=None):
    """
    Отправляет сообщение в Telegram и возвращает (success, message_id).
    reply_markup - JSON-строка или dict.
    """
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False, None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        if isinstance(reply_markup, str):
            try:
                reply_markup = _clean_none(json.loads(reply_markup))
            except Exception as e:
                logging.warning(f"Не удалось разобрать JSON reply_markup: {e}")
                reply_markup = None
        else:
            reply_markup = _clean_none(reply_markup)
        if reply_markup:
            payload["reply_markup"] = reply_markup
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("ok"):
                    return True, data["result"]["message_id"]
                else:
                    logging.warning(f"TG send failed: {data}")
                    return False, None
    except Exception as e:
        logging.error(f"TG send error: {e}")
        return False, None


async def send_vk_message(user_id: int, text: str, keyboard=None):
    """Отправка сообщения в VK через VK API."""
    if not VK_BOT_TOKEN or not user_id:
        return False
    url = "https://api.vk.com/method/messages.send"
    params = {
        "user_id": user_id,
        "message": text,
        "random_id": random.randint(1, 2**31 - 1),
        "access_token": VK_BOT_TOKEN,
        "v": VK_API_VERSION,
    }
    if keyboard:
        params["keyboard"] = keyboard
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=params) as resp:
                data = await resp.json()
                if "error" in data:
                    logging.warning(f"VK send failed: {data['error']}")
                    return False
                return True
    except Exception as e:
        logging.error(f"VK send error: {e}")
        return False


async def send_to_user(user_id: int, platform: str, text: str, reply_markup_tg=None, keyboard_vk=None):
    if platform == 'telegram':
        return await send_telegram_message(user_id, text, reply_markup_tg)
    elif platform == 'vk':
        return await send_vk_message(user_id, text, keyboard_vk)
    else:
        logging.error(f"Unknown platform: {platform}")
        return False


async def send_to_tutor(tutor_id: int, text: str, reply_markup_tg=None, keyboard_vk=None, db_get_tutors_func=None):
    if db_get_tutors_func is None:
        from database import get_all_tutors as db_get_tutors_func
    tutors = await db_get_tutors_func()
    tutor = tutors.get(tutor_id)
    if not tutor:
        return
    if tutor.get("telegram_id"):
        await send_telegram_message(tutor["telegram_id"], text, reply_markup_tg)
    if tutor.get("vk_id"):
        await send_vk_message(tutor["vk_id"], text, keyboard_vk)
