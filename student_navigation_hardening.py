"""Restart-safe navigation for student flows in Telegram and VK."""

from __future__ import annotations

from types import FunctionType


def _clone(fn):
    return FunctionType(
        fn.__code__,
        fn.__globals__,
        name=fn.__name__,
        argdefs=fn.__defaults__,
        closure=fn.__closure__,
    )


async def _telegram_fresh_start(message):
    state = dp.fsm.get_context(
        message.bot,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    await state.clear()
    return await _student_navigation_original_start(message)


async def _telegram_fresh_back(message):
    state = dp.fsm.get_context(
        message.bot,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    await state.clear()
    return await _student_navigation_original_back(message)


async def _vk_fresh_back(message):
    await state_dispenser.delete(message.from_id)
    await message.answer(
        "Главное меню",
        keyboard=await get_main_menu(message.from_id),
    )


def install_telegram_student_navigation_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_student_navigation_tg_hardened", False):
        return

    legacy._student_navigation_original_start = _clone(legacy.Start)
    legacy._student_navigation_original_back = _clone(legacy.main_menu_buttons)
    for target, replacement in (
        (legacy.Start, _telegram_fresh_start),
        (legacy.main_menu_buttons, _telegram_fresh_back),
    ):
        if target.__code__.co_freevars or replacement.__code__.co_freevars:
            raise RuntimeError("Telegram navigation replacement cannot use closures")
        target.__code__ = replacement.__code__

    @legacy.dp.callback_query(legacy.F.data.regexp(
        r"^(?:"
        r"date_.+|slot_.+|confirm_booking|cancel_booking|"
        r"trial_subject_.+|trial_date_.+|back_to_trial_dates|trial_slot_.+|confirm_trial|"
        r"reschedule_date_.+|back_to_reschedule_date|reschedule_slot_.+|confirm_student_reschedule|"
        r"buy_subject_.+|back_to_buy_packages|confirm_buy_subscription"
        r")$"
    ))
    async def expired_student_callback(call: legacy.CallbackQuery, state: legacy.FSMContext):
        # Registered after the state-specific handlers, so it runs only when the
        # original in-memory session no longer matches the old button.
        await legacy.safe_answer(call)
        await state.clear()
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_menu")]
        ])
        text = "Эта кнопка относится к завершённой сессии. Откройте нужный раздел заново."
        try:
            await call.message.edit_text(text, reply_markup=keyboard)
        except legacy.TelegramBadRequest:
            await call.message.answer(text, reply_markup=keyboard)

    legacy._student_navigation_tg_hardened = True


def install_vk_student_navigation_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_student_navigation_vk_hardened", False):
        return
    target = getattr(legacy, "back_to_main_menu_button", None)
    if target is not None:
        if target.__code__.co_freevars or _vk_fresh_back.__code__.co_freevars:
            raise RuntimeError("VK navigation replacement cannot use closures")
        target.__code__ = _vk_fresh_back.__code__
    legacy._student_navigation_vk_hardened = True
