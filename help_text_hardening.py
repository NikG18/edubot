"""Current help text shared by Telegram and VK.

Legacy help screens still mention obsolete manual payment methods and old discount
rules.  This module replaces the already-registered help handlers without changing
other navigation.
"""

from __future__ import annotations


CURRENT_HELP_TEXT = (
    "📖 Помощь по использованию бота\n\n"
    "👤 Для учеников\n"
    "• «Информация о репетиторах» — анкеты, предметы и стоимость занятий.\n"
    "• «Запись на занятие» — выбор преподавателя, предмета, даты и времени.\n"
    "• Пробное занятие бесплатно; после подтверждения преподавателем оплата не требуется.\n"
    "• «Мои записи» — активные занятия, перенос/отмена там, где это допускают правила.\n"
    "• «Оплата» — оплачивайте только конкретную запись или абонемент по ссылке, "
    "которую создаёт бот через Т-Банк. Не переводите оплату вручную по номеру телефона.\n"
    "• Абонементы: 12 занятий — скидка 5%, 24 — 10%, 36 — 15%.\n"
    "• Семейная скидка — 10%. Скидки и акции не суммируются.\n"
    "• «Связать Telegram и VK» — объединяет записи, абонементы и статистику одного ученика.\n"
    "• «Связь с преподавателем» и «Поддержка» — сообщения доставляются через бота.\n\n"
    "👨‍🏫 Для преподавателей\n"
    "• «Панель преподавателя» — ученики, расписание и статистика.\n"
    "• Подтверждайте или отклоняйте новые заявки своевременно.\n"
    "• Если у ученика есть оплаченный абонемент, занятие списывается из него; иначе бот "
    "запрашивает e-mail при необходимости и формирует оплату конкретной записи.\n"
    "• Расписание и действия с занятиями выполняются только через кнопки бота.\n"
    "• Для связи с учеником используйте «Связь с учеником».\n\n"
    "🛡 Администрирование\n"
    "• Полная админ-панель доступна только в Telegram.\n"
    "• В VK для администратора оставлена только статистика.\n"
    "• Учебные материалы доступны только администратору в Telegram.\n\n"
    "Если старая кнопка ведёт в устаревший раздел, вернитесь в главное меню и откройте нужный раздел заново."
)


def install_help_text_hardening(app, platform: str) -> None:
    legacy = app.legacy
    platform = str(platform).lower()
    marker = f"_current_help_text_{platform}_installed"
    if getattr(legacy, marker, False):
        return

    legacy.legacy = legacy
    legacy.CURRENT_HELP_TEXT = CURRENT_HELP_TEXT

    if platform == "telegram":
        async def patched_help(message):
            await message.answer("Открываю раздел помощи...", reply_markup=legacy.ReplyKeyboardRemove())
            keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
                [legacy.InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
            ])
            await message.answer(legacy.CURRENT_HELP_TEXT, reply_markup=keyboard)
    elif platform == "vk":
        async def patched_help(message):
            keyboard = legacy.Keyboard(inline=True)
            keyboard.add(legacy.Callback("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
            await message.answer(legacy.CURRENT_HELP_TEXT, keyboard=keyboard.get_json())
    else:
        raise ValueError("platform must be telegram or vk")

    target = getattr(legacy, "help", None)
    if target is None:
        return
    if target.__code__.co_freevars or patched_help.__code__.co_freevars:
        raise RuntimeError("help handler replacement cannot use closures")
    target.__code__ = patched_help.__code__
    setattr(legacy, marker, True)
