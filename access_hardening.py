"""Server-side role guards for features that were previously hidden only by buttons."""

from __future__ import annotations

from types import FunctionType


def _clone(fn):
    return FunctionType(
        fn.__code__, fn.__globals__, name=fn.__name__,
        argdefs=fn.__defaults__, closure=fn.__closure__,
    )


def install_telegram_materials_guard(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_materials_access_guard_installed", False):
        return

    message_name = "material"
    callback_names = ("back_to_mat", "book", "vid", "bookh", "bookf", "videh", "videf")
    if hasattr(legacy, message_name):
        legacy._materials_original_message = _clone(getattr(legacy, message_name))

        async def guarded_material(message):
            if message.from_user.id != legacy.ADMING_ID:
                await message.answer(
                    "Этот раздел доступен только администратору.",
                    reply_markup=await legacy.get_main_menu(message.from_user.id),
                )
                return
            return await legacy._materials_original_message(message)

        getattr(legacy, message_name).__code__ = guarded_material.__code__

    for name in callback_names:
        fn = getattr(legacy, name, None)
        if fn is None:
            continue
        original_name = f"_materials_original_{name}"
        setattr(legacy, original_name, _clone(fn))

        async def callback_guard(call, _original_name=original_name):
            if call.from_user.id != legacy.ADMING_ID:
                await legacy.safe_answer(call, "⛔ Раздел доступен только администратору.", show_alert=True)
                return
            return await getattr(legacy, _original_name)(call)

        # Aiogram already registered the original callable, so preserve its identity.
        fn.__code__ = callback_guard.__code__
        fn.__defaults__ = callback_guard.__defaults__

    legacy._materials_access_guard_installed = True


def install_vk_materials_guard(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_materials_access_guard_installed", False):
        return

    current_menu = legacy.get_main_menu

    async def menu_without_materials(user_id: int):
        if user_id != legacy.ADMIN_VK_ID:
            return await current_menu(user_id)
        # Keep normal student/admin navigation but remove materials. The nested
        # admin panel itself remains statistics-only via runtime_hardening.
        kb = legacy.Keyboard(inline=False, one_time=False)
        rows = [
            ("ℹ️ Информация о репетиторах", legacy.KeyboardButtonColor.PRIMARY),
            ("📚 Информация о занятиях", legacy.KeyboardButtonColor.PRIMARY),
            ("📝 Запись на занятие", legacy.KeyboardButtonColor.PRIMARY),
            ("📋 Мои записи", legacy.KeyboardButtonColor.PRIMARY),
            ("💳 Оплата", legacy.KeyboardButtonColor.PRIMARY),
            ("✉️ Связь с преподавателем", legacy.KeyboardButtonColor.PRIMARY),
            ("❓ Помощь", legacy.KeyboardButtonColor.PRIMARY),
            ("🔗 Связать Telegram и VK", legacy.KeyboardButtonColor.PRIMARY),
            ("👨‍🏫 Админ-панель", legacy.KeyboardButtonColor.POSITIVE),
        ]
        for index, (label, color) in enumerate(rows):
            kb.add(legacy.Text(label), color=color)
            if index != len(rows) - 1:
                kb.row()
        return kb.get_json()

    legacy.get_main_menu = menu_without_materials

    if hasattr(legacy, "material"):
        async def deny_message(message):
            await message.answer(
                "Учебные материалы доступны администратору только в Telegram.",
                keyboard=await legacy.get_main_menu(message.from_id),
            )
        legacy.material.__code__ = deny_message.__code__

    async def deny_callback(event):
        await legacy.answer_event(
            event,
            "Учебные материалы доступны администратору только в Telegram.",
            snackbar=True,
        )

    # VK universal callback dispatcher resolves these globals dynamically.
    for name in ("back_to_mat", "book", "vid", "bookh", "bookf", "videh", "videf"):
        if hasattr(legacy, name):
            setattr(legacy, name, deny_callback)

    legacy._materials_access_guard_installed = True
