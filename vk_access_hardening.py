"""Server-side authorization for legacy VK reply callbacks."""

from __future__ import annotations

import database as _db


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
        if not allowed:
            tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
            if tutor_id:
                bookings = await _db.get_all_bookings()
                allowed = any(
                    int(row.get("tutor_id") or 0) == int(tutor_id)
                    and int(row.get("user_id") or 0) == student_platform_id
                    for row in bookings.values()
                )
        if not allowed:
            await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
            return

        platform = "vk"
        bookings = await _db.get_all_bookings()
        for row in bookings.values():
            if int(row.get("user_id") or 0) == student_platform_id:
                platform = row.get("user_platform", "vk")
                break
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

    # The universal VK callback dispatcher resolves this global dynamically.
    legacy.process_reply_button = authorized_reply
    app.process_reply_button = authorized_reply
    legacy._vk_reply_authorization_installed = True
