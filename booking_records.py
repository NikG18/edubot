import html
import importlib
import json
import logging
import os
import sys
from zoneinfo import ZoneInfo

# Bot_test.py подменяет code object уже зарегистрированных aiogram handlers.
# Такие функции продолжают использовать globals модуля Bot_test_legacy.
# Если этот модуль уже загружен (Telegram-процесс), явно прокидываем туда
# compatibility-ссылки. В VK-процессе Bot_test_legacy не импортирован, поэтому
# этот блок ничего не делает и не создаёт лишних зависимостей.
_bot_legacy = sys.modules.get("Bot_test_legacy")
if _bot_legacy is not None:
    _bot_legacy.__dict__["legacy"] = _bot_legacy
    _bot_legacy.__dict__["_db"] = importlib.import_module("database")

from database import get_all_tutors, get_booking, get_booking_events, update_booking
from messaging import edit_telegram_message, send_telegram_message_get_id

MSK = ZoneInfo("Europe/Moscow")
RECORDS_CHANNEL_ID = os.environ.get("RECORDS_CHANNEL_ID") or ""
MAX_HISTORY_EVENTS = 15
MAX_EVENT_REASON_LENGTH = 240
MAX_RECORD_LENGTH = 3900

STATUS_HEADERS = {
    "pending": "🟠 ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ",
    "confirmed": "🟡 ПОДТВЕРЖДЕНО — ОЖИДАЕТ ОПЛАТЫ",
    "paid": "🟢 ЗАНЯТИЕ ОПЛАЧЕНО",
    "completed": "✅ ЗАНЯТИЕ ПРОВЕДЕНО",
    "cancelled": "🔴 ЗАНЯТИЕ ОТМЕНЕНО",
}

REFUND_LABELS = {
    "none": "не требуется",
    "not_required": "не требуется",
    "required": "требуется возврат",
    "pending": "возврат обрабатывается",
    "refunded": "возвращено",
    "failed": "ошибка возврата",
}


def format_dt(dt) -> str:
    if not dt:
        return "—"
    try:
        dt = dt.astimezone(MSK)
    except Exception:
        pass
    return dt.strftime("%d.%m.%Y %H:%M")


def _short(value, limit=MAX_EVENT_REASON_LENGTH) -> str:
    text = str(value or "")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return html.escape(text)


def _event_details(event: dict) -> dict:
    details = event.get("details")
    if isinstance(details, dict):
        return details
    if isinstance(details, str):
        try:
            parsed = json.loads(details)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            logging.warning("Некорректный JSON в booking_events.details: %r", details[:200])
            return {}
    return {}


def render_event(event: dict) -> str:
    event_type = event.get("event_type") or "unknown"
    actor = event.get("actor_type") or "system"
    details = _event_details(event)
    labels = {
        "created": "📝 Заявка создана",
        "confirmed": "✅ Подтверждено преподавателем",
        "paid": "💳 Оплата получена",
        "completed": "🎓 Занятие проведено",
        "rejected": "❌ Заявка отклонена преподавателем",
        "payment_failed": "❌ Платёж не прошёл",
        "refund_pending": "↩️ Требуется возврат средств",
        "refunded": "↩️ Возврат средств выполнен",
        "late_payment_after_cancel": "⚠️ Оплата поступила после отмены — требуется возврат",
        "legacy_import": "📦 Импортировано из старой системы",
    }
    if event_type == "cancelled":
        who = {
            "student": "учеником",
            "tutor": "преподавателем",
            "admin": "администратором",
            "payment": "платёжной системой",
            "system": "системой",
        }.get(actor, actor)
        label = f"❌ Отменено {_short(who, 80)}"
        reason = details.get("reason")
        if reason:
            label += f" — {_short(reason)}"
    elif event_type == "rescheduled":
        who = {
            "student": "учеником",
            "tutor": "преподавателем",
            "admin": "администратором",
            "system": "системой",
        }.get(actor, actor)
        label = (
            f"🔄 Перенесено {_short(who, 80)}: "
            f"{_short(details.get('old_date', '?'), 40)} "
            f"{_short(details.get('old_time', '?'), 40)} → "
            f"{_short(details.get('new_date', '?'), 40)} "
            f"{_short(details.get('new_time', '?'), 40)}"
        )
    else:
        label = labels.get(event_type, _short(event_type, 120))
    return f"{label}\n   🕒 {format_dt(event.get('created_at'))}"


async def render_booking_record(booking_id: int):
    booking = await get_booking(booking_id)
    if not booking:
        return None
    tutors = await get_all_tutors()
    tutor_name = tutors.get(booking["tutor_id"], {}).get("name", f"#{booking['tutor_id']}")
    header = STATUS_HEADERS.get(booking["status"], booking["status"])
    if booking["status"] == "cancelled":
        header = {
            "student": "🔴 ЗАНЯТИЕ ОТМЕНЕНО УЧЕНИКОМ",
            "tutor": "🔴 ЗАНЯТИЕ ОТМЕНЕНО ПРЕПОДАВАТЕЛЕМ",
            "admin": "🔴 ЗАНЯТИЕ ОТМЕНЕНО АДМИНИСТРАТОРОМ",
            "payment": "🔴 ЗАНЯТИЕ ОТМЕНЕНО — ОПЛАТА НЕ ПРОШЛА",
        }.get(booking.get("cancelled_by"), header)
    platform = {
        "telegram": "Telegram",
        "vk": "VK",
    }.get(booking.get("user_platform"), booking.get("user_platform") or "—")
    amount = int(booking.get("amount") or 0)
    amount_text = f"{amount / 100:.2f} ₽" if amount else "—"
    refund_status = booking.get("refund_status") or "none"
    refund_text = REFUND_LABELS.get(refund_status, refund_status)
    events = await get_booking_events(booking_id)
    hidden_count = max(0, len(events) - MAX_HISTORY_EVENTS)
    visible_events = events[-MAX_HISTORY_EVENTS:]
    history_parts = []
    if hidden_count:
        history_parts.append(f"… ещё событий: {hidden_count}")
    history_parts.extend(render_event(event) for event in visible_events)
    history = "\n\n".join(history_parts) or "Нет событий"
    text = (
        f"<b>{header}</b>\n\n"
        f"🆔 <b>Занятие #{booking_id}</b>\n"
        f"👤 Ученик: {_short(booking['username'], 160)}\n"
        f"🆔 ID ученика: <code>{booking['user_id']}</code>\n"
        f"🌐 Платформа: {_short(platform, 80)}\n\n"
        f"👨‍🏫 Преподаватель: {_short(tutor_name, 160)}\n"
        f"📚 Предмет: {_short(booking['subject'], 160)}\n"
        f"📅 Дата: {_short(booking['date'], 40)}\n"
        f"🕒 Время: {_short(booking['time_slot'], 80)} МСК\n\n"
        f"💳 Стоимость: {amount_text}\n"
        f"↩️ Возврат: {_short(refund_text, 100)}\n"
        f"🕘 Последнее изменение: {format_dt(booking.get('updated_at'))}\n\n"
        f"<b>История:</b>\n{history}"
    )
    if len(text) > MAX_RECORD_LENGTH:
        safe_history_budget = max(300, MAX_RECORD_LENGTH - (len(text) - len(history)) - 80)
        history = history[-safe_history_budget:]
        text = (
            f"<b>{header}</b>\n\n"
            f"🆔 <b>Занятие #{booking_id}</b>\n"
            f"👤 Ученик: {_short(booking['username'], 160)}\n"
            f"👨‍🏫 Преподаватель: {_short(tutor_name, 160)}\n"
            f"📚 Предмет: {_short(booking['subject'], 160)}\n"
            f"📅 {_short(booking['date'], 40)} · 🕒 {_short(booking['time_slot'], 80)} МСК\n"
            f"🌐 {_short(platform, 80)} · 💳 {amount_text}\n"
            f"↩️ {_short(refund_text, 100)}\n"
            f"🕘 {format_dt(booking.get('updated_at'))}\n\n"
            f"<b>Последние события:</b>\n…\n{history}"
        )
    return text


async def sync_booking_record(booking_id: int) -> bool:
    """Одна booking = одна карточка records_channel. Карточка никогда не удаляется."""
    if not RECORDS_CHANNEL_ID:
        logging.warning("RECORDS_CHANNEL_ID не задан: карточка booking %s не синхронизирована", booking_id)
        return False
    booking = await get_booking(booking_id)
    if not booking:
        return False
    text = await render_booking_record(booking_id)
    if not text:
        return False
    channel_msg_id = booking.get("channel_msg_id")
    if channel_msg_id:
        ok = await edit_telegram_message(RECORDS_CHANNEL_ID, channel_msg_id, text)
        if ok:
            return True
        logging.warning(
            "Не удалось обновить карточку booking=%s message=%s; новое сообщение автоматически не создаётся",
            booking_id,
            channel_msg_id,
        )
        return False
    ok, message_id = await send_telegram_message_get_id(RECORDS_CHANNEL_ID, text)
    if not ok or not message_id:
        return False
    await update_booking(booking_id, channel_msg_id=message_id)
    return True
