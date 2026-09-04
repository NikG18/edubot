"""Harden the two interactive student->tutor message handlers.

The legacy handlers ignored the boolean result of ``send_to_tutor`` and always
showed a green success message. Reply callbacks also carried only a raw numeric
student id, which is ambiguous across Telegram/VK namespaces. New callbacks carry
source platform, student id and tutor id.
"""


async def _telegram_student_to_tutor(message, state, bot):
    user = message.from_user
    username = user.username or user.full_name
    data = await state.get_data()
    tid = data.get("msg_tutor_id")
    tutor_name = data.get("msg_tutor_name")
    text = (message.text or "").strip()
    if not tid or not tutor_name:
        await state.clear()
        await message.answer(
            "Сессия отправки сообщения устарела. Выберите преподавателя заново.",
            reply_markup=await get_main_menu(message.from_user.id),
        )
        return
    if not text:
        await message.answer("Введите текст сообщения.")
        return

    await state.update_data(student_id=user.id, student_username=username)
    forward_msg = (
        f"📨 Сообщение от ученика\n"
        f"👤 {username} (ID: {user.id})\n"
        f"✉️ Преподавателю: {tutor_name}\n\n"
        f"💬 Текст:\n{text}"
    )
    reply_callback = f"reply_tg_{user.id}_{int(tid)}"
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Ответить", callback_data=reply_callback)]
    ])
    vk_reply_kb = vk_keyboard([
        [("↩️ Ответить", {"cmd": reply_callback}, "primary")]
    ])
    delivered = await send_to_tutor(
        tid,
        forward_msg,
        reply_markup_tg=reply_markup,
        keyboard_vk=vk_reply_kb,
    )
    if delivered:
        await message.answer(
            "✅ Сообщение отправлено. Ожидайте ответа.",
            reply_markup=await get_main_menu(message.from_user.id),
        )
    else:
        await message.answer(
            "⚠️ Не удалось доставить сообщение преподавателю. Попробуйте позже или обратитесь в поддержку.",
            reply_markup=await get_main_menu(message.from_user.id),
        )
    await state.clear()


async def _vk_student_to_tutor(message):
    username = await get_user_display_name(message.from_id)
    data = await state_dispenser.get_data(message.from_id)
    tid = data.get("msg_tutor_id")
    tutor_name = data.get("msg_tutor_name")
    text = (message.text or "").strip()
    if not tid or not tutor_name:
        await state_dispenser.delete(message.from_id)
        await message.answer(
            "Сессия отправки сообщения устарела. Выберите преподавателя заново.",
            keyboard=await get_main_menu(message.from_id),
        )
        return
    if not text:
        await message.answer("Введите текст сообщения.")
        return

    forward_msg = (
        f"📨 Сообщение от ученика\n"
        f"👤 {username} (VK ID: {message.from_id})\n"
        f"✉️ Преподавателю: {tutor_name}\n\n"
        f"💬 Текст:\n{text}"
    )
    reply_callback = f"reply_vk_{message.from_id}_{int(tid)}"
    vk_kb = Keyboard(inline=True)
    vk_kb.add(Callback("↩️ Ответить", payload={"cmd": reply_callback}))
    tg_kb = json.dumps({
        "inline_keyboard": [[{
            "text": "↩️ Ответить",
            "callback_data": reply_callback,
        }]]
    }, ensure_ascii=False)

    delivered = await send_to_tutor(
        tid,
        forward_msg,
        reply_markup_tg=tg_kb,
        keyboard_vk=vk_kb.get_json(),
    )
    if delivered:
        await message.answer(
            "✅ Сообщение отправлено. Ожидайте ответа.",
            keyboard=await get_main_menu(message.from_id),
        )
    else:
        await message.answer(
            "⚠️ Не удалось доставить сообщение преподавателю. Попробуйте позже или обратитесь в поддержку.",
            keyboard=await get_main_menu(message.from_id),
        )
    await state_dispenser.delete(message.from_id)


def _replace_registered_handler(target, replacement) -> None:
    # Dispatcher/state-rule registries keep the original function object. Replacing
    # only a module attribute would therefore not affect the registered callback.
    if target.__code__.co_freevars or replacement.__code__.co_freevars:
        raise RuntimeError("handler replacement must not use closure variables")
    target.__code__ = replacement.__code__


def install_telegram_contact_delivery_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_telegram_contact_delivery_hardened", False):
        return
    _replace_registered_handler(legacy.send_message_to_tutor, _telegram_student_to_tutor)
    legacy._telegram_contact_delivery_hardened = True


def install_vk_contact_delivery_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_vk_contact_delivery_hardened", False):
        return
    _replace_registered_handler(legacy.send_message_to_tutor, _vk_student_to_tutor)
    legacy._vk_contact_delivery_hardened = True
