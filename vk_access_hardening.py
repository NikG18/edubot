"""Server-side authorization for VK messaging callbacks.

New messaging callbacks carry the source platform and tutor id. Legacy callbacks
remain usable only when their raw numeric student id resolves unambiguously.
Administrative reply rights are not granted in VK; a user may reply here only in
an actual tutor role.
"""

from __future__ import annotations

import database as _db

_ACTIVE_CONTACT_STATUSES = {"pending", "confirmed", "paid"}
_PLATFORM_ALIASES = {"tg": "telegram", "vk": "vk"}


async def _matching_accounts(tutor_id: int, student_platform_id: int, platform: str | None = None):
    bookings = await _db.get_all_bookings()
    matches: dict[str, str] = {}
    for row in bookings.values():
        row_platform = str(row.get("user_platform") or "vk").lower()
        if (
            int(row.get("tutor_id") or 0) == int(tutor_id)
            and int(row.get("user_id") or 0) == int(student_platform_id)
            and row.get("status") in _ACTIVE_CONTACT_STATUSES
            and (platform is None or row_platform == platform)
        ):
            matches.setdefault(row_platform, row.get("username") or "Ученик")
    return matches


async def _vk_tutor_contact_start(message):
    tutor_id = await get_tutor_by_vk_id(message.from_id)
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
        platform = str(row.get("user_platform") or "vk").lower()
        short_platform = "tg" if platform == "telegram" else "vk"
        key = (short_platform, int(row["user_id"]))
        students.setdefault(key, row.get("username") or "Ученик")

    if not students:
        await message.answer("У вас пока нет учеников для связи.")
        return

    kb = Keyboard(inline=True)
    for (short_platform, student_id), name in students.items():
        suffix = "TG" if short_platform == "tg" else "VK"
        kb.add(Callback(
            f"{name} · {suffix}",
            payload={"cmd": f"tutor_contact_student_{short_platform}_{student_id}"},
        ))
        kb.row()
    kb.add(Callback("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await message.answer("Выберите ученика:", keyboard=kb.get_json())
    await state_dispenser.set(message.from_id, TutorContactStudentStates.choosing_student)


def install_vk_reply_authorization(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_vk_reply_authorization_installed", False):
        return

    async def authorized_reply(event):
        cmd = str((event.payload or {}).get("cmd") or "")
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
            await legacy.answer_event(event, "Некорректная или устаревшая кнопка ответа.", snackbar=True)
            return

        tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
        if not tutor_id:
            await legacy.answer_event(event, "Ответы из VK доступны только преподавателю.", snackbar=True)
            return
        if expected_tutor_id is not None and int(tutor_id) != expected_tutor_id:
            await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
            return

        if explicit_platform is not None:
            # The v2 button itself was created for this tutor and student account.
            platform = explicit_platform
        else:
            # Old reply_<id> buttons have no platform. Use them only when active
            # booking history identifies exactly one messenger namespace.
            matches = await _matching_accounts(tutor_id, student_platform_id)
            if len(matches) != 1:
                await legacy.answer_event(
                    event,
                    "Старая кнопка ответа неоднозначна. Попросите ученика отправить сообщение заново.",
                    snackbar=True,
                )
                return
            platform = next(iter(matches))

        await legacy.state_dispenser.update(
            event.user_id,
            reply_student_id=student_platform_id,
            reply_student_platform=platform,
        )
        await legacy.bot.api.messages.send(
            user_id=event.user_id,
            message="Введите ваш ответ (текст):",
            random_id=legacy.random.randint(1, 2**31 - 1),
        )
        await legacy.state_dispenser.set(event.user_id, legacy.ContactStates.waiting_reply)

    async def authorized_tutor_contact(event):
        cmd = str((event.payload or {}).get("cmd") or "")
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
            await legacy.answer_event(event, "Некорректная кнопка выбора ученика.", snackbar=True)
            return

        tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
        if not tutor_id:
            await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
            return

        matches = await _matching_accounts(tutor_id, student_platform_id, explicit_platform)
        if explicit_platform is None:
            if len(matches) != 1:
                await legacy.answer_event(
                    event,
                    "Старая кнопка неоднозначна. Откройте «Связь с учеником» заново.",
                    snackbar=True,
                )
                return
            student_platform = next(iter(matches))
        else:
            if explicit_platform not in matches:
                await legacy.answer_event(event, "Этот ученик недоступен для связи.", snackbar=True)
                return
            student_platform = explicit_platform
        student_username = matches.get(student_platform) or "Ученик"

        await legacy.state_dispenser.update(
            event.user_id,
            tutor_contact_student_id=student_platform_id,
            tutor_contact_student_platform=student_platform,
        )
        kb = legacy.Keyboard(inline=True)
        kb.add(legacy.Callback("❌ Отмена", payload={"cmd": "cancel_tutor_msg_to_student"}))
        await legacy.edit_event_message(
            event,
            f"Вы пишете ученику {student_username}. Введите сообщение:",
            keyboard=kb.get_json(),
        )
        await legacy.state_dispenser.set(
            event.user_id,
            legacy.TutorContactStudentStates.waiting_message,
        )

    # The start handler is already registered by vkbottle, so replace its code object.
    target_start = getattr(legacy, "tutor_contact_student_start", None)
    if target_start is not None:
        if target_start.__code__.co_freevars or _vk_tutor_contact_start.__code__.co_freevars:
            raise RuntimeError("VK tutor-contact replacement cannot use closures")
        target_start.__code__ = _vk_tutor_contact_start.__code__

    # The universal VK callback dispatcher resolves these globals dynamically.
    legacy.process_reply_button = authorized_reply
    app.process_reply_button = authorized_reply
    legacy.tutor_contact_student_chosen = authorized_tutor_contact
    app.tutor_contact_student_chosen = authorized_tutor_contact
    legacy._vk_reply_authorization_installed = True
