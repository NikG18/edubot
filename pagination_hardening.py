"""Pagination for large Telegram admin booking lists."""

from __future__ import annotations

from datetime import datetime

import database as _db
from booking_visibility_rules import admin_booking_matches

PAGE_SIZE = 20
_VALID_SECTIONS = {"pending", "confirmed", "paid", "completed", "cancelled", "trials"}


def _booking_sort_key(item):
    booking_id, booking = item
    try:
        lesson_date = datetime.strptime(str(booking.get("date") or ""), "%d.%m.%Y")
    except ValueError:
        lesson_date = datetime.max
    return lesson_date, str(booking.get("time_slot") or ""), int(booking_id)


def _page_bounds(total: int, page: int, page_size: int = PAGE_SIZE) -> tuple[int, int, int]:
    pages = max(1, (max(0, int(total)) + page_size - 1) // page_size)
    page = min(max(0, int(page)), pages - 1)
    start = page * page_size
    return page, start, min(start + page_size, total)


def _section_title(section: str) -> str:
    return {
        "pending": "Ожидают подтверждения",
        "confirmed": "Ожидают оплаты",
        "paid": "Оплаченные",
        "completed": "Проведённые",
        "cancelled": "Отменённые",
        "trials": "Пробные занятия",
    }.get(section, section)


async def _admin_bookings_first_page(call):
    """Closure-free replacement for the already-registered admin list handler."""
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    section = call.data.removeprefix("admin_bookings_status_")
    await _render_admin_bookings_page(call, section, 0)


def install_telegram_pagination_hardening(app) -> None:
    legacy_module = app.legacy
    if getattr(legacy_module, "_admin_booking_pagination_installed", False):
        return

    async def render_page(call, section: str, page: int):
        if section not in _VALID_SECTIONS:
            await legacy_module.safe_answer(call, "Некорректный раздел.", show_alert=True)
            return
        bookings = await _db.get_all_bookings()
        rows = [
            (bid, row) for bid, row in bookings.items()
            if admin_booking_matches(row, section)
        ]
        rows.sort(key=_booking_sort_key)
        page, start, end = _page_bounds(len(rows), page)
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)

        tutors = await _db.get_all_tutors()
        buttons = []
        for bid, booking in rows[start:end]:
            tutor_name = tutors.get(booking.get("tutor_id"), {}).get("name", "Неизвестный")
            trial_status = f" · {booking.get('status')}" if section == "trials" else ""
            label = (
                f"#{bid} {booking.get('date')} {booking.get('time_slot')} · "
                f"{booking.get('username')} · {tutor_name}{trial_status}"
            )
            buttons.append([
                legacy_module.InlineKeyboardButton(
                    text=label[:60],
                    callback_data=f"admin_booking_view_{bid}",
                )
            ])

        nav = []
        if page > 0:
            nav.append(legacy_module.InlineKeyboardButton(
                text="⬅️",
                callback_data=f"admin_bookings_page_{section}_{page - 1}",
            ))
        if page + 1 < pages:
            nav.append(legacy_module.InlineKeyboardButton(
                text="➡️",
                callback_data=f"admin_bookings_page_{section}_{page + 1}",
            ))
        if nav:
            buttons.append(nav)
        buttons.append([
            legacy_module.InlineKeyboardButton(text="🔙 К разделам", callback_data="admin_bookings")
        ])
        await call.message.edit_text(
            f"{_section_title(section)}: {len(rows)}\n"
            f"Страница {page + 1}/{pages}",
            reply_markup=legacy_module.InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    # aiogram has already registered this callable in Bot_test.py; preserve its
    # function identity but transplant only closure-free code.
    app._render_admin_bookings_page = render_page
    legacy_module._render_admin_bookings_page = render_page
    app.legacy = legacy_module
    if app.admin_bookings_list.__code__.co_freevars or _admin_bookings_first_page.__code__.co_freevars:
        raise RuntimeError("admin booking pagination replacement cannot use closures")
    app.admin_bookings_list.__code__ = _admin_bookings_first_page.__code__

    @legacy_module.dp.callback_query(legacy_module.F.data.regexp(r"^admin_bookings_page_[a-z]+_\d+$"))
    async def admin_bookings_page(call: legacy_module.CallbackQuery):
        await legacy_module.safe_answer(call)
        if call.from_user.id != legacy_module.ADMING_ID:
            return
        try:
            payload = call.data.removeprefix("admin_bookings_page_")
            section, page_raw = payload.rsplit("_", 1)
            page = int(page_raw)
        except (AttributeError, TypeError, ValueError):
            await legacy_module.safe_answer(call, "Некорректная страница.", show_alert=True)
            return
        await render_page(call, section, page)

    legacy_module._admin_booking_pagination_installed = True
