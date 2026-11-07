"""One active Telegram lesson per action screen."""

from datetime import timedelta

from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from booking_visibility_rules import is_trial_booking, payment_delivery_note
from tutor_students_rules import group_tutor_students

ACTIVE_STATUSES = frozenset({"pending", "confirmed", "paid"})
PAGE_SIZE = 8
STATUSES = {
    "pending": "Ожидает подтверждения",
    "confirmed": "Подтверждено, ожидает оплаты",
    "paid": "Оплачено",
    "cancelled": "Отменено",
    "completed": "Завершено",
}


def short(value, size=80):
    return " ".join(str(value or "—").split())[:size]


def button(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def status_label(booking):
    if booking["status"] == "confirmed" and is_trial_booking(booking):
        return "Пробное подтверждено, бесплатно"
    if delivery_note := payment_delivery_note(booking):
        return f"Подтверждено · {delivery_note}"
    return STATUSES.get(booking["status"], "Неизвестный статус")


def render_list(rows, page, *, tutor_id=None):
    """Rows carry the same booking ID used by the card and existing actions."""
    is_tutor = tutor_id is not None
    title = "📋 Ваши ученики · записи" if is_tutor else "📋 Мои записи"
    back = f"back_to_tutor_panel_{tutor_id}" if is_tutor else "back_to_menu"
    page_key = f"record_tutor_page_{tutor_id}" if is_tutor else "record_student_page"
    last_page = max(0, (len(rows) - 1) // PAGE_SIZE)
    page = min(page, last_page)
    lines = [title]
    keys = []
    if rows:
        lines.extend(["Откройте занятие, чтобы увидеть доступные действия.",
                      f"Страница {page + 1}/{last_page + 1}"])
    else:
        lines.append("Активных записей пока нет.")
    for booking_id, booking, name in rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        when = f"{short(booking['date'], 10)} {short(booking['time_slot'], 16)}"
        lines.append(f"№{booking_id} · {when} МСК\n"
                     f"{short(name)} · {short(booking['subject'])}\n{status_label(booking)}")
        role = "tutor" if is_tutor else "student"
        keys.append([button(f"Открыть №{booking_id} · {when}", f"record_{role}_{booking_id}_{page}")])
    paging = []
    if page > 0:
        paging.append(button("← Предыдущая", f"{page_key}_{page - 1}"))
    if page < last_page:
        paging.append(button("Следующая →", f"{page_key}_{page + 1}"))
    if paging:
        keys.append(paging)
    if not is_tutor:
        keys.append([button("📊 Статистика", "student_stats")])
    keys.append([button("🔙 Назад", back)])
    return "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keys)


async def student_list(legacy, db, user_id, page=0):
    records = await db.get_bookings_for_account("telegram", user_id, statuses=ACTIVE_STATUSES)
    tutors = await legacy.get_all_tutors() if records else {}
    rows = [(bid, booking, tutors.get(booking["tutor_id"], {}).get("name", "Преподаватель"))
            for bid, booking in records.items()]
    return render_list(rows, page)


def tutor_list(students, tutor_id, page=0):
    rows = []
    for student in students:
        name = f"{student['username']} (проведено: {student['completed_lessons']})"
        for bid, booking in student["active_bookings"]:
            rows.append((bid, booking, name))
    return render_list(rows, page, tutor_id=tutor_id)


def render_card(legacy, booking_id, booking, name, page, *, is_tutor):
    tid = booking["tutor_id"]
    back = f"record_tutor_page_{tid}_{page}" if is_tutor else f"record_student_page_{page}"
    who = "Ученик" if is_tutor else "Преподаватель"
    text = (f"Занятие №{booking_id}\n\n{who}: {short(name)}\n"
            f"Предмет: {short(booking['subject'])}\n"
            f"Дата и время: {short(booking['date'])} {short(booking['time_slot'])} МСК\n"
            f"Статус: {status_label(booking)}")
    if is_tutor:
        text += "\nКанал ученика: " + ("VK" if booking.get("user_platform") == "vk" else "Telegram")
    rows = []
    status = booking["status"]
    try:
        remaining = legacy.parse_booking_time(booking) - legacy.now_msk_naive()
    except (KeyError, TypeError, ValueError):
        remaining = timedelta(0)
    if is_tutor and status == "pending":
        rows.append([button(f"✅ Подтвердить №{booking_id}", f"tutor_confirm_{booking_id}")])
        rows.append([button(f"❌ Отклонить №{booking_id}", f"tutor_reject_{booking_id}")])
    elif is_tutor and status == "confirmed" and remaining > timedelta(hours=24):
        rows.append([button(f"🔄 Перенести №{booking_id}", f"tutor_reschedule_{booking_id}")])
        rows.append([button(f"❌ Отменить №{booking_id}", f"tutor_cancel_{booking_id}")])
    elif not is_tutor and remaining > timedelta(0) and legacy._tg_student_can_change(booking):
        if not is_trial_booking(booking) or remaining > timedelta(hours=24):
            rows.append([button(f"🔄 Перенести №{booking_id}", f"reschedule_student_{booking_id}")])
        elif is_trial_booking(booking):
            text += "\n\nПри отмене пробное будет считаться использованным."
        rows.append([button(f"❌ Отменить №{booking_id}", f"cancel_student_{booking_id}")])
    if not rows and not is_tutor and status == "paid":
        text += "\n\nЧтобы изменить оплаченное занятие, обратитесь в поддержку."
    rows.append([button("🔙 К списку записей", back)])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def install_telegram_booking_cards(app):
    legacy = app.legacy
    db = app._db
    if getattr(legacy, "_booking_cards_installed", False):
        return

    @legacy.dp.callback_query(F.data.regexp(r"^record_student_page_\d{1,9}$"))
    async def student_page(call, state):
        await legacy.safe_answer(call)
        await state.clear()
        text, kb = await student_list(legacy, db, call.from_user.id, int(call.data.rsplit("_", 1)[1]))
        await call.message.edit_text(text, reply_markup=kb, parse_mode=None)

    @legacy.dp.callback_query(F.data.regexp(r"^record_tutor_page_\d{1,10}_\d{1,9}$"))
    async def tutor_page(call, state):
        _, _, _, tid, page = call.data.split("_")
        if await legacy.get_tutor_by_telegram_id(call.from_user.id) != int(tid):
            await legacy.safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
            return
        await legacy.safe_answer(call)
        await state.clear()
        students = group_tutor_students(await legacy.get_all_bookings(), int(tid))
        text, kb = tutor_list(students, int(tid), int(page))
        await call.message.edit_text(text, reply_markup=kb, parse_mode=None)

    @legacy.dp.callback_query(F.data.regexp(r"^record_(student|tutor)_\d{1,10}_\d{1,9}$"))
    async def card(call, state):
        _, role, raw_bid, page = call.data.split("_")
        bid = int(raw_bid)
        is_tutor = role == "tutor"
        booking = await db.get_booking(bid)
        if not booking:
            await legacy.safe_answer(call, "Запись больше не доступна.", show_alert=True)
            return
        if is_tutor:
            if await legacy.get_tutor_by_telegram_id(call.from_user.id) != booking["tutor_id"]:
                await legacy.safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
                return
            name = booking.get("username")
        else:
            if not await db.account_owns_booking("telegram", call.from_user.id, booking):
                await legacy.safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
                return
            tutors = await legacy.get_all_tutors()
            name = tutors.get(booking["tutor_id"], {}).get("name", "Преподаватель")
        await legacy.safe_answer(call)
        await state.clear()
        text, kb = render_card(legacy, bid, booking, name, int(page), is_tutor=is_tutor)
        await call.message.edit_text(text, reply_markup=kb, parse_mode=None)

    legacy._booking_cards_installed = True
