"""Server-side authorization for legacy VK messaging callbacks."""

from __future__ import annotations

import database as _db

_ACTIVE_CONTACT_STATUSES = {"pending", "confirmed", "paid"}


async def _tutor_has_student(tutor_id: int, student_platform_id: int) -> tuple[bool, str]:
    bookings = await _db.get_all_bookings()
    platform = "vk"
    allowed = False
    for row in bookings.values():
        if (
            int(row.get("tutor_id") or 0) == int(tutor_id)
            and int(row.get("user_id") or 0) == int(student_platform_id)
            and row.get("status") in _ACTIVE_CONTACT_STATUSES
        ):
            allowed = True
            platform = row.get("user_platform", "vk")
            break
    return allowed, platform


def install_vk_reply_authorization(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_vk_reply_authorization_installed", False):
        return

    async def authorized_reply(event):
        try:
            student_platform_id = int(event.payload["cmd"].split("_", 1)[1])
        except (KeyError, TypeError, ValueError, IndexError):
            await legacy.answer_event(event, "Некорректная кнопка ответа.", snackbar=True)
            return

        allowed = event.user_id == legacy.ADMIN_VK_ID
        platform = "vk"
        if not allowed:
            tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
            if tutor_id:
                allowed, platform = await _tutor_has_student(tutor_id, student_platform_id)
        else:
            bookings = await _db.get_all_bookings()
            for row in bookings.values():
                if int(row.get("user_id") or 0) == student_platform_id:
                    platform = row.get("user_platform", "vk")
                    break

        if not allowed:
            await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
            return

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
        try:
            student_platform_id = int(event.payload["cmd"].rsplit("_", 1)[1])
        except (KeyError, TypeError, ValueError, IndexError):
            await legacy.answer_event(event, "Некорректная кнопка выбора ученика.", snackbar=True)
            return

        tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
        if not tutor_id:
            await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
            return
        allowed, student_platform = await _tutor_has_student(tutor_id, student_platform_id)
        if not allowed:
            await legacy.answer_event(event, "Этот ученик недоступен для связи.", snackbar=True)
            return

        bookings = await _db.get_all_bookings()
        student_username = "Ученик"
        for row in bookings.values():
            if (
                int(row.get("tutor_id") or 0) == int(tutor_id)
                and int(row.get("user_id") or 0) == student_platform_id
                and row.get("status") in _ACTIVE_CONTACT_STATUSES
            ):
                student_username = row.get("username") or "Ученик"
                break

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

    # The universal VK callback dispatcher resolves these globals dynamically.
    legacy.process_reply_button = authorized_reply
    app.process_reply_button = authorized_reply
    legacy.tutor_contact_student_chosen = authorized_tutor_contact
    app.tutor_contact_student_chosen = authorized_tutor_contact
    legacy._vk_reply_authorization_installed = True
