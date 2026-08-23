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


async def send_telegram_message(chat_id: int, text: str, reply_markup: str = None):
    """Отправка сообщения в Telegram через HTTP API. reply_markup - JSON-строка клавиатуры."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
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
async def send_telegram_message_get_id(chat_id: int, text: str, reply_markup: str = None):
    """
    Отправляет сообщение в Telegram и возвращает (success, message_id).
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

async def send_vk_message(user_id: int, text: str, keyboard: str = None):
    """Отправка сообщения в VK через VK API. keyboard - JSON-строка клавиатуры."""
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


async def send_to_user(user_id: int, platform: str, text: str, reply_markup_tg: str = None, keyboard_vk: str = None):
    """Отправка сообщения пользователю в зависимости от платформы."""
    if platform == 'telegram':
        return await send_telegram_message(user_id, text, reply_markup_tg)
    elif platform == 'vk':
        return await send_vk_message(user_id, text, keyboard_vk)
    else:
        logging.error(f"Unknown platform: {platform}")
        return False


async def send_to_tutor(tutor_id: int, text: str, reply_markup_tg: str = None, keyboard_vk: str = None, db_get_tutors_func=None):
    """
    Отправка уведомления преподавателю в оба мессенджера, если у него есть соответствующие ID.
    reply_markup_tg - JSON-строка клавиатуры для Telegram.
    keyboard_vk - JSON-строка клавиатуры для VK.
    """
    if db_get_tutors_func is None:
        from database import get_all_tutors as db_get_tutors_func
    tutors = await db_get_tutors_func()
    tutor = tutors.get(tutor_id)
    if not tutor:
        return

    # Отправка в Telegram
    if tutor.get("telegram_id"):
        await send_telegram_message(tutor["telegram_id"], text, reply_markup_tg)

    # Отправка в VK
    if tutor.get("vk_id"):
        await send_vk_message(tutor["vk_id"], text, keyboard_vk)
