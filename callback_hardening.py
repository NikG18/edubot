"""Short, restart-tolerant callbacks for subject selection.

Legacy callbacks embed the full subject name and can exceed Telegram's 64-byte
callback_data limit.  New buttons carry only tutor id + subject index.  The
subject is resolved again from the current tutor record, so no ephemeral token
map is required and a bot restart does not invalidate the button.
"""

from __future__ import annotations


def _subject_by_index(tutor: dict | None, index: int) -> str | None:
    if not tutor:
        return None
    names = list((tutor.get("subjects") or {}).keys())
    return names[index] if 0 <= index < len(names) else None


def install_telegram_callback_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_short_subject_callbacks_installed", False):
        return

    async def make_subjects_keyboard(tutor_id: int, back_callback: str = "back_to_menu"):
        tutors = await legacy.get_all_tutors()
        tutor = tutors.get(int(tutor_id))
        if not tutor:
            return legacy.InlineKeyboardMarkup(inline_keyboard=[
                [legacy.InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)]
            ])
        buttons = []
        for index, subject in enumerate((tutor.get("subjects") or {}).keys()):
            buttons.append([
                legacy.InlineKeyboardButton(
                    text=subject,
                    callback_data=f"subjectid_{int(tutor_id)}_{index}",
                )
            ])
        buttons.append([
            legacy.InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data=back_callback)
        ])
        return legacy.InlineKeyboardMarkup(inline_keyboard=buttons)

    legacy.make_subjects_keyboard = make_subjects_keyboard
    app.make_subjects_keyboard = make_subjects_keyboard

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^subjectid_\d+_\d+$"))
    async def subject_by_index(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.safe_answer(call)
        try:
            _prefix, tutor_raw, index_raw = call.data.split("_", 2)
            tutor_id = int(tutor_raw)
            index = int(index_raw)
        except (TypeError, ValueError):
            await call.message.edit_text("Кнопка устарела. Начните запись заново.")
            await state.clear()
            return
        tutors = await legacy.get_all_tutors()
        subject = _subject_by_index(tutors.get(tutor_id), index)
        if subject is None:
            await call.message.edit_text("Предмет больше недоступен. Начните запись заново.")
            await state.clear()
            return
        await state.update_data(tutor_id=tutor_id, subject=subject)
        dates = await legacy.get_available_dates(tutor_id)
        if not dates:
            await call.message.edit_text("У преподавателя пока нет свободных дат.")
            return
        buttons = []
        row = []
        for date_str in dates:
            dt = legacy.datetime.strptime(date_str, "%d.%m.%Y")
            label = f"{date_str} ({legacy.WEEKDAY_NAMES[legacy.WEEKDAYS[dt.weekday()]]})"
            row.append(legacy.InlineKeyboardButton(text=label, callback_data=f"date_{date_str}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([legacy.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_booking_tutors")])
        await call.message.edit_text(
            f"Вы выбрали предмет: {subject}\nВыберите дату:",
            reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await state.set_state(legacy.BookingStates.waiting_date)

    async def show_manage_subjects_menu(update, state, tutor_id: int):
        tutors = await legacy.get_all_tutors()
        tutor = tutors.get(int(tutor_id))
        if not tutor:
            text = "Репетитор не найден."
            if isinstance(update, legacy.types.Message):
                await update.answer(text)
            else:
                await update.message.edit_text(text)
            return
        subjects = tutor.get("subjects") or {}
        text = "Предметы репетитора:\n" + "".join(
            f"• {name} — {price} руб.\n" for name, price in subjects.items()
        )
        buttons = [
            [legacy.InlineKeyboardButton(text=f"✏️ {name}", callback_data=f"editsubjid_{index}")]
            for index, name in enumerate(subjects.keys())
        ]
        buttons.extend([
            [legacy.InlineKeyboardButton(text="➕ Добавить предмет", callback_data="add_subject")],
            [legacy.InlineKeyboardButton(text="🔙 Назад к редактированию", callback_data="back_to_edit_tutor")],
        ])
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=buttons)
        if isinstance(update, legacy.types.Message):
            await update.answer(text, reply_markup=keyboard)
        else:
            await update.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(legacy.AdminStates.managing_subjects)

    legacy.show_manage_subjects_menu = show_manage_subjects_menu
    app.show_manage_subjects_menu = show_manage_subjects_menu

    @legacy.dp.callback_query(
        legacy.F.data.regexp(r"^editsubjid_\d+$"),
        legacy.StateFilter(legacy.AdminStates.managing_subjects),
    )
    async def edit_subject_by_index(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.safe_answer(call)
        data = await state.get_data()
        tutor_id = data.get("edit_tutor_id")
        if not tutor_id:
            await call.message.edit_text("Сессия редактирования устарела. Откройте репетитора заново.")
            await state.clear()
            return
        try:
            index = int(call.data.rsplit("_", 1)[1])
        except (TypeError, ValueError):
            await call.message.edit_text("Некорректная кнопка.")
            return
        tutors = await legacy.get_all_tutors()
        subject = _subject_by_index(tutors.get(int(tutor_id)), index)
        if subject is None:
            await call.message.edit_text("Предмет больше не найден. Откройте список заново.")
            return
        await state.update_data(edit_subject_name=subject)
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text="✏️ Изменить название", callback_data="editsubj_name")],
            [legacy.InlineKeyboardButton(text="💰 Изменить цену", callback_data="editsubj_price")],
            [legacy.InlineKeyboardButton(text="❌ Удалить предмет", callback_data="editsubj_delete")],
            [legacy.InlineKeyboardButton(text="🔙 Назад к списку предметов", callback_data="back_to_subjects_list")],
        ])
        await call.message.edit_text(f"Предмет: {subject}\nВыберите действие:", reply_markup=keyboard)
        await state.set_state(legacy.AdminStates.editing_subject_choice)

    legacy._short_subject_callbacks_installed = True
