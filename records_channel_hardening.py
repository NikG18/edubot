"""Accurate cross-platform records-channel cards for trials and subscriptions."""

from __future__ import annotations

import logging

import booking_records as records
import subscription_hardening as subs
from booking_visibility_rules import is_trial_booking
from database import get_all_tutors, get_booking, get_booking_events


TRIAL_HEADERS = {
    "pending": "🎓 ПРОБНОЕ — ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ",
    "confirmed": "🎓 ПРОБНОЕ ПОДТВЕРЖДЕНО — ОПЛАТА НЕ ТРЕБУЕТСЯ",
    "completed": "✅ ПРОБНОЕ ЗАНЯТИЕ ПРОВЕДЕНО",
    "cancelled": "🔴 ПРОБНОЕ ЗАНЯТИЕ ОТМЕНЕНО",
}


def _event_line(event: dict) -> str:
    event_type = str(event.get("event_type") or "")
    special = {
        "subscription_reserved": "🎟 Занятие зарезервировано из абонемента",
        "late_student_cancel_counted": "⚠️ Поздняя отмена/неявка засчитана как занятие",
        "refund_requested": "↩️ Запрошен возврат средств",
        "subscription_released": "🎟 Резерв занятия возвращён в абонемент",
    }
    if event_type not in special:
        return records.render_event(event)
    return f"{special[event_type]}\n   🕒 {records.format_dt(event.get('created_at'))}"


async def render_booking_record(booking_id: int):
    booking = await get_booking(int(booking_id))
    if not booking:
        return None
    tutors = await get_all_tutors()
    tutor = tutors.get(booking["tutor_id"], {})
    tutor_name = tutor.get("name", f"#{booking['tutor_id']}")
    trial = is_trial_booking(booking)

    usage = None
    if not trial:
        try:
            usage = await subs.get_booking_usage(int(booking_id))
        except Exception:
            logging.exception("Could not read subscription usage for records booking=%s", booking_id)

    status = str(booking.get("status") or "")
    header = TRIAL_HEADERS.get(status) if trial else None
    header = header or records.STATUS_HEADERS.get(status, status)
    if status == "paid" and usage:
        header = "🎟 ЗАНЯТИЕ ОПЛАЧЕНО ИЗ АБОНЕМЕНТА"
    if status == "cancelled" and not trial:
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
    amount_kop = int(booking.get("amount") or 0)
    amount_text = f"{amount_kop / 100:.2f} ₽" if amount_kop else "0.00 ₽"

    if trial:
        payment_source = "🎓 Бесплатное пробное · оплата не требуется"
        amount_text = "Бесплатно"
    elif usage:
        usage_status = {
            "reserved": "зарезервировано",
            "consumed": "использовано",
            "released": "резерв возвращён",
        }.get(str(usage.get("status") or ""), str(usage.get("status") or "—"))
        payment_source = (
            f"🎟 Абонемент #{usage['subscription_id']} · занятие {usage['unit_index']} · "
            f"{usage_status}"
        )
    elif booking.get("tinkoff_payment_id"):
        payment_source = f"💳 Отдельный платёж T-Bank · PaymentId {booking['tinkoff_payment_id']}"
    elif status == "confirmed":
        payment_source = "💳 Отдельная оплата ещё не создана/ожидается"
    else:
        payment_source = "💳 Отдельный платёж отсутствует"

    booking_type = "Пробное" if trial else "Обычное"
    student_id = booking.get("student_id")
    student_identity = str(student_id) if student_id is not None else "—"
    refund_status = booking.get("refund_status") or "none"
    refund_text = records.REFUND_LABELS.get(refund_status, refund_status)

    events = await get_booking_events(int(booking_id))
    hidden_count = max(0, len(events) - records.MAX_HISTORY_EVENTS)
    visible_events = events[-records.MAX_HISTORY_EVENTS:]
    history_parts = []
    if hidden_count:
        history_parts.append(f"… ещё событий: {hidden_count}")
    history_parts.extend(_event_line(event) for event in visible_events)
    history = "\n\n".join(history_parts) or "Нет событий"

    trial_state = ""
    if trial:
        trial_state = (
            f"\n🎓 Пробное использовано: {'да' if booking.get('trial_consumed') else 'нет'}"
        )

    text = (
        f"<b>{header}</b>\n\n"
        f"🆔 <b>Занятие #{booking_id}</b>\n"
        f"🏷 Тип: {records._short(booking_type, 40)}\n"
        f"👤 Ученик: {records._short(booking.get('username'), 160)}\n"
        f"🔗 Student ID: <code>{records._short(student_identity, 40)}</code>\n"
        f"🌐 Аккаунт: {records._short(platform, 40)} · <code>{records._short(booking.get('user_id'), 40)}</code>\n\n"
        f"👨‍🏫 Преподаватель: {records._short(tutor_name, 160)} · ID {booking['tutor_id']}\n"
        f"📚 Предмет: {records._short(booking.get('subject'), 160)}\n"
        f"📅 Дата: {records._short(booking.get('date'), 40)}\n"
        f"🕒 Время: {records._short(booking.get('time_slot'), 80)} МСК\n\n"
        f"💰 Сумма занятия: {amount_text}\n"
        f"{records._short(payment_source, 300)}\n"
        f"↩️ Возврат: {records._short(refund_text, 100)}"
        f"{trial_state}\n"
        f"🕘 Последнее изменение: {records.format_dt(booking.get('updated_at'))}\n\n"
        f"<b>История:</b>\n{history}"
    )
    if len(text) > records.MAX_RECORD_LENGTH:
        keep = max(250, records.MAX_RECORD_LENGTH - (len(text) - len(history)) - 80)
        history = history[-keep:]
        text = (
            f"<b>{header}</b>\n\n"
            f"🆔 <b>Занятие #{booking_id}</b> · {records._short(booking_type, 30)}\n"
            f"👤 {records._short(booking.get('username'), 120)} · {records._short(platform, 30)} "
            f"<code>{records._short(booking.get('user_id'), 30)}</code>\n"
            f"👨‍🏫 {records._short(tutor_name, 120)} · 📚 {records._short(booking.get('subject'), 120)}\n"
            f"📅 {records._short(booking.get('date'), 30)} · 🕒 {records._short(booking.get('time_slot'), 60)} МСК\n"
            f"💰 {amount_text} · {records._short(payment_source, 220)}\n"
            f"↩️ {records._short(refund_text, 80)}\n\n"
            f"<b>Последние события:</b>\n…\n{history}"
        )
    return text


def install_records_channel_hardening() -> None:
    if getattr(records, "_accurate_cross_platform_records_installed", False):
        return
    records.render_booking_record = render_booking_record
    records._accurate_cross_platform_records_installed = True
