"""Фискальные реквизиты поставщика для агентской модели.

Репетитор — непосредственный исполнитель услуги и принципал, сервис — агент.
Для агентского чека T-Bank требует SupplierInfo с телефоном, именем и ИНН
поставщика. Этот слой добавляет поле phone без изменения базового интерфейса БД
и публикует его в get_all_tutors().

Здесь же установлен небольшой compatibility-патч для актуальных wrapper-entrypoint:
в меню преподавателя скрыты связывание Telegram/VK и учебные материалы. Учебные
материалы остаются в меню администратора; связывание аккаунтов остаётся доступно
ученикам и администратору.
"""

import asyncio
import re
import sys

import database as _db

_schema_lock = asyncio.Lock()
_schema_ready = False


def normalize_supplier_phone(value: str | None) -> str:
    """Нормализует телефон в формат +<цифры>, требуемый T-Bank."""
    raw = (value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not (7 <= len(digits) <= 18):
        return ""
    return "+" + digits


async def ensure_fiscal_schema():
    global _schema_ready
    if _schema_ready:
        return
    async with _schema_lock:
        if _schema_ready:
            return
        await _db._ensure_pool()
        async with _db._legacy.pool.acquire() as conn:
            await conn.execute("ALTER TABLE tutors ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT ''")
        _schema_ready = True


async def get_tutor_phone(tutor_id: int) -> str:
    await ensure_fiscal_schema()
    async with _db._legacy.pool.acquire() as conn:
        value = await conn.fetchval("SELECT phone FROM tutors WHERE id=$1", int(tutor_id))
    return normalize_supplier_phone(value)


async def set_tutor_phone(tutor_id: int, value: str):
    """Сохраняет нормализованный контакт поставщика и возвращает обновлённую запись."""
    phone = normalize_supplier_phone(value)
    if not phone:
        raise ValueError(
            "Телефон должен содержать от 7 до 18 цифр, например +79991234567."
        )
    await ensure_fiscal_schema()
    async with _db._legacy.pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE tutors SET phone=$1 WHERE id=$2 RETURNING id, name, phone",
            phone,
            int(tutor_id),
        )
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "phone": normalize_supplier_phone(row["phone"]),
    }


def _install_telegram_role_menu(legacy):
    if getattr(legacy, "_role_menu_visibility_installed", False):
        return
    original = legacy.get_main_menu

    async def _get_main_menu(user_id: int):
        is_admin = user_id == legacy.ADMING_ID
        is_tutor = await legacy.get_tutor_by_telegram_id(user_id) is not None
        if is_admin or not is_tutor:
            return await original(user_id)

        # Для преподавателя намеренно нет учебных материалов и связывания
        # Telegram/VK. Остальные пункты преподавательского меню сохраняются.
        buttons = [
            [legacy.KeyboardButton(text="📚 Информация о занятиях")],
            [legacy.KeyboardButton(text="👨‍🏫 Панель преподавателя")],
            [legacy.KeyboardButton(text="✉️ Связь с учеником")],
            [legacy.KeyboardButton(text="🆘 Поддержка")],
            [legacy.KeyboardButton(text="❓ Помощь")],
        ]
        return legacy.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    legacy.get_main_menu = _get_main_menu
    legacy._role_menu_visibility_installed = True


def _install_vk_role_menu(legacy):
    if getattr(legacy, "_role_menu_visibility_installed", False):
        return
    original = legacy.get_main_menu

    async def _get_main_menu(user_id: int) -> str:
        is_admin = user_id == legacy.ADMIN_VK_ID
        is_tutor = await legacy.get_tutor_by_vk_id(user_id) is not None
        if is_admin or not is_tutor:
            return await original(user_id)

        kb = legacy.Keyboard(inline=False, one_time=False)
        rows = [
            ("📚 Информация о занятиях", legacy.KeyboardButtonColor.PRIMARY),
            ("👨‍🏫 Панель преподавателя", legacy.KeyboardButtonColor.POSITIVE),
            ("✉️ Связь с учеником", legacy.KeyboardButtonColor.PRIMARY),
            ("🆘 Поддержка", legacy.KeyboardButtonColor.PRIMARY),
            ("❓ Помощь", legacy.KeyboardButtonColor.PRIMARY),
        ]
        for index, (label, color) in enumerate(rows):
            kb.add(legacy.Text(label), color=color)
            if index != len(rows) - 1:
                kb.row()
        return kb.get_json()

    legacy.get_main_menu = _get_main_menu
    legacy._role_menu_visibility_installed = True


def install_role_menu_visibility(legacy):
    """Скрывает лишние пункты только из преподавательского главного меню."""
    name = getattr(legacy, "__name__", "")
    if name.endswith("Bot_test_legacy"):
        _install_telegram_role_menu(legacy)
    elif name.endswith("vk_bot_legacy"):
        _install_vk_role_menu(legacy)


def _install_loaded_role_menus():
    # В актуальных wrapper-entrypoint legacy-модуль полностью импортируется до
    # fiscal_agent, поэтому патч можно безопасно установить при импорте этого слоя.
    for module_name in ("Bot_test_legacy", "vk_bot_legacy"):
        legacy = sys.modules.get(module_name)
        if legacy is not None and hasattr(legacy, "get_main_menu"):
            install_role_menu_visibility(legacy)


def install(legacy):
    """Добавляет phone к словарям get_all_tutors() конкретного bot legacy-модуля."""
    install_role_menu_visibility(legacy)
    if getattr(legacy, "_fiscal_supplier_layer_installed", False):
        return

    original = legacy.get_all_tutors

    async def _get_all_tutors_with_phone():
        tutors = await original()
        await ensure_fiscal_schema()
        if not tutors:
            return tutors
        ids = [int(tid) for tid in tutors]
        async with _db._legacy.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, phone FROM tutors WHERE id=ANY($1::int[])", ids)
        phones = {int(row["id"]): normalize_supplier_phone(row["phone"]) for row in rows}
        for tid, tutor in tutors.items():
            tutor["phone"] = phones.get(int(tid), "")
        return tutors

    legacy.get_all_tutors = _get_all_tutors_with_phone
    legacy._fiscal_supplier_layer_installed = True


_install_loaded_role_menus()
