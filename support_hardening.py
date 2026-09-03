"""Route support from Telegram and VK into the Telegram admin inbox.

VK administration is intentionally statistics-only. Support requests from VK are
therefore delivered to the Telegram admin with an explicit source-platform callback.
Replies return through the shared bridge to the platform that originated the request.
"""

from __future__ import annotations

import json
import os


async def _telegram_support_to_admin(message, state, bot):
    user = message.from_user
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите текст обращения.")
        return
    username = user.username or user.full_name
    callback = f"supportv2_telegram_{user.id}"
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Ответить", callback_data=callback)]
    ])
    try:
        await bot.send_message(
            ADMING_ID,
            f"🆘 Сообщение в поддержку из Telegram\n"
            f"👤 {username} (ID: {user.id})\n\n{text}",
            reply_markup=reply_markup,
        )
    except Exception:
        logging.exception("Не удалось доставить Telegram support request администратору")
        await message.answer("⚠️ Не удалось доставить обращение. Попробуйте ещё раз позже.")
        return
    await state.clear()
    await message.answer(
        "✅ Ваше сообщение отправлено администратору. Ожидайте ответа.",
        reply_markup=await get_main_menu(user.id),
    )


async def _vk_support_to_telegram_admin(message):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите текст обращения.")
        return
    username = await get_user_display_name(message.from_id)
    callback = f"supportv2_vk_{message.from_id}"
    reply_markup = json.dumps({
        "inline_keyboard": [[{
            "text": "↩️ Ответить",
            "callback_data": callback,
        }]]
    }, ensure_ascii=False)
    delivered = await send_telegram_message(
        TG_ADMIN_ID,
        f"🆘 Сообщение в поддержку из VK\n"
        f"👤 {username} (VK ID: {message.from_id})\n\n{text}",
        reply_markup=reply_markup,
    )
    if not delivered:
        await message.answer("⚠️ Не удалось доставить обращение. Попробуйте ещё раз позже.")
        return
    await state_dispenser.delete(message.from_id)
    await message.answer(
        "✅ Ваше сообщение отправлено администратору. Ожидайте ответа.",
        keyboard=await get_main_menu(message.from_id),
    )


async def _telegram_support_send_reply(message, state, bot):
    data = await state.get_data()
    student_id = data.get("support_reply_student_id")
    platform = data.get("support_reply_student_platform")
    if not student_id:
        await state.clear()
        await message.answer("Сессия ответа устарела.", reply_markup=await get_main_menu(message.from_user.id))
        return
    if platform not in {"telegram", "vk"}:
        # Compatibility with an old support_reply_<id> message.
        platform = "telegram"
        bookings = await get_all_bookings()
        for booking in bookings.values():
            if int(booking.get("user_id") or 0) == int(student_id):
                platform = booking.get("user_platform", "telegram")
                break
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите текст ответа.")
        return
    try:
        delivered = await send_to_user(
            int(student_id),
            platform,
            f"📬 Ответ от администратора:\n{text}",
        )
    except Exception:
        delivered = False
        logging.exception("Не удалось отправить ответ поддержки")
    if delivered:
        await state.clear()
        await message.answer(
            "✅ Ответ отправлен пользователю.",
            reply_markup=await get_main_menu(message.from_user.id),
        )
    else:
        await message.answer(
            "⚠️ Не удалось отправить ответ. Проверьте, что пользователь не заблокировал бота, и попробуйте ещё раз.",
            reply_markup=await get_main_menu(message.from_user.id),
        )


def _replace_registered_code(target, replacement) -> None:
    if target.__code__.co_freevars or replacement.__code__.co_freevars:
        raise RuntimeError("support handler replacement cannot use closures")
    target.__code__ = replacement.__code__


def install_telegram_support_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_telegram_support_hardened", False):
        return

    _replace_registered_code(legacy.support_message_to_admin, _telegram_support_to_admin)
    _replace_registered_code(legacy.support_send_reply, _telegram_support_send_reply)

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^supportv2_(telegram|vk)_\d+$"))
    async def support_v2_reply_start(call: legacy.CallbackQuery, state: legacy.FSMContext):
        await legacy.safe_answer(call)
        if call.from_user.id != legacy.ADMING_ID:
            await legacy.safe_answer(call, "⛔ Только администратор может отвечать на обращения.", show_alert=True)
            return
        try:
            _prefix, platform, raw_id = call.data.split("_", 2)
            student_id = int(raw_id)
        except (TypeError, ValueError):
            await call.message.answer("Некорректная кнопка ответа.")
            return
        await state.update_data(
            support_reply_student_id=student_id,
            support_reply_student_platform=platform,
        )
        await call.message.answer(f"Введите ответ пользователю ({platform}):")
        await state.set_state(legacy.SupportAdminReplyStates.waiting_reply)

    legacy._telegram_support_hardened = True


def install_vk_support_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_vk_support_hardened", False):
        return
    raw_admin = os.environ.get("ADMING_ID", "").strip()
    if not raw_admin.lstrip("-").isdigit():
        legacy.logging.warning(
            "ADMING_ID не задан в VK service: обращения VK в Telegram-admin будут возвращать ошибку доставки"
        )
        telegram_admin_id = 0
    else:
        telegram_admin_id = int(raw_admin)
    legacy.TG_ADMIN_ID = telegram_admin_id
    _replace_registered_code(legacy.support_message_to_admin, _vk_support_to_telegram_admin)
    legacy._vk_support_hardened = True
