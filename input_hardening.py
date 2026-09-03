"""Input guards for legacy FSM handlers.

The old aiogram handlers frequently call ``message.text.strip()`` directly. A
sticker, voice note, document or photo can therefore produce ``None`` and crash a
state handler. We guard this once at the dispatcher boundary instead of editing
dozens of legacy handlers. The only text-state exception is the admin tutor-photo
step, which intentionally accepts a photo.

Subject names are also bounded by UTF-8 byte length. New primary/admin subject
buttons use short IDs, but some legacy trial/subscription messages still embed the
subject in callback_data. Forty bytes keeps those payloads safely below Telegram's
64-byte limit while existing database values are left untouched.
"""

SUBJECT_NAME_MAX_BYTES = 40


def install_telegram_input_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_text_input_guard_installed", False):
        return

    photo_state = getattr(legacy.AdminStates.waiting_photo, "state", None)
    subject_name_states = {
        getattr(legacy.AdminStates.waiting_subject_name, "state", None),
        getattr(legacy.AdminStates.adding_subject_name, "state", None),
        getattr(legacy.AdminStates.editing_subject_name_state, "state", None),
    }
    subject_name_states.discard(None)

    class TextInputGuard(legacy.BaseMiddleware):
        async def __call__(self, handler, event, data):
            if not isinstance(event, legacy.Message):
                return await handler(event, data)

            state = data.get("state")
            current = await state.get_state() if state is not None else None

            if event.text is None:
                if current and current != photo_state:
                    await event.answer(
                        "Сейчас бот ожидает текстовое сообщение. "
                        "Отправьте текст или вернитесь в меню."
                    )
                    return None
                return await handler(event, data)

            if current in subject_name_states:
                name = event.text.strip()
                if not name:
                    await event.answer("Название предмета не может быть пустым.")
                    return None
                if len(name.encode("utf-8")) > SUBJECT_NAME_MAX_BYTES:
                    await event.answer(
                        "Название предмета слишком длинное. "
                        f"Используйте не более {SUBJECT_NAME_MAX_BYTES} байт UTF-8 "
                        "(для русских букв это обычно около 20 символов)."
                    )
                    return None

            return await handler(event, data)

    legacy.dp.message.outer_middleware(TextInputGuard())
    legacy._text_input_guard_installed = True
