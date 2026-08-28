import json
import logging

import messaging_legacy as _legacy
from messaging_legacy import *


async def edit_telegram_message(chat_id: int, message_id: int, text: str, reply_markup=None) -> bool:
    """Редактирует существующую карточку занятия вместо удаления/создания новой."""
    if not _legacy.TELEGRAM_BOT_TOKEN or not chat_id or not message_id:
        return False
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        if isinstance(reply_markup, str):
            try:
                reply_markup = json.loads(reply_markup)
            except json.JSONDecodeError:
                reply_markup = None
        if reply_markup:
            payload["reply_markup"] = _legacy._clean_none(reply_markup)
    result = await _legacy._post_json(
        f"https://api.telegram.org/bot{_legacy.TELEGRAM_BOT_TOKEN}/editMessageText",
        payload,
    )
    if result and result.get("ok"):
        return True
    description = (result or {}).get("description", "")
    if "message is not modified" in description.lower():
        return True
    logging.warning("Telegram API editMessageText failed: %s", result)
    return False
