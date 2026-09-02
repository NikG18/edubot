import inspect
from datetime import timedelta
from types import FunctionType, SimpleNamespace

import database as _db
import Bot_test_legacy as legacy
from Bot_test_legacy import *
from booking_records import format_dt, render_event, sync_booking_record
from fiscal_agent import get_tutor_phone, normalize_supplier_phone, set_tutor_phone


def _is_trial(booking) -> bool:
    return bool(booking and booking.get("booking_type") == "trial")


def _stack_has_caller(name: str) -> bool:
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame else None
        for _ in range(8):
            if current is None:
                break
            if current.f_code.co_name == name:
                return True
            current = current.f_back
        return False
    finally:
        del frame


async def _contextual_add_booking(tutor_id, user_id, username, subject, date, time_slot,
                                  channel_msg_id=None, user_platform="telegram",
                                  booking_type="regular", trial_email=None):
    if _stack_has_caller("confirm_trial_booking"):
        booking_type = "trial"
    return await _db.add_booking(
        tutor_id, user_id, username, subject, date, time_slot,
        channel_msg_id=channel_msg_id,
        user_platform=user_platform,
        booking_type=booking_type,
        trial_email=trial_email,
    )


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


# ---------------------------------------------------------------------------
# Ученик Telegram: pending/confirmed можно отменять и переносить
# ---------------------------------------------------------------------------

def _tg_student_can_change(booking) -> bool:
    if not booking or booking.get("status") == "paid":
        return False
    start = legacy.parse_booking_time(booking)
    now = legacy.now_msk_naive()
    if start <= now:
        return False
    if booking.get("status") == "pending":
        return True
    if booking.get("status") == "confirmed":
        return _is_trial(booking) or (start - now) > timedelta(hours=24)
    return False


async def _tg_render_records(message):
    user_id = message.from_user.id
    bookings = await _db.get_bookings_for_account(
        "telegram", user_id, statuses={"pending", "confirmed", "paid"}
    )
    rows = list(bookings.items())
    if not rows:
        await message.answer(
            "У вас пока нет активных записей.",
            reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=[
                [legacy.InlineKeyboardButton(text="📊 Статистика", callback_data="student_stats")],
                [legacy.InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")],
            ]),
        )
        return

    tutors = await legacy.get_all_tutors()
    text_lines = ["Ваши записи:\n"]
    buttons = []
    for bid, booking in rows:
        tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Неизвестный")
        can_change = _tg_student_can_change(booking)
        can_reschedule = can_change
        if _is_trial(booking):
            can_reschedule = (
                legacy.parse_booking_time(booking) - legacy.now_msk_naive()
            ) > timedelta(hours=24)
        status_text = {
            "pending": "ожидает подтверждения",
            "confirmed": "подтверждено, ожидает оплаты",
            "paid": "оплачено",
        }.get(booking["status"], booking["status"])
        if _is_trial(booking) and booking["status"] == "confirmed":
            status_text = "пробное подтверждено, оплата не требуется"
        action_note = "⚠️ Действия недоступны"
        if can_change:
            action_note = (
                "⚠️ Можно отменить; пробное будет считаться использованным"
                if _is_trial(booking) and not can_reschedule
                else "✅ Можно отменить/перенести"
            )
        text_lines.append(
            f"👨‍🏫 {tutor_name}\n"
            f"📚 {booking['subject']}\n"
            f"📅 {booking['date']} 🕒 {booking['time_slot']} ({status_text})\n"
            + action_note
        )
        if can_reschedule:
            buttons.append([
                legacy.InlineKeyboardButton(
                    text=f"🔄 Перенести: {tutor_name} {booking['date']} {booking['time_slot']}"[:64],
                    callback_data=f"reschedule_student_{bid}",
                )
            ])
        if can_change:
            buttons.append([
                legacy.InlineKeyboardButton(
                    text=f"❌ Отменить: {tutor_name} {booking['date']} {booking['time_slot']}"[:64],
                    callback_data=f"cancel_student_{bid}",
                )
            ])
    buttons.append([legacy.InlineKeyboardButton(text="📊 Статистика", callback_data="student_stats")])
    buttons.append([legacy.InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    await message.answer(
        "\n".join(text_lines),
        reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def _tg_my_records(message, state):
    await state.clear()
    await message.answer("Переходим в раздел...", reply_markup=legacy.ReplyKeyboardRemove())
    await legacy._tg_render_records(message)


async def _tg_back_to_my_records(call, state):
    await legacy.safe_answer(call)
    await state.clear()
    await legacy._tg_render_records(call.message)


async def _tg_cancel_student_booking(call, bot):
    await legacy.safe_answer(call)
    bid = int(call.data.split("_")[2])
    booking = await _db.get_booking(bid)
    if not booking:
        await call.message.edit_text("Запись не найдена.")
        return
    if not await _db.account_owns_booking("telegram", call.from_user.id, booking):
        await call.message.edit_text("⛔ Доступ запрещён.")
        return
    if booking["status"] == "paid":
        await call.message.edit_text("Для отмены оплаченного занятия обратитесь в поддержку для возврата.")
        return
    if not legacy._tg_student_can_change(booking):
        await call.message.edit_text("Эту запись уже нельзя отменить.")
        return

    changed, current = await _db.cancel_booking_record(
        bid,
        actor_type="student",
        actor_id=call.from_user.id,
        reason="Отменено учеником",
        expected_statuses={"pending", "confirmed"},
    )
    if not changed:
        await call.message.edit_text("Статус записи уже изменился.")
        return
    await legacy.send_to_tutor(
        current["tutor_id"],
        f"❌ Ученик {current['username']} отменил занятие:\n"
        f"📚 {current['subject']}\n📅 {current['date']} 🕒 {current['time_slot']}",
    )
    cancellation_text = "✅ Запись отменена."
    if _is_trial(current):
        cancellation_text += (
            " Поскольку до начала оставалось не более 24 часов, пробное считается использованным."
            if current.get("trial_consumed")
            else " Пробное не израсходовано — вы сможете выбрать другое время."
        )
    await call.message.edit_text(
        cancellation_text,
        reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text="🔙 К моим записям", callback_data="back_to_my_records")]
        ]),
    )


async def _tg_student_reschedule_start(call, state):
    await legacy.safe_answer(call)
    bid = int(call.data.split("_")[2])
    booking = await _db.get_booking(bid)
    if not booking:
        await call.message.edit_text("Запись не найдена.")
        return
    if not await _db.account_owns_booking("telegram", call.from_user.id, booking):
        await call.message.edit_text("⛔ Доступ запрещён.")
        return
    if booking["status"] == "paid":
        await call.message.edit_text("Оплаченное занятие переносится через поддержку.")
        return
    if _is_trial(booking):
        start = legacy.parse_booking_time(booking)
        if (start - legacy.now_msk_naive()) <= timedelta(hours=24):
            await call.message.edit_text(
                "Пробное уже нельзя переносить менее чем за 24 часа. Его можно отменить, "
                "но оно будет считаться использованным."
            )
            return
    if not legacy._tg_student_can_change(booking):
        await call.message.edit_text("Эту запись уже нельзя перенести.")
        return

    await state.update_data(
        old_booking_id=bid,
        tutor_id=booking["tutor_id"],
        subject=booking["subject"],
        old_date=booking["date"],
        old_time=booking["time_slot"],
        old_status=booking["status"],
        student_id=booking["user_id"],
        student_username=booking["username"],
        user_platform=booking.get("user_platform", "telegram"),
    )
    dates = await legacy.get_available_dates(booking["tutor_id"])
    if not dates:
        await call.message.edit_text("У преподавателя нет свободных дат для переноса.")
        return
    buttons = []
    row = []
    for date_str in dates:
        dt = legacy.datetime.strptime(date_str, "%d.%m.%Y")
        label = f"{date_str} ({legacy.WEEKDAY_NAMES[legacy.WEEKDAYS[dt.weekday()]]})"
        row.append(legacy.InlineKeyboardButton(text=label, callback_data=f"reschedule_date_{date_str}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([legacy.InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_menu")])
    await call.message.edit_text(
        "Выберите новую дату:",
        reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(legacy.StudentRescheduleStates.waiting_date)


async def _tg_reschedule_unpaid_in_place(booking_id: int, new_date: str, new_time: str, actor_id: int) -> bool:
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        try:
            async with conn.transaction():
                old = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 FOR UPDATE", booking_id)
                if not old or old["status"] not in {"pending", "confirmed"}:
                    return False
                if old["date"] == new_date and old["time_slot"] == new_time:
                    return True
                await conn.execute(
                    "UPDATE bookings SET date=$1,time_slot=$2,reminded=0,updated_at=NOW() WHERE id=$3",
                    new_date, new_time, booking_id,
                )
                await _db._add_booking_event(
                    conn, booking_id, "rescheduled", old["status"], old["status"],
                    "student", actor_id,
                    {
                        "old_date": old["date"],
                        "old_time": old["time_slot"],
                        "new_date": new_date,
                        "new_time": new_time,
                        "payment_link_kept": bool(old["tinkoff_payment_id"]),
                    },
                )
        except _db.asyncpg.UniqueViolationError:
            return False
    await _db._sync_booking_record_safely(booking_id)
    return True


async def _tg_confirm_student_reschedule(call, state, bot):
    await legacy.safe_answer(call)
    data = await state.get_data()
    bid = data["old_booking_id"]
    booking = await _db.get_booking(bid)
    if not booking or not await _db.account_owns_booking("telegram", call.from_user.id, booking):
        await call.message.edit_text("Запись не найдена.")
        await state.clear()
        return
    moved = await legacy._tg_reschedule_unpaid_in_place(
        bid, data["new_date"], data["new_time"], call.from_user.id
    )
    if not moved:
        await call.message.edit_text("⚠️ Новый слот уже занят. Старая запись сохранена.")
        await state.clear()
        return

    payment_note = ""
    if booking.get("tinkoff_payment_id"):
        payment_note = "\n💳 Ранее выданная ссылка на оплату остаётся действительной для этой записи."
    await legacy.send_to_tutor(
        booking["tutor_id"],
        f"🔄 Ученик {booking['username']} перенёс занятие.\n"
        f"📚 {booking['subject']}\n"
        f"Было: {data['old_date']} {data['old_time']}\n"
        f"Стало: {data['new_date']} {data['new_time']}",
    )
    await call.message.edit_text(f"✅ Занятие перенесено.{payment_note}")
    await state.clear()


async def _trial_aware_tutor_confirm_booking(call, bot, state):
    await legacy.safe_answer(call)
    bid = int(call.data.split("_")[2])
    booking = await _db.get_booking(bid)
    if not _is_trial(booking):
        return await legacy._original_tutor_confirm_booking(call, bot, state)
    if not booking:
        await call.message.edit_text("Заявка не найдена.")
        return
    if not await legacy._require_booking_tutor(call, booking):
        return
    if booking["status"] != "pending":
        await call.message.edit_text("Заявка уже обработана.")
        return

    await _db.update_booking(
        bid,
        status="confirmed",
        amount=0,
        commission_percent=0,
        _actor_type="tutor",
        _actor_id=call.from_user.id,
        _event_type="confirmed",
    )
    await legacy.send_to_user(
        booking["user_id"],
        booking.get("user_platform", "telegram"),
        (
            "✅ Бесплатное пробное занятие подтверждено!\n"
            f"📚 {booking['subject']}\n"
            f"📅 {booking['date']} 🕒 {booking['time_slot']}\n\n"
            "Оплата не требуется."
        ),
    )
    await state.clear()
    await call.message.edit_text("✅ Бесплатное пробное занятие подтверждено. Оплата не требуется.")


# В aiogram обработчики уже зарегистрированы в legacy.dp, поэтому меняем код
# зарегистрированных функций, а вспомогательные функции кладём в globals legacy.
legacy.legacy = legacy
legacy._db = _db
legacy._is_trial = _is_trial
legacy._tg_student_can_change = _tg_student_can_change
legacy._tg_render_records = _tg_render_records
legacy._tg_reschedule_unpaid_in_place = _tg_reschedule_unpaid_in_place
legacy.my_records.__code__ = _tg_my_records.__code__
legacy.back_to_my_records.__code__ = _tg_back_to_my_records.__code__
legacy.cancel_student_booking.__code__ = _tg_cancel_student_booking.__code__
legacy.student_reschedule_start.__code__ = _tg_student_reschedule_start.__code__
legacy.confirm_student_reschedule.__code__ = _tg_confirm_student_reschedule.__code__
legacy._original_tutor_confirm_booking = FunctionType(
    legacy.tutor_confirm_booking.__code__,
    legacy.tutor_confirm_booking.__globals__,
    name=legacy.tutor_confirm_booking.__name__,
    argdefs=legacy.tutor_confirm_booking.__defaults__,
    closure=legacy.tutor_confirm_booking.__closure__,
)
legacy.tutor_confirm_booking.__code__ = _trial_aware_tutor_confirm_booking.__code__
legacy.add_booking = _contextual_add_booking


# ---------------------------------------------------------------------------
# Автоотмена подтверждённых, но неоплаченных занятий за 72 часа
# ---------------------------------------------------------------------------

_original_cleanup_old_bookings = _db.cleanup_old_bookings


async def _cleanup_with_unpaid_autocancel():
    result = await _original_cleanup_old_bookings()
    now = legacy.now_msk_naive()
    bookings = await _db.get_all_bookings()
    for bid, booking in bookings.items():
        if booking.get("status") != "confirmed":
            continue
        if _is_trial(booking):
            continue
        try:
            start = legacy.parse_booking_time(booking)
        except Exception:
            legacy.logging.warning("Не удалось разобрать время booking %s для автоотмены", bid)
            continue
        remaining = start - now
        if remaining.total_seconds() <= 0 or remaining > timedelta(days=3):
            continue
        changed, cancelled = await _db.cancel_booking_record(
            bid,
            actor_type="system",
            reason="Не оплачено за 72 часа до занятия",
            expected_statuses={"confirmed"},
        )
        if not changed or not cancelled:
            continue
        text = (
            f"❌ Занятие #{bid} автоматически отменено, потому что оплата не поступила "
            "за 72 часа до начала.\n"
            f"📚 {cancelled['subject']}\n"
            f"📅 {cancelled['date']} 🕒 {cancelled['time_slot']}"
        )
        try:
            await legacy.send_to_user(
                cancelled["user_id"], cancelled.get("user_platform", "telegram"), text
            )
        except Exception:
            legacy.logging.exception("Не удалось уведомить ученика об автоотмене booking %s", bid)
        try:
            await legacy.send_to_tutor(
                cancelled["tutor_id"],
                f"❌ Занятие #{bid} с {cancelled['username']} автоматически отменено: "
                "оплата не поступила за 72 часа до начала.",
            )
        except Exception:
            legacy.logging.exception("Не удалось уведомить преподавателя об автоотмене booking %s", bid)
    return result


legacy.update_booking = _contextual_update_booking
legacy.delete_booking = _contextual_delete_booking
legacy.mark_booking_paid_once = _db.mark_booking_paid_once
legacy.mark_booking_payment_failed = _db.mark_booking_payment_failed
legacy.reschedule_booking = _db.reschedule_booking
legacy.move_booking_in_place = _db.move_booking_in_place
legacy.cleanup_old_bookings = _cleanup_with_unpaid_autocancel


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
        return True
    return await _original_delete_message(self, chat_id, message_id, *args, **kwargs)


async def _safe_send_message(self, chat_id, text, *args, **kwargs):
    if _records_chat(chat_id):
        booking_id = _booking_id_from_stack()
        if booking_id:
            await sync_booking_record(booking_id)
            booking = await _db.get_booking(booking_id)
            if booking and booking.get("channel_msg_id"):
                return SimpleNamespace(message_id=booking["channel_msg_id"])
        legacy.logging.warning("Подавлена попытка создать неидентифицированный дубль в records_channel")
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
    bookings_button = legacy.InlineKeyboardButton(
        text="📚 Управление занятиями",
        callback_data="admin_bookings",
    )
    phones_button = legacy.InlineKeyboardButton(
        text="📞 Телефоны репетиторов",
        callback_data="admin_tutor_phones",
    )
    keyboard.inline_keyboard.insert(1, [bookings_button])
    keyboard.inline_keyboard.insert(2, [phones_button])
    return keyboard


legacy.admin_actions_keyboard = admin_actions_keyboard


class FiscalAdminStates(legacy.StatesGroup):
    waiting_tutor_phone = legacy.State()


async def _tutor_phone_keyboard():
    tutors = await _db.get_all_tutors()
    buttons = []
    for tutor_id, tutor in tutors.items():
        phone = await get_tutor_phone(tutor_id)
        label = f"{tutor['name']} · {phone or 'телефон не указан'}"
        buttons.append([
            legacy.InlineKeyboardButton(
                text=label[:60],
                callback_data=f"admin_tutor_phone_{tutor_id}",
            )
        ])
    buttons.append([
        legacy.InlineKeyboardButton(
            text="🔙 В админ-панель",
            callback_data="admin_panel_open",
        )
    ])
    return legacy.InlineKeyboardMarkup(inline_keyboard=buttons)


@legacy.dp.callback_query(legacy.F.data == "admin_tutor_phones")
async def admin_tutor_phones(call: legacy.CallbackQuery, state: legacy.FSMContext):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    await state.clear()
    await call.message.edit_text(
        "📞 <b>Телефоны репетиторов</b>\n\n"
        "Выберите репетитора. Номер попадёт в реквизиты поставщика "
        "агентского фискального чека.",
        reply_markup=await _tutor_phone_keyboard(),
    )


@legacy.dp.callback_query(legacy.F.data.regexp(r"^admin_tutor_phone_\d+$"))
async def admin_tutor_phone_start(call: legacy.CallbackQuery, state: legacy.FSMContext):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    tutor_id = int(call.data.rsplit("_", 1)[1])
    tutors = await _db.get_all_tutors()
    tutor = tutors.get(tutor_id)
    if not tutor:
        await call.message.edit_text("Репетитор не найден.")
        return
    current = await get_tutor_phone(tutor_id)
    await state.update_data(fiscal_phone_tutor_id=tutor_id)
    await state.set_state(FiscalAdminStates.waiting_tutor_phone)
    await call.message.edit_text(
        f"Репетитор: <b>{legacy.html.quote(str(tutor['name']))}</b>\n"
        f"Текущий телефон: <code>{legacy.html.quote(current or 'не указан')}</code>\n\n"
        "Отправьте контактный телефон поставщика, например "
        "<code>+79991234567</code>. Пробелы, скобки и дефисы допустимы."
    )


@legacy.dp.message(FiscalAdminStates.waiting_tutor_phone)
async def admin_tutor_phone_save(message: legacy.Message, state: legacy.FSMContext):
    if message.from_user.id != legacy.ADMING_ID:
        await state.clear()
        return
    phone = normalize_supplier_phone(message.text)
    if not phone:
        await message.answer(
            "Некорректный номер. Отправьте от 7 до 18 цифр, "
            "например <code>+79991234567</code>."
        )
        return
    data = await state.get_data()
    tutor_id = data.get("fiscal_phone_tutor_id")
    saved = await set_tutor_phone(tutor_id, phone) if tutor_id else None
    await state.clear()
    if not saved:
        await message.answer("Репетитор не найден, телефон не сохранён.")
        return
    await message.answer(
        f"✅ Телефон для <b>{legacy.html.quote(str(saved['name']))}</b> сохранён: "
        f"<code>{legacy.html.quote(saved['phone'])}</code>",
        reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(
                text="📞 К телефонам репетиторов",
                callback_data="admin_tutor_phones",
            )],
            [legacy.InlineKeyboardButton(
                text="🔙 В админ-панель",
                callback_data="admin_panel_open",
            )],
        ]),
    )


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
