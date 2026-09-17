"""Keep Telegram tutor-card navigation independent of tutor photos.

Telegram cannot edit a photo message with ``edit_text``.  The old tutor card
attached its inline keyboard to a photo, so going back or continuing to another
step could fail.  New cards keep the navigable card as a text message and send
the photo separately.  The separate photo is deleted when the student returns
to the tutor list.
"""

from __future__ import annotations


_runtime_legacy = None
_PHOTO_STATE_KEY = "tutor_info_photo_message_id"


async def _delete_tracked_photo(call, state) -> None:
    legacy = _runtime_legacy
    data = await state.get_data()
    photo_message_id = data.get(_PHOTO_STATE_KEY)
    if not photo_message_id:
        return
    try:
        await call.bot.delete_message(
            chat_id=call.message.chat.id,
            message_id=int(photo_message_id),
        )
    except (legacy.TelegramBadRequest, TypeError, ValueError):
        pass
    await state.update_data(**{_PHOTO_STATE_KEY: None})


async def _replace_with_text(call, text, keyboard) -> None:
    """Edit a text callback message or safely replace a media message."""

    legacy = _runtime_legacy
    if getattr(call.message, "content_type", "text") == "text":
        try:
            await call.message.edit_text(text, reply_markup=keyboard)
            return
        except legacy.TelegramBadRequest:
            # A stale/deleted message is replaced below.
            pass
    try:
        await call.message.delete()
    except legacy.TelegramBadRequest:
        pass
    await call.message.answer(text, reply_markup=keyboard)


async def _hardened_back_to_tutors(call, state):
    legacy = _runtime_legacy
    await legacy.safe_answer(call)
    await _delete_tracked_photo(call, state)
    await state.clear()
    keyboard = await legacy.make_tutors_keyboard("tutor_info")
    await _replace_with_text(call, "Кто из репетиторов Вас интересует?", keyboard)


async def _hardened_show_tutor_info(call, state):
    legacy = _runtime_legacy
    await legacy.safe_answer(call)
    await _delete_tracked_photo(call, state)
    await state.clear()

    try:
        tutor_id = int(call.data.rsplit("_", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await _replace_with_text(call, "Кнопка устарела. Откройте список репетиторов заново.", None)
        return
    tutors = await legacy.get_all_tutors()
    tutor = tutors.get(tutor_id)
    if not tutor:
        await _replace_with_text(call, "Репетитор не найден.", None)
        return

    description = str(tutor.get("description") or "Описание пока не добавлено.")
    lines = [description, "", "Предметы и цены:"]
    for subject, price in (tutor.get("subjects") or {}).items():
        lines.append(f"• {subject} — {price} руб.")
    text = "\n".join(lines)
    keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
        [legacy.InlineKeyboardButton(
            text="🎓 Записаться на пробное занятие",
            callback_data=f"trials_{tutor_id}",
        )],
        [legacy.InlineKeyboardButton(
            text="🔙 Назад к списку",
            callback_data="back_to_tutors",
        )],
    ])

    photo = tutor.get("photo")
    if not photo:
        await _replace_with_text(call, text, keyboard)
        return

    # Remove the old list/media message so the final order is photo -> text card.
    try:
        await call.message.delete()
    except legacy.TelegramBadRequest:
        pass
    photo_message = None
    try:
        photo_message = await call.bot.send_photo(
            chat_id=call.message.chat.id,
            photo=photo,
        )
    except legacy.TelegramBadRequest:
        legacy.logging.exception(
            "Не удалось показать фото репетитора %s; карточка показана без фото",
            tutor_id,
        )
    await call.message.answer(text, reply_markup=keyboard)
    if photo_message is not None:
        await state.update_data(**{_PHOTO_STATE_KEY: photo_message.message_id})


def install_telegram_tutor_info_photo_hardening(app) -> None:
    global _runtime_legacy
    legacy = app.legacy
    if getattr(legacy, "_telegram_tutor_info_photo_hardened", False):
        return
    _runtime_legacy = legacy

    # These function objects were registered by aiogram during legacy import.
    # Transplant closure-free code so the dispatcher keeps pointing at them.
    legacy._tutor_info_delete_photo = _delete_tracked_photo
    legacy._tutor_info_replace_text = _replace_with_text
    legacy._runtime_legacy = legacy
    for target, replacement in (
        (legacy.back_to_tutors, _hardened_back_to_tutors),
        (legacy.show_tutor_info, _hardened_show_tutor_info),
    ):
        if target.__code__.co_freevars or replacement.__code__.co_freevars:
            raise RuntimeError("Tutor info navigation replacement cannot use closures")
        target.__code__ = replacement.__code__

    # Replacement code runs with legacy-module globals.
    legacy._delete_tracked_photo = _delete_tracked_photo
    legacy._replace_with_text = _replace_with_text
    legacy._PHOTO_STATE_KEY = _PHOTO_STATE_KEY
    legacy._telegram_tutor_info_photo_hardened = True
