import html
import logging
import os
from zoneinfo import ZoneInfo

from database import get_all_tutors, get_booking, get_booking_events, update_booking
from messaging import edit_telegram_message, send_telegram_message_get_id

MSK = ZoneInfo("Europe/Moscow")
RECORDS_CHANNEL_ID = int(os.environ.get("RECORDS_CHANNEL_ID") or 0)
MAX_HISTORY_EVENTS = 25

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


def render_event(event: dict) -> str:
    event_type = event.get("event_type") or "unknown"
    actor = event.get("actor_type") or "system"
    details = event.get("details") or {}
    labels = {
        "created": "📝 Заявка создана",
        "confirmed": "✅ Подтверждено преподавателем",
        "paid": "💳 Оплата получена",
        "completed": "🎓 Занятие проведено",
        "rejected": "❌ Заявка отклонена преподавателем",
        "payment_failed": "❌ Платёж не прошёл",
        "refund_pending": "↩️ Требуется возврат средств",
        "refunded": "↩️ Возврат средств выполнен",
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
        label = f"❌ Отменено {who}"
        reason = details.get("reason")
        if reason:
            label += f" — {html.escape(str(reason))}"
    elif event_type == "rescheduled":
        who = {
            "student": "учеником",
            "tutor": "преподавателем",
            "admin": "администратором",
            "system": "системой",
        }.get(actor, actor)
        label = (
            f"🔄 Перенесено {who}: "
            f"{html.escape(str(details.get('old_date', '?')))} "
            f"{html.escape(str(details.get('old_time', '?')))} → "
            f"{html.escape(str(details.get('new_date', '?')))} "
            f"{html.escape(str(details.get('new_time', '?')))}"
        )
    else:
        label = labels.get(event_type, html.escape(str(event_type)))
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
    return (
        f"<b>{header}</b>\n\n"
        f"🆔 <b>Занятие #{booking_id}</b>\n"
        f"👤 Ученик: {html.escape(str(booking['username']))}\n"
        f"🆔 ID ученика: <code>{booking['user_id']}</code>\n"
        f"🌐 Платформа: {html.escape(str(platform))}\n\n"
        f"👨‍🏫 Преподаватель: {html.escape(str(tutor_name))}\n"
        f"📚 Предмет: {html.escape(str(booking['subject']))}\n"
        f"📅 Дата: {html.escape(str(booking['date']))}\n"
        f"🕒 Время: {html.escape(str(booking['time_slot']))} МСК\n\n"
        f"💳 Стоимость: {amount_text}\n"
        f"↩️ Возврат: {html.escape(str(refund_text))}\n"
        f"🕘 Последнее изменение: {format_dt(booking.get('updated_at'))}\n\n"
        f"<b>История:</b>\n{history}"
    )


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
