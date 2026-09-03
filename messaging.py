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
    normalized_markup = _legacy._normalize_telegram_reply_markup(reply_markup)
    if normalized_markup:
        payload["reply_markup"] = normalized_markup

    url = f"https://api.telegram.org/bot{_legacy.TELEGRAM_BOT_TOKEN}/editMessageText"
    result = await _legacy._post_json(url, payload)
    if result and result.get("ok"):
        return True
    description = str((result or {}).get("description", ""))
    if "message is not modified" in description.lower():
        return True

    # Same safety net as sendMessage: forwarded/user-generated text can contain raw
    # HTML metacharacters. Preserve normal bot formatting, but retry malformed HTML
    # once as plain text instead of losing the update.
    if _legacy._telegram_parse_error(result):
        plain_payload = dict(payload)
        plain_payload.pop("parse_mode", None)
        plain_result = await _legacy._post_json(url, plain_payload)
        if plain_result and plain_result.get("ok"):
            return True
        result = plain_result

    logging.warning("Telegram API editMessageText failed: %s", result)
    return False
