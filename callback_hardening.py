"""Short, restart-tolerant callbacks for subject selection.

Legacy callbacks embed the full subject name and can exceed Telegram's 64-byte
callback_data limit. New buttons carry only tutor id + subject index. The subject
is resolved again from the current tutor record, so no ephemeral token map is
required and a bot restart does not invalidate the button itself.
"""

from __future__ import annotations


def _subject_by_index(tutor: dict | None, index: int) -> str | None:
    if not tutor:
        return None
    names = list((tutor.get("subjects") or {}).keys())
    return names[index] if 0 <= index < len(names) else None


async def _subscription_tutor_short(call, state):
    """Replacement code for the already-registered buy_tutor handler."""
    await safe_answer(call)
    try:
        tid = int(call.data.split("_")[2])
    except (TypeError, ValueError, IndexError):
        await state.clear()
        await call.message.edit_text("Кнопка устарела. Начните покупку абонемента заново.")
        return
    await state.update_data(buy_tutor_id=tid)
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await state.clear()
        await call.message.edit_text("Репетитор не найден.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for index, (subj, price) in enumerate(tutor["subjects"].items()):
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{subj} ({price} руб.)",
                callback_data=f"buysubjid_{tid}_{index}",
            )
        ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_buy_tutors")
    ])
    await call.message.edit_text(
        f"Выберите предмет для абонемента у {tutor['name']}:",
        reply_markup=keyboard,
    )
    await state.set_state(BuySubscriptionStates.choosing_subject)


async def _subscription_back_subjects_short(call, state):
    """Replacement for the old fiscal navigation patch using short callbacks."""
    await safe_answer(call)
    data = await state.get_data()
    tid = data.get("buy_tutor_id")
    if not tid:
        await state.clear()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К оплате", callback_data="back_to_payment_menu")]
        ])
        await call.message.edit_text(
            "Данные выбора абонемента потеряны. Выберите репетитора заново.",
            reply_markup=keyboard,
        )
        return
    tutors = await get_all_tutors()
    tutor = tutors.get(int(tid))
    if not tutor:
        await state.clear()
        await call.message.edit_text(
            "Репетитор не найден.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К оплате", callback_data="back_to_payment_menu")]
            ]),
        )
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for index, (subj, price) in enumerate(tutor["subjects"].items()):
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{subj} ({price} руб.)",
                callback_data=f"buysubjid_{int(tid)}_{index}",
            )
        ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_buy_tutors")
    ])
    await call.message.edit_text(
        f"Выберите предмет для абонемента у {tutor['name']}:",
        reply_markup=keyboard,
    )
    await state.set_state(BuySubscriptionStates.choosing_subject)


def _replace_registered_code(target, replacement) -> None:
    if target.__code__.co_freevars or replacement.__code__.co_freevars:
        raise RuntimeError("registered handler replacement must not use closure variables")
    target.__code__ = replacement.__code__


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

    async def continue_trial_booking(call, state, tutor_id: int):
        """Legacy trial flow with short subject callbacks and the same eligibility checks."""
        tutors = await legacy.get_all_tutors()
        tutor = tutors.get(int(tutor_id))
        if not tutor:
            if call.message.content_type != "text":
                await call.message.delete()
            await call.message.answer("Репетитор не найден.")
            await state.clear()
            return

        email = await legacy.get_student_email("telegram", call.from_user.id)
        if not email:
            await state.update_data(tutor_id=int(tutor_id), tutor_name=tutor["name"])
            await state.set_state(legacy.TrialBookingStates.waiting_email)
            text = (
                "Введите email для учёта бесплатных пробных занятий. "
                "Он не связывает ваши аккаунты и не открывает доступ к данным; "
                "используется только для ограничения повторных пробных и связи с поддержкой."
            )
            if call.message.content_type != "text":
                await call.message.delete()
                await call.message.answer(text)
            else:
                await call.message.edit_text(text)
            return

        if not await legacy.is_trial_available("telegram", call.from_user.id, int(tutor_id), email):
            await call.message.edit_text(
                "Бесплатное пробное занятие у этого репетитора уже использовано "
                "или сейчас ожидает проведения."
            )
            await state.clear()
            return

        await state.update_data(
            tutor_id=int(tutor_id),
            tutor_name=tutor["name"],
            trial_email=email,
        )
        subjects = list((tutor.get("subjects") or {}).keys())
        if len(subjects) == 1:
            await state.update_data(subject=subjects[0])
            if call.message.content_type != "text":
                await call.message.delete()
                await call.message.answer(
                    "Ищем доступные слоты на ближайшие 7 дней...",
                    reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=[
                        [legacy.InlineKeyboardButton(
                            text="▶️ Продолжить",
                            callback_data=f"trial_proceed_{int(tutor_id)}",
                        )]
                    ]),
                )
                return
            await call.message.edit_text("Ищем доступные слоты на ближайшие 7 дней...")
            await legacy.show_trial_dates(call, state, int(tutor_id))
            return
        if len(subjects) > 1:
            await state.set_state(legacy.TrialBookingStates.choosing_subject)
            keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
                [legacy.InlineKeyboardButton(
                    text=subject,
                    callback_data=f"trialsubjid_{int(tutor_id)}_{index}",
                )]
                for index, subject in enumerate(subjects)
            ] + [[legacy.InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_tutors")]])
            if call.message.content_type != "text":
                await call.message.delete()
                await call.message.answer("Выберите предмет для пробного занятия:", reply_markup=keyboard)
            else:
                await call.message.edit_text("Выберите предмет для пробного занятия:", reply_markup=keyboard)
            return
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
        ])
        if call.message.content_type != "text":
            await call.message.delete()
            await call.message.answer("У этого репетитора пока нет предметов.", reply_markup=keyboard)
        else:
            await call.message.edit_text("У этого репетитора пока нет предметов.", reply_markup=keyboard)
        await state.clear()

    legacy._continue_trial_booking = continue_trial_booking

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^trialsubjid_\d+_\d+$"))
    async def trial_subject_by_index(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.safe_answer(call)
        try:
            _prefix, tutor_raw, index_raw = call.data.split("_", 2)
            tutor_id = int(tutor_raw)
            index = int(index_raw)
        except (TypeError, ValueError):
            await state.clear()
            await call.message.edit_text("Кнопка пробного занятия устарела. Начните запись заново.")
            return
        tutors = await legacy.get_all_tutors()
        tutor = tutors.get(tutor_id)
        subject = _subject_by_index(tutor, index)
        if subject is None:
            await state.clear()
            await call.message.edit_text("Предмет больше недоступен. Начните пробное занятие заново.")
            return
        email = await legacy.get_student_email("telegram", call.from_user.id)
        if not email or not await legacy.is_trial_available("telegram", call.from_user.id, tutor_id, email):
            await state.clear()
            await call.message.edit_text(
                "Данные пробного занятия устарели или пробное уже использовано. Откройте анкету преподавателя заново."
            )
            return
        await state.update_data(
            tutor_id=tutor_id,
            tutor_name=tutor["name"],
            trial_email=email,
            subject=subject,
        )
        if call.message.content_type != "text":
            await call.message.delete()
            await call.message.answer("Ищем доступные слоты на ближайшие 7 дней...")
            return
        await call.message.edit_text("Ищем доступные слоты на ближайшие 7 дней...")
        await legacy.show_trial_dates(call, state, tutor_id)

    if hasattr(legacy, "buy_subscription_tutor"):
        _replace_registered_code(legacy.buy_subscription_tutor, _subscription_tutor_short)
    if hasattr(legacy, "back_to_buy_subjects"):
        _replace_registered_code(legacy.back_to_buy_subjects, _subscription_back_subjects_short)

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^buysubjid_\d+_\d+$"))
    async def subscription_subject_by_index(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.safe_answer(call)
        try:
            _prefix, tutor_raw, index_raw = call.data.split("_", 2)
            tutor_id = int(tutor_raw)
            index = int(index_raw)
        except (TypeError, ValueError):
            await state.clear()
            await call.message.edit_text("Кнопка абонемента устарела. Начните покупку заново.")
            return
        tutors = await legacy.get_all_tutors()
        tutor = tutors.get(tutor_id)
        subject = _subject_by_index(tutor, index)
        if subject is None:
            await state.clear()
            await call.message.edit_text("Предмет больше недоступен. Начните покупку абонемента заново.")
            return
        price = tutor["subjects"][subject]
        await state.update_data(buy_tutor_id=tutor_id, buy_subject=subject)
        buttons = []
        for count, discount in ((12, 5), (24, 10), (36, 15)):
            total = price * count * (1 - discount / 100)
            buttons.append([legacy.InlineKeyboardButton(
                text=f"{count} занятий — скидка {discount}% (итого {total:.0f} руб.)",
                callback_data=f"buy_package_{count}",
            )])
        buttons.append([legacy.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_buy_subjects")])
        await call.message.edit_text(
            f"Выберите пакет занятий по предмету «{subject}»:",
            reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await state.set_state(legacy.BuySubscriptionStates.choosing_package)

    @legacy.dp.callback_query(legacy.F.data.startswith("buy_tutor_"))
    async def subscription_tutor_restart_fallback(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.buy_subscription_tutor(call, state)

    @legacy.dp.callback_query(legacy.F.data == "back_to_buy_subjects")
    async def subscription_back_restart_fallback(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.back_to_buy_subjects(call, state)

    @legacy.dp.callback_query(legacy.F.data.startswith("buy_package_"))
    async def subscription_package_restart_fallback(call: legacy.CallbackQuery, state: legacy.FSMContext):
        data = await state.get_data()
        if data.get("buy_tutor_id") and data.get("buy_subject"):
            return
        await legacy.safe_answer(call)
        await state.clear()
        await call.message.edit_text(
            "Сессия покупки абонемента устарела после перезапуска. Начните покупку заново.",
            reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=[
                [legacy.InlineKeyboardButton(text="🔙 К оплате", callback_data="back_to_payment_menu")]
            ]),
        )

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
