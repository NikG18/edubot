"""Disable obsolete Telegram manual-payment callbacks.

The current payment menu is booking-linked, but old cached messages can still carry
legacy qr/card/sbp callbacks.  Route them back to the supported payment menu instead
of showing manual transfer instructions.
"""

from __future__ import annotations


async def _stale_payment_method(call):
    await safe_answer(
        call,
        "Старый способ оплаты отключён. Выберите конкретное занятие или абонемент.",
        show_alert=True,
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить занятие", callback_data="pay_booking")],
        [InlineKeyboardButton(text="📚 Купить абонемент", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="🔄 Автоплатёж", callback_data="autopay_settings")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")],
    ])
    await call.message.edit_text("Выберите действие:", reply_markup=keyboard)


def install_telegram_payment_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_telegram_stale_payment_methods_disabled", False):
        return

    for name in ("qr", "card", "sbp"):
        target = getattr(legacy, name, None)
        if target is None:
            continue
        if target.__code__.co_freevars or _stale_payment_method.__code__.co_freevars:
            raise RuntimeError("payment callback replacement cannot use closures")
        target.__code__ = _stale_payment_method.__code__

    legacy._telegram_stale_payment_methods_disabled = True
