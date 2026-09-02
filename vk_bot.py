import inspect
from datetime import timedelta

import database as _db
import vk_bot_legacy as legacy
from vk_bot_legacy import *
from fiscal_agent import get_tutor_phone, normalize_supplier_phone, set_tutor_phone

TRIAL_PREFIX = "Пробное: "


class FiscalAdminStates(legacy.BaseStateGroup):
    waiting_tutor_phone = "waiting_tutor_phone"


def _vk_tutor_edit_keyboard():
    keyboard = legacy.Keyboard(inline=True)
    buttons = [
        ("Изменить имя", "edit_name"),
        ("Изменить описание", "edit_desc"),
        ("Изменить Telegram ID", "edit_telegram_id"),
        ("Изменить VK ID", "edit_vk_id"),
        ("🆔 Изменить ИНН", "edit_inn"),
        ("📞 Изменить телефон", "edit_phone"),
        ("📚 Управление предметами", "manage_subjects"),
        ("💰 Изменить комиссию", "edit_commission"),
        ("🔄 Режим комиссии", "toggle_commission_mode"),
        ("🔙 К списку", "admin_edit_list"),
    ]
    for index, (label, command) in enumerate(buttons):
        keyboard.add(legacy.Callback(label, payload={"cmd": command}))
        if index != len(buttons) - 1:
            keyboard.row()
    return keyboard.get_json()


async def _vk_show_tutor_editor(event, tutor_id: int):
    tutors = await legacy.get_all_tutors()
    tutor = tutors.get(tutor_id)
    if not tutor:
        await legacy.edit_event_message(event, "Репетитор не найден.")
        return False
    phone = await get_tutor_phone(tutor_id)
    await legacy.edit_event_message(
        event,
        f"Редактирование: {tutor['name']}\n"
        f"Телефон для чека: {phone or 'не указан'}\n\n"
        "Что хотите изменить?",
        keyboard=_vk_tutor_edit_keyboard(),
    )
    return True


async def _vk_edit_tutor_choice(event):
    tutor_id = int(event.payload["cmd"].split("_")[-1])
    await legacy.state_dispenser.set(
        event.user_id,
        legacy.AdminStates.waiting_edit_choice,
    )
    await legacy.state_dispenser.update(event.user_id, edit_tutor_id=tutor_id)
    await _vk_show_tutor_editor(event, tutor_id)


async def _vk_back_to_edit_tutor(event):
    data = await legacy.state_dispenser.get_data(event.user_id)
    tutor_id = data.get("edit_tutor_id")
    if not tutor_id:
        await legacy.edit_event_message(event, "Репетитор не выбран.")
        return
    await legacy.state_dispenser.set(
        event.user_id,
        legacy.AdminStates.waiting_edit_choice,
    )
    await legacy.state_dispenser.update(event.user_id, edit_tutor_id=tutor_id)
    await _vk_show_tutor_editor(event, tutor_id)


_original_vk_edit_field_choice = legacy.edit_field_choice


async def _vk_edit_field_choice(event):
    field = event.payload["cmd"].removeprefix("edit_")
    if field != "phone":
        return await _original_vk_edit_field_choice(event)

    data = await legacy.state_dispenser.get_data(event.user_id)
    tutor_id = data.get("edit_tutor_id")
    tutors = await legacy.get_all_tutors()
    tutor = tutors.get(tutor_id)
    if not tutor:
        await legacy.edit_event_message(event, "Репетитор не найден.")
        return
    current = await get_tutor_phone(tutor_id)
    await legacy.state_dispenser.set(
        event.user_id,
        FiscalAdminStates.waiting_tutor_phone,
    )
    await legacy.state_dispenser.update(
        event.user_id,
        edit_tutor_id=tutor_id,
        fiscal_phone_tutor_id=tutor_id,
    )
    await legacy.edit_event_message(
        event,
        f"Репетитор: {tutor['name']}\n"
        f"Текущий телефон: {current or 'не указан'}\n\n"
        "Отправьте контактный телефон поставщика, например +79991234567. "
        "Пробелы, скобки и дефисы допустимы. Для отмены отправьте: отмена",
    )


legacy.get_tutor_phone = get_tutor_phone
legacy._vk_tutor_edit_keyboard = _vk_tutor_edit_keyboard
legacy._vk_show_tutor_editor = _vk_show_tutor_editor
legacy.edit_tutor_choice = _vk_edit_tutor_choice
legacy.back_to_edit_tutor = _vk_back_to_edit_tutor
legacy.edit_field_choice = _vk_edit_field_choice


@legacy.bot.on.private_message(state=FiscalAdminStates.waiting_tutor_phone)
async def vk_admin_tutor_phone_save(message: legacy.Message):
    if message.from_id != legacy.ADMIN_VK_ID:
        await legacy.state_dispenser.delete(message.from_id)
        return
    value = (message.text or "").strip()
    data = await legacy.state_dispenser.get_data(message.from_id)
    tutor_id = data.get("fiscal_phone_tutor_id")
    if value.lower() in {"отмена", "назад", "cancel"}:
        await legacy.state_dispenser.delete(message.from_id)
        await message.answer(
            "Изменение телефона отменено.",
            keyboard=await legacy.get_main_menu(message.from_id),
        )
        return
    phone = normalize_supplier_phone(value)
    if not phone:
        await message.answer(
            "Некорректный номер. Укажите от 7 до 18 цифр, "
            "например: +79991234567"
        )
        return
    saved = await set_tutor_phone(tutor_id, phone) if tutor_id else None
    if not saved:
        await message.answer("Репетитор не найден. Откройте его карточку заново.")
        await legacy.state_dispenser.delete(message.from_id)
        return
    await legacy.state_dispenser.set(
        message.from_id,
        legacy.AdminStates.waiting_edit_choice,
    )
    await legacy.state_dispenser.update(message.from_id, edit_tutor_id=tutor_id)
    await message.answer(
        f"✅ Телефон для {saved['name']} сохранён: {saved['phone']}",
        keyboard=_vk_tutor_edit_keyboard(),
    )


def _caller_context():
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back and frame.f_back.f_back else None
        if not caller:
            return "", None
        actor_id = None
        event = caller.f_locals.get("event")
        if event is not None:
            actor_id = getattr(event, "user_id", None)
        message = caller.f_locals.get("message")
        if actor_id is None and message is not None:
            actor_id = getattr(message, "from_id", None)
        return caller.f_code.co_name, actor_id
    finally:
        del frame


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


def _is_trial(booking) -> bool:
    return bool(
        booking and (
            booking.get("booking_type") == "trial"
            or str(booking.get("subject") or "").startswith(TRIAL_PREFIX)
        )
    )


def _booking_start(booking):
    return legacy.datetime.strptime(
        booking["date"] + " " + booking["time_slot"].split("-")[0].replace(".", ":"),
        "%d.%m.%Y %H:%M",
    )


def _student_can_change(booking) -> bool:
    if not booking:
        return False
    status = booking.get("status")
    if status == "paid":
        return False
    start = _booking_start(booking)
    if start <= legacy.now_msk_naive():
        return False
    if status == "pending":
        return True
    if status == "confirmed":
        return _is_trial(booking) or (start - legacy.now_msk_naive()) > timedelta(hours=24)
    return False


async def _contextual_update_booking(booking_id, **kwargs):
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
# VK compatibility fixes
# ---------------------------------------------------------------------------
_original_open_link = legacy.OpenLink


def _compat_open_link(label, link, *args, **kwargs):
    return _original_open_link(link, label, *args, **kwargs)


legacy.OpenLink = _compat_open_link

_original_get_available_dates = legacy.get_available_dates


async def _vk_safe_available_dates(tutor_id: int, days_ahead=30):
    dates = await _original_get_available_dates(tutor_id, days_ahead=days_ahead)
    return dates[:9]


legacy.get_available_dates = _vk_safe_available_dates


# ---------------------------------------------------------------------------
# Бесплатные пробные занятия (временная совместимость до booking_type)
# ---------------------------------------------------------------------------
_original_add_booking = _db.add_booking


async def _contextual_add_booking(tutor_id, user_id, username, subject, date, time_slot,
                                  channel_msg_id=None, user_platform="telegram",
                                  booking_type="regular", trial_email=None):
    if _stack_has_caller("confirm_trial_booking") and not str(subject).startswith(TRIAL_PREFIX):
        subject = f"{TRIAL_PREFIX}{subject}"
        booking_type = "trial"
    return await _original_add_booking(
        tutor_id, user_id, username, subject, date, time_slot,
        channel_msg_id=channel_msg_id, user_platform=user_platform,
        booking_type="trial" if str(subject).startswith(TRIAL_PREFIX) else booking_type,
        trial_email=trial_email,
    )


_original_tutor_confirm_booking = legacy.tutor_confirm_booking


async def _trial_aware_tutor_confirm_booking(event):
    try:
        booking_id = int(event.payload["cmd"].rsplit("_", 1)[1])
    except (KeyError, ValueError, TypeError):
        return await _original_tutor_confirm_booking(event)

    booking = await _db.get_booking(booking_id)
    if not _is_trial(booking):
        return await _original_tutor_confirm_booking(event)

    tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
    if not booking or tutor_id != booking.get("tutor_id"):
        await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
        return
    if booking.get("status") != "pending":
        await legacy.edit_event_message(event, "Заявка уже обработана.")
        return

    await _db.update_booking(
        booking_id,
        status="confirmed",
        amount=0,
        commission_percent=0,
        _actor_type="tutor",
        _actor_id=event.user_id,
        _event_type="confirmed",
    )
    clean_subject = str(booking["subject"])
    if clean_subject.startswith(TRIAL_PREFIX):
        clean_subject = clean_subject[len(TRIAL_PREFIX):]
    await legacy.send_to_user(
        booking["user_id"],
        booking.get("user_platform", "vk"),
        (
            "✅ Бесплатное пробное занятие подтверждено!\n"
            f"📚 {clean_subject}\n"
            f"📅 {booking['date']} 🕒 {booking['time_slot']}\n\n"
            "Оплата не требуется."
        ),
    )
    await legacy.edit_event_message(event, "✅ Бесплатное пробное занятие подтверждено. Оплата не требуется.")


legacy.tutor_confirm_booking = _trial_aware_tutor_confirm_booking

# ---------------------------------------------------------------------------
# Ученик: pending/confirmed можно отменять и переносить
# ---------------------------------------------------------------------------
async def _compat_render_my_records(user_id, *, edit_event=None, message=None):
    bookings = await _db.get_bookings_for_account(
        "vk", user_id, statuses={"pending", "confirmed", "paid"}
    )
    user_bookings = list(bookings.items())
    if not user_bookings:
        kb = legacy.Keyboard(inline=True)
        kb.add(legacy.Callback("📊 Статистика", payload={"cmd": "student_stats"}))
        kb.row()
        kb.add(legacy.Callback("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
        if edit_event is not None:
            await legacy.edit_event_message(edit_event, "У вас пока нет активных записей.", keyboard=kb.get_json())
        else:
            await message.answer("У вас пока нет активных записей.", keyboard=kb.get_json())
        return

    tutors = await legacy.get_all_tutors()
    text_lines = ["Ваши записи:\n"]
    kb = legacy.Keyboard(inline=True)
    for bid, b in user_bookings:
        tutor = tutors.get(b["tutor_id"], {"name": "Неизвестный"})
        can_act = _student_can_change(b)
        can_reschedule = can_act
        if _is_trial(b):
            can_reschedule = (
                _booking_start(b) - legacy.now_msk_naive()
            ) > timedelta(hours=24)
        status_text = {
            "pending": "ожидает подтверждения",
            "confirmed": "подтверждено, ожидает оплаты",
            "paid": "оплачено",
        }.get(b["status"], b["status"])
        if _is_trial(b) and b["status"] == "confirmed":
            status_text = "пробное подтверждено, оплата не требуется"
        action_note = "⚠️ Действия недоступны"
        if can_act:
            action_note = (
                "⚠️ Можно отменить; пробное будет считаться использованным"
                if _is_trial(b) and not can_reschedule
                else "✅ Можно отменить/перенести"
            )
        text_lines.append(
            f"👨‍🏫 {tutor['name']}\n📚 {b['subject']}\n"
            f"📅 {b['date']} 🕒 {b['time_slot']} ({status_text})\n"
            + action_note
        )
        if can_reschedule:
            kb.add(legacy.Callback(
                f"🔄 Перенести: {tutor['name']} {b['date']} {b['time_slot']}",
                payload={"cmd": f"reschedule_student_{bid}"},
            ))
            kb.row()
        if can_act:
            kb.add(legacy.Callback(
                f"❌ Отменить: {tutor['name']} {b['date']} {b['time_slot']}",
                payload={"cmd": f"cancel_student_{bid}"},
            ))
            kb.row()
    kb.add(legacy.Callback("📊 Статистика", payload={"cmd": "student_stats"}))
    kb.row()
    kb.add(legacy.Callback("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    text = "\n".join(text_lines)
    if edit_event is not None:
        await legacy.edit_event_message(edit_event, text, keyboard=kb.get_json())
    else:
        await message.answer(text, keyboard=kb.get_json())


async def _compat_my_records(message):
    await legacy.state_dispenser.delete(message.from_id)
    await legacy._compat_render_my_records(message.from_id, message=message)


async def _compat_back_to_my_records(event):
    await legacy._compat_render_my_records(event.user_id, edit_event=event)


async def _compat_cancel_student_booking(event):
    bid = int(event.payload["cmd"].split("_")[2])
    booking = await _db.get_booking(bid)
    if not booking:
        await legacy.edit_event_message(event, "Запись не найдена.")
        return
    if not await legacy._require_vk_booking_owner(event, booking):
        return
    if booking["status"] == "paid":
        await legacy.edit_event_message(event, "Для отмены оплаченного занятия обратитесь в поддержку для возврата.")
        return
    if not _student_can_change(booking):
        await legacy.edit_event_message(event, "Эту запись уже нельзя отменить.")
        return

    changed, current = await _db.cancel_booking_record(
        bid,
        actor_type="student",
        actor_id=event.user_id,
        reason="Отменено учеником",
        expected_statuses={"pending", "confirmed"},
    )
    if not changed:
        await legacy.edit_event_message(event, "Статус записи уже изменился.")
        return
    await legacy.send_to_user(
        booking["user_id"], booking.get("user_platform", "vk"), "✅ Вы отменили занятие."
    )
    await legacy.send_to_tutor(
        booking["tutor_id"],
        f"❌ Ученик {booking['username']} отменил занятие:\n"
        f"📚 {booking['subject']}\n📅 {booking['date']} 🕒 {booking['time_slot']}",
    )
    kb = legacy.Keyboard(inline=True)
    kb.add(legacy.Callback("🔙 К моим записям", payload={"cmd": "back_to_my_records"}))
    cancellation_text = "✅ Запись отменена."
    if _is_trial(current):
        cancellation_text += (
            " Поскольку до начала оставалось не более 24 часов, пробное считается использованным."
            if current.get("trial_consumed")
            else " Пробное не израсходовано — вы сможете выбрать другое время."
        )
    await legacy.edit_event_message(event, cancellation_text, keyboard=kb.get_json())


async def _compat_student_reschedule_start(event):
    bid = int(event.payload["cmd"].split("_")[2])
    booking = await _db.get_booking(bid)
    if not booking:
        await legacy.edit_event_message(event, "Запись не найдена.")
        return
    if not await legacy._require_vk_booking_owner(event, booking):
        return
    if booking["status"] == "paid":
        await legacy.edit_event_message(event, "Оплаченное занятие переносится через поддержку.")
        return
    if _is_trial(booking):
        if (_booking_start(booking) - legacy.now_msk_naive()) <= timedelta(hours=24):
            await legacy.edit_event_message(
                event,
                "Пробное уже нельзя переносить менее чем за 24 часа. Его можно отменить, "
                "но оно будет считаться использованным."
            )
            return
    if not _student_can_change(booking):
        await legacy.edit_event_message(event, "Эту запись уже нельзя перенести.")
        return

    await legacy.state_dispenser.set(event.user_id, legacy.StudentRescheduleStates.waiting_date)
    await legacy.state_dispenser.update(
        event.user_id,
        old_booking_id=bid,
        tutor_id=booking["tutor_id"],
        subject=booking["subject"],
        old_date=booking["date"],
        old_time=booking["time_slot"],
        old_status=booking["status"],
        student_id=booking["user_id"],
        student_username=booking["username"],
        user_platform=booking.get("user_platform", "vk"),
    )
    dates = await legacy.get_available_dates(booking["tutor_id"])
    if not dates:
        await legacy.edit_event_message(event, "У преподавателя нет свободных дат для переноса.")
        return
    kb = legacy.Keyboard(inline=True)
    row = []
    for d in dates:
        dt_date = legacy.datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({legacy.WEEKDAY_NAMES[legacy.WEEKDAYS[dt_date.weekday()]]})"
        row.append(legacy.Callback(label, payload={"cmd": f"reschedule_date_{d}"}))
        if len(row) == 3:
            for btn in row:
                kb.add(btn)
            kb.row()
            row = []
    if row:
        for btn in row:
            kb.add(btn)
        kb.row()
    kb.add(legacy.Callback("🔙 Отмена", payload={"cmd": "back_to_menu"}))
    await legacy.edit_event_message(event, "Выберите новую дату:", keyboard=kb.get_json())


async def _reschedule_unpaid_in_place(booking_id: int, new_date: str, new_time: str, actor_id: int) -> bool:
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
                        "old_date": old["date"], "old_time": old["time_slot"],
                        "new_date": new_date, "new_time": new_time,
                        "payment_link_kept": bool(old["tinkoff_payment_id"]),
                    },
                )
        except _db.asyncpg.UniqueViolationError:
            return False
    await _db._sync_booking_record_safely(booking_id)
    return True


async def _compat_confirm_student_reschedule(event):
    data = await legacy.state_dispenser.get_data(event.user_id)
    bid = data["old_booking_id"]
    booking = await _db.get_booking(bid)
    if not booking or not await _db.account_owns_booking("vk", event.user_id, booking):
        await legacy.edit_event_message(event, "Запись не найдена.")
        return
    moved = await _reschedule_unpaid_in_place(
        bid, data["new_date"], data["new_time"], event.user_id
    )
    if not moved:
        await legacy.edit_event_message(event, "⚠️ Новый слот уже занят. Старая запись сохранена.")
        await legacy.state_dispenser.delete(event.user_id)
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
    await legacy.send_to_user(
        booking["user_id"],
        booking.get("user_platform", "vk"),
        f"✅ Занятие перенесено на {data['new_date']} {data['new_time']}.{payment_note}",
    )
    await legacy.edit_event_message(event, f"✅ Перенос выполнен.{payment_note}")
    await legacy.state_dispenser.delete(event.user_id)


# Функция my_records уже зарегистрирована декоратором при импорте legacy.
# Меняем её code object, поэтому даём legacy-модулю ссылку на самого себя,
# которую использует compatibility-реализация.
legacy.legacy = legacy
legacy._compat_render_my_records = _compat_render_my_records
legacy._student_can_change = _student_can_change
legacy.my_records.__code__ = _compat_my_records.__code__
legacy.back_to_my_records = _compat_back_to_my_records
legacy.cancel_student_booking = _compat_cancel_student_booking
legacy.student_reschedule_start = _compat_student_reschedule_start
legacy.confirm_student_reschedule = _compat_confirm_student_reschedule


# Старый VK-код остаётся неизменным, но все операции брони проходят через новый аудит.
legacy.update_booking = _contextual_update_booking
legacy.delete_booking = _contextual_delete_booking
legacy.mark_booking_paid_once = _db.mark_booking_paid_once
legacy.mark_booking_payment_failed = _db.mark_booking_payment_failed
legacy.reschedule_booking = _db.reschedule_booking
legacy.move_booking_in_place = _db.move_booking_in_place
legacy.cleanup_old_bookings = _db.cleanup_old_bookings
legacy.add_booking = _contextual_add_booking
legacy.get_booking = _db.get_booking
legacy.get_all_bookings = _db.get_all_bookings


async def main():
    return await legacy.main()


if __name__ == "__main__":
    legacy.logging.basicConfig(level=legacy.logging.INFO, stream=legacy.sys.stdout)
    legacy.asyncio.run(main())
