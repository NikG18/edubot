"""Prevent stale VK callback buttons from crashing after process restarts.

VK's custom StateDispenser keeps payload data in process memory. Old callback
messages survive a restart, while that payload does not. The universal callback
dispatcher itself has no state filters, so helpers that index ``data[... ]`` can
otherwise raise KeyError. We wrap only the continuation steps that truly require
prior ephemeral state. Start steps that reconstruct context from the callback/DB
remain unchanged.
"""

from __future__ import annotations


_REQUIREMENTS = {
    # Trial booking continuation.
    "trial_subject_chosen": ("tutor_id",),
    "trial_date_chosen": ("tutor_id",),
    "back_to_trial_dates": ("tutor_id",),
    "trial_slot_chosen": ("tutor_id", "subject", "date"),
    "confirm_trial_booking": ("tutor_id", "tutor_name", "subject", "date", "time_slot"),
    # Regular booking continuation.
    "choose_date": ("tutor_id",),
    "back_to_date": ("tutor_id",),
    "choose_slot": ("tutor_id", "subject", "date"),
    "confirm_booking": ("tutor_id", "tutor_name", "subject", "date", "time_slot"),
    # Student reschedule continuation.
    "student_reschedule_date": ("tutor_id", "old_booking_id"),
    "back_to_reschedule_date": ("tutor_id", "old_booking_id"),
    "student_reschedule_slot": ("subject", "old_date", "old_time", "new_date"),
    "confirm_student_reschedule": (
        "old_booking_id", "tutor_id", "new_date", "new_time", "subject",
        "student_id", "student_username", "old_date", "old_time",
    ),
    # Tutor reschedule continuation.
    "tutor_reschedule_date": ("tutor_id", "old_booking_id"),
    "back_tutor_reschedule_date": ("tutor_id", "old_booking_id"),
    "tutor_reschedule_slot": ("student_username", "subject", "old_date", "old_time", "new_date"),
    "confirm_tutor_reschedule": (
        "old_booking_id", "tutor_id", "new_date", "new_time", "subject",
        "student_id", "student_username",
    ),
    # Tutor schedule continuation. schedule_main itself rebuilds tid safely.
    "edit_day": ("tid",),
    "back_to_schedule": ("tid",),
    "handle_block_day": ("tid",),
    "handle_unblock_day": ("tid",),
    "add_slot_start": ("tid", "current_day"),
    "add_range_start": ("tid", "current_day"),
    "range_duration_chosen": ("tid", "current_day"),
    "range_break_back": ("tid", "current_day"),
    "range_break_chosen": ("tid", "current_day", "range_duration"),
    "del_slot_start": ("tid", "current_day"),
    "confirm_del_slot": ("tid", "current_day"),
}


async def _fresh_start(message):
    """Make VK 'Начать' a true restart of the current user flow."""
    user_id = message.from_id
    await state_dispenser.delete(user_id)
    await message.answer(
        "👋 Добро пожаловать! Выберите действие в меню.",
        keyboard=await get_main_menu(user_id),
    )


async def _recover(legacy, event) -> None:
    await legacy.state_dispenser.delete(event.user_id)
    text = "Эта кнопка относится к сессии до перезапуска бота. Откройте нужный раздел заново."
    try:
        await legacy.edit_event_message(
            event,
            text,
            keyboard=await legacy.get_main_menu(event.user_id),
        )
    except Exception:
        legacy.logging.exception("Не удалось отредактировать устаревшее VK callback-сообщение")
        try:
            await legacy.bot.api.messages.send(
                user_id=event.user_id,
                message=text,
                keyboard=await legacy.get_main_menu(event.user_id),
                random_id=0,
            )
        except Exception:
            legacy.logging.exception("Не удалось отправить VK recovery-сообщение")


def install_vk_restart_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_vk_restart_state_guards_installed", False):
        return

    # Preserve the function object already registered by vkbottle, but replace
    # its code with a closure-free implementation that clears all in-memory FSM
    # state before showing the main menu.
    if getattr(legacy, "start_handler", None) is not None:
        legacy.start_handler.__code__ = _fresh_start.__code__

    for name, required in _REQUIREMENTS.items():
        original = getattr(legacy, name, None)
        if original is None:
            continue

        def build_guard(func, keys):
            async def guarded(event):
                data = await legacy.state_dispenser.get_data(event.user_id)
                if any(data.get(key) is None for key in keys):
                    await _recover(legacy, event)
                    return None
                return await func(event)
            return guarded

        setattr(legacy, name, build_guard(original, tuple(required)))

    legacy._vk_restart_state_guards_installed = True
