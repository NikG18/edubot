"""Subject-first regular booking for Telegram and VK.

The legacy flow asks for a tutor first and a subject second.  This module keeps
the existing date/time/confirmation handlers, but replaces only the beginning
of the regular booking flow with:

    subject -> tutors teaching that subject -> date -> time

Callback payloads contain numeric indexes instead of subject names, so long or
non-ASCII subject names cannot exceed Telegram/VK callback limits.
"""

from __future__ import annotations

from collections.abc import Mapping


_telegram_legacy = None
_vk_legacy = None


def build_subject_catalog(tutors: Mapping) -> list[dict]:
    """Return a stable, case-insensitive subject catalog.

    Each result contains the display name and a mapping of tutor id to the
    tutor's exact subject spelling.  The exact spelling is saved in a booking,
    while visually equivalent entries such as ``"Химия"`` and ``" химия "``
    are shown to the student only once.
    """

    grouped: dict[str, dict] = {}
    for raw_tid, tutor in tutors.items():
        try:
            tutor_id = int(raw_tid)
        except (TypeError, ValueError):
            continue
        subjects = (tutor or {}).get("subjects") or {}
        for raw_subject in subjects:
            exact_subject = str(raw_subject)
            display_name = " ".join(exact_subject.split())
            if not display_name:
                continue
            key = display_name.casefold()
            item = grouped.setdefault(
                key,
                {"name": display_name, "tutors": {}},
            )
            item["tutors"][tutor_id] = exact_subject

    return sorted(grouped.values(), key=lambda item: item["name"].casefold())


def catalog_item(catalog: list[dict], raw_index) -> tuple[int, dict] | None:
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return None
    if index < 0 or index >= len(catalog):
        return None
    return index, catalog[index]


def _tutor_by_id(tutors: Mapping, tutor_id: int):
    return tutors.get(tutor_id) or tutors.get(str(tutor_id))


async def _tg_subject_menu():
    legacy = _telegram_legacy
    tutors = await legacy.get_all_tutors()
    catalog = build_subject_catalog(tutors)
    buttons = [
        [legacy.InlineKeyboardButton(
            text=item["name"],
            callback_data=f"booksub_{index}",
        )]
        for index, item in enumerate(catalog)
    ]
    buttons.append([
        legacy.InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")
    ])
    return catalog, legacy.InlineKeyboardMarkup(inline_keyboard=buttons)


async def _tg_start_from_message(message, state):
    legacy = _telegram_legacy
    await state.clear()
    await message.answer("Переходим в раздел...", reply_markup=legacy.ReplyKeyboardRemove())
    catalog, keyboard = await _tg_subject_menu()
    text = "Выберите предмет:" if catalog else "Пока нет доступных предметов."
    await message.answer(text, reply_markup=keyboard)


async def _tg_start_from_callback(call, state):
    legacy = _telegram_legacy
    await legacy.safe_answer(call)
    await state.clear()
    catalog, keyboard = await _tg_subject_menu()
    text = "Выберите предмет:" if catalog else "Пока нет доступных предметов."
    try:
        await call.message.edit_text(text, reply_markup=keyboard)
    except legacy.TelegramBadRequest:
        try:
            await call.message.delete()
        except legacy.TelegramBadRequest:
            pass
        await call.message.answer(text, reply_markup=keyboard)


async def _tg_tutors_for_subject(call, state, raw_index):
    legacy = _telegram_legacy
    tutors = await legacy.get_all_tutors()
    catalog = build_subject_catalog(tutors)
    resolved = catalog_item(catalog, raw_index)
    if resolved is None:
        await state.clear()
        await call.message.edit_text(
            "Список предметов изменился. Выберите предмет заново.",
            reply_markup=(await _tg_subject_menu())[1],
        )
        return

    index, item = resolved
    tutor_rows = []
    for tutor_id in item["tutors"]:
        tutor = _tutor_by_id(tutors, tutor_id)
        if not tutor:
            continue
        tutor_rows.append((str(tutor.get("name") or "Репетитор"), tutor_id))
    tutor_rows.sort(key=lambda row: row[0].casefold())
    buttons = [
        [legacy.InlineKeyboardButton(
            text=name,
            callback_data=f"booktutor_{tutor_id}_{index}",
        )]
        for name, tutor_id in tutor_rows
    ]
    buttons.append([
        legacy.InlineKeyboardButton(text="🔙 К предметам", callback_data="book_back_subjects")
    ])
    await state.update_data(booking_subject_index=index, subject=item["name"])
    await call.message.edit_text(
        f"Предмет: {item['name']}\nВыберите преподавателя:",
        reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def _tg_choose_tutor(call, state, raw_tutor_id, raw_index):
    legacy = _telegram_legacy
    tutors = await legacy.get_all_tutors()
    catalog = build_subject_catalog(tutors)
    resolved = catalog_item(catalog, raw_index)
    try:
        tutor_id = int(raw_tutor_id)
    except (TypeError, ValueError):
        tutor_id = 0
    tutor = _tutor_by_id(tutors, tutor_id)
    if resolved is None or tutor is None:
        await state.clear()
        await call.message.edit_text("Выбор устарел. Начните запись заново.")
        return

    index, item = resolved
    exact_subject = item["tutors"].get(tutor_id)
    if exact_subject is None:
        await _tg_tutors_for_subject(call, state, index)
        return

    await state.update_data(
        booking_subject_index=index,
        tutor_id=tutor_id,
        tutor_name=tutor.get("name") or "Репетитор",
        subject=exact_subject,
    )
    dates = await legacy.get_available_dates(tutor_id)
    if not dates:
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(
                text="🔙 Назад к преподавателям",
                callback_data=f"booksub_{index}",
            )]
        ])
        await call.message.edit_text(
            "У этого преподавателя пока нет свободных дат. "
            "Попробуйте позже или выберите другого преподавателя.",
            reply_markup=keyboard,
        )
        return

    buttons = []
    for date_string in dates:
        date_value = legacy.datetime.strptime(date_string, "%d.%m.%Y")
        label = (
            f"{date_string} "
            f"({legacy.WEEKDAY_NAMES[legacy.WEEKDAYS[date_value.weekday()]]})"
        )
        buttons.append([
            legacy.InlineKeyboardButton(text=label, callback_data=f"date_{date_string}")
        ])
    buttons.append([
        legacy.InlineKeyboardButton(
            text="🔙 Назад к преподавателям",
            callback_data="book_back_tutors",
        )
    ])
    await call.message.edit_text(
        f"{item['name']} · {tutor.get('name') or 'Репетитор'}\nВыберите дату:",
        reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(legacy.BookingStates.waiting_date)


def install_telegram_subject_booking(app) -> None:
    global _telegram_legacy
    legacy = app.legacy
    if getattr(legacy, "_subject_first_booking_tg_installed", False):
        return
    _telegram_legacy = legacy

    # legal_telegram delegates its privacy-gated entry points to these helpers.
    legacy._subject_booking_start_message = _tg_start_from_message
    legacy._subject_booking_start_callback = _tg_start_from_callback

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^booksub_\d+$"))
    async def select_subject(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.safe_answer(call)
        await _tg_tutors_for_subject(call, state, call.data.rsplit("_", 1)[1])

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^booktutor_\d+_\d+$"))
    async def select_tutor(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.safe_answer(call)
        _, tutor_id, subject_index = call.data.split("_", 2)
        await _tg_choose_tutor(call, state, tutor_id, subject_index)

    @legacy.dp.callback_query(legacy.F.data == "book_back_subjects")
    async def back_to_subjects(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await _tg_start_from_callback(call, state)

    @legacy.dp.callback_query(legacy.F.data == "book_back_tutors")
    async def back_to_tutors(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.safe_answer(call)
        data = await state.get_data()
        subject_index = data.get("booking_subject_index")
        if subject_index is None:
            await _tg_start_from_callback(call, state)
            return
        await _tg_tutors_for_subject(call, state, subject_index)

    legacy._subject_first_booking_tg_installed = True


async def _vk_subject_menu():
    legacy = _vk_legacy
    tutors = await legacy.get_all_tutors()
    catalog = build_subject_catalog(tutors)
    keyboard = legacy.Keyboard(inline=True)
    for index, item in enumerate(catalog):
        keyboard.add(legacy.Callback(item["name"], payload={"cmd": f"booksub_{index}"}))
        keyboard.row()
    keyboard.add(legacy.Callback("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    return catalog, keyboard.get_json()


async def _vk_start_from_message(message):
    legacy = _vk_legacy
    await legacy.state_dispenser.delete(message.from_id)
    catalog, keyboard = await _vk_subject_menu()
    text = "Выберите предмет:" if catalog else "Пока нет доступных предметов."
    await message.answer(text, keyboard=keyboard)


async def _vk_start_from_event(event):
    legacy = _vk_legacy
    await legacy.state_dispenser.delete(event.user_id)
    catalog, keyboard = await _vk_subject_menu()
    text = "Выберите предмет:" if catalog else "Пока нет доступных предметов."
    await legacy.edit_event_message(event, text, keyboard=keyboard)


async def _vk_tutors_for_subject(event, raw_index):
    legacy = _vk_legacy
    tutors = await legacy.get_all_tutors()
    catalog = build_subject_catalog(tutors)
    resolved = catalog_item(catalog, raw_index)
    if resolved is None:
        await _vk_start_from_event(event)
        return
    index, item = resolved
    tutor_rows = []
    for tutor_id in item["tutors"]:
        tutor = _tutor_by_id(tutors, tutor_id)
        if tutor:
            tutor_rows.append((str(tutor.get("name") or "Репетитор"), tutor_id))
    tutor_rows.sort(key=lambda row: row[0].casefold())
    keyboard = legacy.Keyboard(inline=True)
    for name, tutor_id in tutor_rows:
        keyboard.add(legacy.Callback(
            name,
            payload={"cmd": f"booktutor_{tutor_id}_{index}"},
        ))
        keyboard.row()
    keyboard.add(legacy.Callback("🔙 К предметам", payload={"cmd": "book_back_subjects"}))
    await legacy.state_dispenser.set(event.user_id, legacy.BookingStates.choosing_subject)
    await legacy.state_dispenser.update(
        event.user_id,
        booking_subject_index=index,
        subject=item["name"],
    )
    await legacy.edit_event_message(
        event,
        f"Предмет: {item['name']}\nВыберите преподавателя:",
        keyboard=keyboard.get_json(),
    )


async def _vk_choose_tutor(event, raw_tutor_id, raw_index):
    legacy = _vk_legacy
    tutors = await legacy.get_all_tutors()
    catalog = build_subject_catalog(tutors)
    resolved = catalog_item(catalog, raw_index)
    try:
        tutor_id = int(raw_tutor_id)
    except (TypeError, ValueError):
        tutor_id = 0
    tutor = _tutor_by_id(tutors, tutor_id)
    if resolved is None or tutor is None:
        await legacy.edit_event_message(event, "Выбор устарел. Начните запись заново.")
        await legacy.state_dispenser.delete(event.user_id)
        return
    index, item = resolved
    exact_subject = item["tutors"].get(tutor_id)
    if exact_subject is None:
        await _vk_tutors_for_subject(event, index)
        return
    await legacy.state_dispenser.update(
        event.user_id,
        booking_subject_index=index,
        tutor_id=tutor_id,
        tutor_name=tutor.get("name") or "Репетитор",
        subject=exact_subject,
    )
    dates = await legacy.get_available_dates(tutor_id)
    if not dates:
        keyboard = legacy.Keyboard(inline=True)
        keyboard.add(legacy.Callback(
            "🔙 Назад к преподавателям",
            payload={"cmd": f"booksub_{index}"},
        ))
        await legacy.edit_event_message(
            event,
            "У этого преподавателя пока нет свободных дат. "
            "Попробуйте позже или выберите другого преподавателя.",
            keyboard=keyboard.get_json(),
        )
        return

    keyboard = legacy.Keyboard(inline=True)
    row = []
    for date_string in dates:
        date_value = legacy.datetime.strptime(date_string, "%d.%m.%Y")
        label = (
            f"{date_string} "
            f"({legacy.WEEKDAY_NAMES[legacy.WEEKDAYS[date_value.weekday()]]})"
        )
        row.append(legacy.Callback(label, payload={"cmd": f"date_{date_string}"}))
        if len(row) == 3:
            for button in row:
                keyboard.add(button)
            keyboard.row()
            row = []
    if row:
        for button in row:
            keyboard.add(button)
        keyboard.row()
    keyboard.add(legacy.Callback(
        "🔙 Назад к преподавателям",
        payload={"cmd": "book_back_tutors"},
    ))
    await legacy.edit_event_message(
        event,
        f"{item['name']} · {tutor.get('name') or 'Репетитор'}\nВыберите дату:",
        keyboard=keyboard.get_json(),
    )
    await legacy.state_dispenser.set(event.user_id, legacy.BookingStates.waiting_date)


async def _vk_select_subject(event):
    await _vk_tutors_for_subject(event, event.payload.get("cmd", "").rsplit("_", 1)[-1])


async def _vk_select_tutor(event):
    parts = event.payload.get("cmd", "").split("_", 2)
    if len(parts) != 3:
        await _vk_start_from_event(event)
        return
    await _vk_choose_tutor(event, parts[1], parts[2])


async def _vk_back_to_tutors(event):
    legacy = _vk_legacy
    data = await legacy.state_dispenser.get_data(event.user_id)
    subject_index = data.get("booking_subject_index")
    if subject_index is None:
        await _vk_start_from_event(event)
        return
    await _vk_tutors_for_subject(event, subject_index)


def install_vk_subject_booking(app) -> None:
    global _vk_legacy
    legacy = app.legacy
    if getattr(legacy, "_subject_first_booking_vk_installed", False):
        return
    _vk_legacy = legacy
    legacy._subject_booking_start_message = _vk_start_from_message
    legacy._subject_booking_start_event = _vk_start_from_event
    legacy.booking_subject_chosen = _vk_select_subject
    legacy.booking_tutor_chosen = _vk_select_tutor
    legacy.booking_back_to_subjects = _vk_start_from_event
    legacy.booking_back_to_tutors = _vk_back_to_tutors
    legacy._subject_first_booking_vk_installed = True
