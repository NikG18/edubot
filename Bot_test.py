import inspect
from types import SimpleNamespace

import database as _db
import Bot_test_legacy as legacy
from Bot_test_legacy import *
from booking_records import format_dt, render_event, sync_booking_record


# ---------------------------------------------------------------------------
# Совместимость со старым большим Telegram-модулем
# ---------------------------------------------------------------------------

def _caller_context():
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back and frame.f_back.f_back else None
        if not caller:
            return "", None
        actor_id = None
        call = caller.f_locals.get("call")
        if call is not None and getattr(call, "from_user", None) is not None:
            actor_id = getattr(call.from_user, "id", None)
        message = caller.f_locals.get("message")
        if actor_id is None and message is not None and getattr(message, "from_user", None) is not None:
            actor_id = getattr(message.from_user, "id", None)
        return caller.f_code.co_name, actor_id
    finally:
        del frame


async def _contextual_update_booking(booking_id, **kwargs):
    """Добавляет автора события, не меняя старые handlers."""
    caller, actor_id = _caller_context()
    status = kwargs.get("status")
    if status == "cancelled":
        if caller == "cancel_student_booking":
            kwargs.setdefault("_actor_type", "student")
            kwargs.setdefault("_actor_id", actor_id)
            kwargs.setdefault("_reason", "Отменено учеником")
        elif caller == "tutor_cancel_booking":
            kwargs.setdefault("_actor_type", "tutor")
            kwargs.setdefault("_actor_id", actor_id)
            kwargs.setdefault("_reason", "Отменено преподавателем")
    elif status == "confirmed":
        kwargs.setdefault("_actor_type", "tutor")
        kwargs.setdefault("_actor_id", actor_id)
        kwargs.setdefault("_event_type", "confirmed")
    return await _db.update_booking(booking_id, **kwargs)


async def _contextual_delete_booking(booking_id: int):
    """Старый tutor_reject больше не удаляет строку, а оставляет аудируемый отказ."""
    _caller, actor_id = _caller_context()
    changed, booking = await _db.change_booking_status(
        booking_id,
        "cancelled",
        event_type="rejected",
        actor_type="tutor",
        actor_id=actor_id,
        reason="Заявка отклонена преподавателем",
        expected_statuses={"pending"},
    )
    return booking if changed else booking


legacy.update_booking = _contextual_update_booking
legacy.delete_booking = _contextual_delete_booking
legacy.mark_booking_paid_once = _db.mark_booking_paid_once
legacy.mark_booking_payment_failed = _db.mark_booking_payment_failed
legacy.reschedule_booking = _db.reschedule_booking
legacy.move_booking_in_place = _db.move_booking_in_place
legacy.cleanup_old_bookings = _db.cleanup_old_bookings


# ---------------------------------------------------------------------------
# records_channel: старые handlers больше не удаляют и не создают дубли
# ---------------------------------------------------------------------------

_original_delete_message = legacy.Bot.delete_message
_original_send_message = legacy.Bot.send_message


def _records_chat(chat_id) -> bool:
    target = legacy.RECORDS_CHANNEL_ID
    return bool(target) and str(chat_id) == str(target)


def _booking_id_from_stack():
    for frame_info in inspect.stack()[2:14]:
        local_vars = frame_info.frame.f_locals
        for key in ("booking_id", "new_id", "old_bid", "bid"):
            value = local_vars.get(key)
            if isinstance(value, int) and value > 0:
                return value
    return None


async def _safe_delete_message(self, chat_id, message_id, *args, **kwargs):
    if _records_chat(chat_id):
        # Журнал занятий не удаляем никогда.
        return True
    return await _original_delete_message(self, chat_id, message_id, *args, **kwargs)


async def _safe_send_message(self, chat_id, text, *args, **kwargs):
    if _records_chat(chat_id):
        booking_id = _booking_id_from_stack()
        if booking_id:
            await sync_booking_record(booking_id)
            booking = await _db.get_booking(booking_id)
            if booking and booking.get("channel_msg_id"):
                # Старый код после send_message читает только message_id.
                return SimpleNamespace(message_id=booking["channel_msg_id"])
        # Не создаём дубль, если контекст брони определить не удалось.
        # Возвращаем объект с None: update_booking проигнорирует смысловую синхронизацию
        # channel_msg_id, а в журнале остаётся существующая карточка.
        legacy.logging.warning(
            "Подавлена попытка создать неидентифицированный дубль в records_channel"
        )
        return SimpleNamespace(message_id=None)
    return await _original_send_message(self, chat_id, text, *args, **kwargs)


legacy.Bot.delete_message = _safe_delete_message
legacy.Bot.send_message = _safe_send_message


# ---------------------------------------------------------------------------
# Админ-панель управления занятиями
# ---------------------------------------------------------------------------

_original_admin_actions_keyboard = legacy.admin_actions_keyboard


def admin_actions_keyboard():
    keyboard = _original_admin_actions_keyboard()
    button = legacy.InlineKeyboardButton(
        text="📚 Управление занятиями",
        callback_data="admin_bookings",
    )
    keyboard.inline_keyboard.insert(1, [button])
    return keyboard


legacy.admin_actions_keyboard = admin_actions_keyboard


async def _show_admin_booking(call, booking_id: int):
    booking = await _db.get_booking(booking_id)
    if not booking:
        await call.message.edit_text("Занятие не найдено.")
        return
    tutors = await _db.get_all_tutors()
    tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Неизвестный")
    amount = (booking.get("amount") or 0) / 100
    cancelled_line = ""
    if booking.get("cancelled_by"):
        cancelled_line = (
            f"\n❌ Кем отменено: {legacy.html.quote(str(booking['cancelled_by']))}"
            f"\n🕒 Отмена: {format_dt(booking.get('cancelled_at'))}"
        )
    text = (
        f"📚 <b>Занятие #{booking_id}</b>\n\n"
        f"Статус: <b>{legacy.html.quote(str(booking['status']))}</b>\n"
        f"👤 {legacy.html.quote(str(booking['username']))}\n"
        f"👨‍🏫 {legacy.html.quote(str(tutor_name))}\n"
        f"📚 {legacy.html.quote(str(booking['subject']))}\n"
        f"📅 {legacy.html.quote(str(booking['date']))}\n"
        f"🕒 {legacy.html.quote(str(booking['time_slot']))}\n"
        f"🌐 {legacy.html.quote(str(booking.get('user_platform') or 'telegram'))}\n"
        f"💳 {amount:.2f} ₽\n"
        f"↩️ Возврат: {legacy.html.quote(str(booking.get('refund_status') or 'none'))}\n"
        f"🕘 Обновлено: {format_dt(booking.get('updated_at'))}"
        f"{cancelled_line}"
    )
    buttons = []
    if booking["status"] in {"pending", "confirmed", "paid"}:
        buttons.append([
            legacy.InlineKeyboardButton(
                text="❌ Отменить занятие",
                callback_data=f"admin_booking_cancel_{booking_id}",
            )
        ])
    if booking.get("refund_status") in {"required", "pending"}:
        buttons.append([
            legacy.InlineKeyboardButton(
                text="↩️ Отметить возврат выполненным",
                callback_data=f"admin_booking_refund_{booking_id}",
            )
        ])
    buttons.append([
        legacy.InlineKeyboardButton(
            text="📜 История",
            callback_data=f"admin_booking_history_{booking_id}",
        )
    ])
    buttons.append([
        legacy.InlineKeyboardButton(text="🔙 К разделам", callback_data="admin_bookings")
    ])
    await call.message.edit_text(
        text,
        reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@legacy.dp.callback_query(legacy.F.data == "admin_bookings")
async def admin_bookings_menu(call: legacy.CallbackQuery):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
        [legacy.InlineKeyboardButton(text="🟠 Ожидают подтверждения", callback_data="admin_bookings_status_pending")],
        [legacy.InlineKeyboardButton(text="🟡 Ожидают оплаты", callback_data="admin_bookings_status_confirmed")],
        [legacy.InlineKeyboardButton(text="🟢 Оплаченные", callback_data="admin_bookings_status_paid")],
        [legacy.InlineKeyboardButton(text="✅ Проведённые", callback_data="admin_bookings_status_completed")],
        [legacy.InlineKeyboardButton(text="🔴 Отменённые", callback_data="admin_bookings_status_cancelled")],
        [legacy.InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel_open")],
    ])
    await call.message.edit_text("📚 Управление занятиями", reply_markup=keyboard)


@legacy.dp.callback_query(legacy.F.data.startswith("admin_bookings_status_"))
async def admin_bookings_list(call: legacy.CallbackQuery):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    status = call.data.removeprefix("admin_bookings_status_")
    if status not in {"pending", "confirmed", "paid", "completed", "cancelled"}:
        return
    bookings = await _db.get_all_bookings()
    rows = [(bid, b) for bid, b in bookings.items() if b["status"] == status]
    rows.sort(key=lambda item: (item[1]["date"], item[1]["time_slot"], item[0]))
    buttons = []
    for bid, booking in rows[-50:]:
        label = f"#{bid} {booking['date']} {booking['time_slot']} · {booking['username']}"
        buttons.append([
            legacy.InlineKeyboardButton(
                text=label[:60],
                callback_data=f"admin_booking_view_{bid}",
            )
        ])
    buttons.append([
        legacy.InlineKeyboardButton(text="🔙 К разделам", callback_data="admin_bookings")
    ])
    await call.message.edit_text(
        f"Занятия со статусом <b>{status}</b>: {len(rows)}",
        reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@legacy.dp.callback_query(legacy.F.data.startswith("admin_booking_view_"))
async def admin_booking_view(call: legacy.CallbackQuery):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    booking_id = int(call.data.rsplit("_", 1)[1])
    await _show_admin_booking(call, booking_id)


@legacy.dp.callback_query(legacy.F.data.regexp(r"^admin_booking_cancel_\d+$"))
async def admin_booking_cancel_confirm(call: legacy.CallbackQuery):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    booking_id = int(call.data.rsplit("_", 1)[1])
    booking = await _db.get_booking(booking_id)
    if not booking:
        await call.message.edit_text("Занятие не найдено.")
        return
    warning = ""
    if booking["status"] == "paid":
        warning = (
            "\n\n⚠️ Занятие оплачено. После отмены возврат будет отмечен как "
            "<b>требуется</b>, но деньги автоматически не возвращаются."
        )
    keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
        [legacy.InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"admin_do_booking_cancel_{booking_id}")],
        [legacy.InlineKeyboardButton(text="🔙 Назад", callback_data=f"admin_booking_view_{booking_id}")],
    ])
    await call.message.edit_text(
        f"Отменить занятие #{booking_id}?{warning}",
        reply_markup=keyboard,
    )


@legacy.dp.callback_query(legacy.F.data.startswith("admin_do_booking_cancel_"))
async def admin_booking_cancel_do(call: legacy.CallbackQuery):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    booking_id = int(call.data.rsplit("_", 1)[1])
    changed, booking = await _db.admin_cancel_booking(booking_id, call.from_user.id)
    if not changed or not booking:
        await call.message.edit_text("Статус занятия уже изменился.")
        return
    student_message = (
        f"❌ Администратор отменил занятие #{booking_id}.\n"
        f"📚 {booking['subject']}\n📅 {booking['date']} 🕒 {booking['time_slot']}"
    )
    if booking.get("refund_status") == "required":
        student_message += (
            "\n\n💳 Занятие было оплачено. Возврат средств обрабатывается отдельно."
        )
    await legacy.send_to_user(
        booking["user_id"],
        booking.get("user_platform", "telegram"),
        student_message,
    )
    await legacy.send_to_tutor(
        booking["tutor_id"],
        f"❌ Администратор отменил занятие #{booking_id} с {booking['username']}.",
    )
    await _show_admin_booking(call, booking_id)


@legacy.dp.callback_query(legacy.F.data.startswith("admin_booking_refund_"))
async def admin_booking_refund(call: legacy.CallbackQuery):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    booking_id = int(call.data.rsplit("_", 1)[1])
    ok = await _db.mark_booking_refunded(booking_id, call.from_user.id)
    if not ok:
        await call.message.edit_text("Возврат уже отмечен или не требуется.")
        return
    await _show_admin_booking(call, booking_id)


@legacy.dp.callback_query(legacy.F.data.startswith("admin_booking_history_"))
async def admin_booking_history(call: legacy.CallbackQuery):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    booking_id = int(call.data.rsplit("_", 1)[1])
    events = await _db.get_booking_events(booking_id)
    visible = events[-25:]
    prefix = f"… пропущено ранних событий: {len(events) - len(visible)}\n\n" if len(events) > len(visible) else ""
    history = prefix + ("\n\n".join(render_event(event) for event in visible) or "История отсутствует")
    keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
        [legacy.InlineKeyboardButton(text="🔙 К занятию", callback_data=f"admin_booking_view_{booking_id}")]
    ])
    await call.message.edit_text(
        f"📜 <b>История занятия #{booking_id}</b>\n\n{history}",
        reply_markup=keyboard,
    )


async def main():
    return await legacy.main()


if __name__ == "__main__":
    legacy.logging.basicConfig(level=legacy.logging.INFO, stream=legacy.sys.stdout)
    legacy.asyncio.run(main())
