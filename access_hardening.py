"""Server-side role guards for features that were previously hidden only by buttons."""

from __future__ import annotations

from types import FunctionType


def _clone(fn):
    return FunctionType(
        fn.__code__, fn.__globals__, name=fn.__name__,
        argdefs=fn.__defaults__, closure=fn.__closure__,
    )


# These functions intentionally reference names that will resolve in the legacy module
# after their code objects are assigned to already-registered aiogram handlers. They
# have no closures, so code replacement is safe.
async def _tg_material_message_guard(message):
    if message.from_user.id != ADMING_ID:
        await message.answer(
            "Этот раздел доступен только администратору.",
            reply_markup=await get_main_menu(message.from_user.id),
        )
        return
    return await _materials_original_message(message)


async def _tg_material_callback_guard(call):
    if call.from_user.id != ADMING_ID:
        await safe_answer(call, "⛔ Раздел доступен только администратору.", show_alert=True)
        return
    data = str(call.data or "")
    key = data.split("_", 1)[0] if data not in _materials_callback_originals else data
    original = _materials_callback_originals.get(data) or _materials_callback_originals.get(key)
    if original is None:
        await safe_answer(call, "Кнопка устарела. Откройте раздел заново.", show_alert=True)
        return
    return await original(call)


async def _vk_material_message_deny(message):
    await message.answer(
        "Учебные материалы доступны администратору только в Telegram.",
        keyboard=await get_main_menu(message.from_id),
    )


def install_telegram_materials_guard(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_materials_access_guard_installed", False):
        return

    if hasattr(legacy, "material"):
        legacy._materials_original_message = _clone(legacy.material)
        legacy.material.__code__ = _tg_material_message_guard.__code__

    originals = {}
    for name in ("back_to_mat", "book", "vid", "bookh", "bookf", "videh", "videf"):
        fn = getattr(legacy, name, None)
        if fn is not None:
            originals[name] = _clone(fn)
            fn.__code__ = _tg_material_callback_guard.__code__
    legacy._materials_callback_originals = originals
    legacy._materials_access_guard_installed = True


def install_vk_materials_guard(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_materials_access_guard_installed", False):
        return

    current_menu = legacy.get_main_menu

    async def menu_without_materials(user_id: int):
        if user_id != legacy.ADMIN_VK_ID:
            return await current_menu(user_id)
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
        legacy.material.__code__ = _vk_material_message_deny.__code__

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
