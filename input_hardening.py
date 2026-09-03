"""Input guards for legacy FSM handlers.

The old aiogram handlers frequently call ``message.text.strip()`` directly.  A
sticker, voice note, document or photo can therefore produce ``None`` and crash a
state handler.  We guard this once at the dispatcher boundary instead of editing
dozens of legacy handlers.  The only text-state exception is the admin tutor-photo
step, which intentionally accepts a photo.
"""


def install_telegram_input_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_text_input_guard_installed", False):
        return

    photo_state = getattr(legacy.AdminStates.waiting_photo, "state", None)

    class TextInputGuard(legacy.BaseMiddleware):
        async def __call__(self, handler, event, data):
            if isinstance(event, legacy.Message) and event.text is None:
                state = data.get("state")
                current = await state.get_state() if state is not None else None
                if current and current != photo_state:
                    await event.answer(
                        "Сейчас бот ожидает текстовое сообщение. "
                        "Отправьте текст или вернитесь в меню."
                    )
                    return None
            return await handler(event, data)

    legacy.dp.message.outer_middleware(TextInputGuard())
    legacy._text_input_guard_installed = True
