"""Фискальные реквизиты поставщика для агентской модели.

Репетитор — непосредственный исполнитель услуги и принципал, сервис — агент.
Для агентского чека T-Bank требует SupplierInfo с телефоном, именем и ИНН
поставщика. Этот слой добавляет поле phone без изменения базового интерфейса БД
и публикует его в get_all_tutors().
"""

import asyncio
import re

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


def install(legacy):
    """Добавляет phone к словарям get_all_tutors() конкретного bot legacy-модуля."""
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
