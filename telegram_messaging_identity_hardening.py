"""Disambiguate Telegram/VK student identities in Telegram messaging callbacks.

Legacy callbacks carried only a numeric user id. Telegram and VK ids live in
separate namespaces, so equal numbers can point to different people. New callbacks
carry platform + user id; old callbacks are accepted only when active booking data
resolves them to exactly one platform.
"""

from __future__ import annotations

_ACTIVE = {"pending", "confirmed", "paid"}
_PLATFORM_ALIASES = {"tg": "telegram", "vk": "vk"}


async def _telegram_reply_button(call, state):
    await safe_answer(call)
    cmd = str(call.data or "")
    parts = cmd.split("_")
    explicit_platform = None
    expected_tutor_id = None
    try:
        if len(parts) == 4 and parts[0] == "reply" and parts[1] in _PLATFORM_ALIASES:
            explicit_platform = _PLATFORM_ALIASES[parts[1]]
            student_platform_id = int(parts[2])
            expected_tutor_id = int(parts[3])
        elif len(parts) == 2 and parts[0] == "reply":
            student_platform_id = int(parts[1])
        else:
            raise ValueError
    except (TypeError, ValueError):
        await safe_answer(call, "Некорректная или устаревшая кнопка ответа.", show_alert=True)
        return

    current_tutor = await get_tutor_by_telegram_id(call.from_user.id)
    is_admin = call.from_user.id == ADMING_ID
    if not current_tutor and not is_admin:
        await safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
        return

    if explicit_platform is not None:
        # New buttons are bound to the tutor they were generated for. Admin support
        # replies use a separate supportv2 callback and do not need this path.
        if not current_tutor or int(current_tutor) != int(expected_tutor_id):
            await safe_answer(call, "⛔ Эта кнопка предназначена другому преподавателю.", show_alert=True)
            return
        platform = explicit_platform
    else:
        bookings = await get_all_bookings()
        platforms = set()
        for row in bookings.values():
            if int(row.get("user_id") or 0) != student_platform_id:
                continue
            if row.get("status") not in ("pending", "confirmed", "paid"):
                continue
            if current_tutor and int(row.get("tutor_id") or 0) != int(current_tutor):
                continue
            platforms.add(str(row.get("user_platform") or "telegram").lower())
        if len(platforms) != 1:
            await safe_answer(
                call,
                "Старая кнопка ответа неоднозначна. Попросите ученика отправить сообщение заново.",
                show_alert=True,
            )
            return
        platform = next(iter(platforms))

    await state.update_data(
        reply_student_id=student_platform_id,
        reply_student_platform=platform,
    )
    await call.message.answer("Введите ваш ответ (текст):")
    await state.set_state(ContactStates.waiting_reply)


async def _telegram_tutor_contact_start(message, state):
    tutor_id = await get_tutor_by_telegram_id(message.from_user.id)
    if not tutor_id:
        await message.answer("Вы не зарегистрированы как преподаватель.")
        return

    bookings = await get_all_bookings()
    students = {}
    for row in bookings.values():
        if int(row.get("tutor_id") or 0) != int(tutor_id):
            continue
        if row.get("status") not in ("pending", "confirmed", "paid"):
            continue
        platform = str(row.get("user_platform") or "telegram").lower()
        short_platform = "tg" if platform == "telegram" else "vk"
        key = (short_platform, int(row["user_id"]))
        students.setdefault(key, row.get("username") or "Ученик")

    if not students:
        await message.answer("У вас пока нет учеников для связи.")
        return

    buttons = []
    for (short_platform, student_id), name in students.items():
        suffix = "TG" if short_platform == "tg" else "VK"
        buttons.append([
            InlineKeyboardButton(
                text=f"{name} · {suffix}",
                callback_data=f"tutor_contact_student_{short_platform}_{student_id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    await message.answer(
        "Выберите ученика:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(TutorContactStudentStates.choosing_student)


async def _telegram_tutor_contact_chosen(call, state):
    await safe_answer(call)
    cmd = str(call.data or "")
    suffix = cmd.removeprefix("tutor_contact_student_")
    parts = suffix.split("_")
    explicit_platform = None
    try:
        if len(parts) == 2 and parts[0] in _PLATFORM_ALIASES:
            explicit_platform = _PLATFORM_ALIASES[parts[0]]
            student_platform_id = int(parts[1])
        elif len(parts) == 1:
            student_platform_id = int(parts[0])
        else:
            raise ValueError
    except (TypeError, ValueError):
        await safe_answer(call, "Некорректная кнопка выбора ученика.", show_alert=True)
        return

    tutor_id = await get_tutor_by_telegram_id(call.from_user.id)
    if not tutor_id:
        await safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
        return

    bookings = await get_all_bookings()
    matches = {}
    for row in bookings.values():
        platform = str(row.get("user_platform") or "telegram").lower()
        if (
            int(row.get("tutor_id") or 0) == int(tutor_id)
            and int(row.get("user_id") or 0) == student_platform_id
            and row.get("status") in ("pending", "confirmed", "paid")
            and (explicit_platform is None or platform == explicit_platform)
        ):
            matches.setdefault(platform, row.get("username") or "Ученик")

    if explicit_platform is None:
        if len(matches) != 1:
            await safe_answer(
                call,
                "Старая кнопка неоднозначна. Откройте «Связь с учеником» заново.",
                show_alert=True,
            )
            return
        student_platform = next(iter(matches))
    else:
        if explicit_platform not in matches:
            await safe_answer(call, "⛔ Этот ученик не относится к вашим активным записям.", show_alert=True)
            return
        student_platform = explicit_platform

    student_username = matches.get(student_platform) or "Ученик"
    await state.update_data(
        tutor_contact_student_id=student_platform_id,
        tutor_contact_student_platform=student_platform,
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_tutor_msg_to_student")]
    ])
    await call.message.edit_text(
        f"Вы пишете ученику {student_username}. Введите сообщение:",
        reply_markup=keyboard,
    )
    await state.set_state(TutorContactStudentStates.waiting_message)


def _replace(target, replacement) -> None:
    if target.__code__.co_freevars or replacement.__code__.co_freevars:
        raise RuntimeError("Telegram messaging handler replacement cannot use closures")
    target.__code__ = replacement.__code__


def install_telegram_messaging_identity_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_telegram_messaging_identity_hardened", False):
        return
    legacy._PLATFORM_ALIASES = _PLATFORM_ALIASES
    _replace(legacy.process_reply_button, _telegram_reply_button)
    _replace(legacy.tutor_contact_student_start, _telegram_tutor_contact_start)
    _replace(legacy.tutor_contact_student_chosen, _telegram_tutor_contact_chosen)
    legacy._telegram_messaging_identity_hardened = True
