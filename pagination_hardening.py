"""Pagination for large Telegram admin booking lists."""

from __future__ import annotations

from datetime import datetime

import database as _db

PAGE_SIZE = 20
_VALID_STATUSES = {"pending", "confirmed", "paid", "completed", "cancelled"}


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


def install_telegram_pagination_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_admin_booking_pagination_installed", False):
        return

    async def render_page(call, status: str, page: int):
        if status not in _VALID_STATUSES:
            await legacy.safe_answer(call, "Некорректный статус.", show_alert=True)
            return
        bookings = await _db.get_all_bookings()
        rows = [(bid, row) for bid, row in bookings.items() if row.get("status") == status]
        rows.sort(key=_booking_sort_key)
        page, start, end = _page_bounds(len(rows), page)
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)

        tutors = await _db.get_all_tutors()
        buttons = []
        for bid, booking in rows[start:end]:
            tutor_name = tutors.get(booking.get("tutor_id"), {}).get("name", "Неизвестный")
            label = (
                f"#{bid} {booking.get('date')} {booking.get('time_slot')} · "
                f"{booking.get('username')} · {tutor_name}"
            )
            buttons.append([
                legacy.InlineKeyboardButton(
                    text=label[:60],
                    callback_data=f"admin_booking_view_{bid}",
                )
            ])

        nav = []
        if page > 0:
            nav.append(legacy.InlineKeyboardButton(
                text="⬅️",
                callback_data=f"admin_bookings_page_{status}_{page - 1}",
            ))
        if page + 1 < pages:
            nav.append(legacy.InlineKeyboardButton(
                text="➡️",
                callback_data=f"admin_bookings_page_{status}_{page + 1}",
            ))
        if nav:
            buttons.append(nav)
        buttons.append([
            legacy.InlineKeyboardButton(text="🔙 К разделам", callback_data="admin_bookings")
        ])
        await call.message.edit_text(
            f"Занятия со статусом <b>{status}</b>: {len(rows)}\n"
            f"Страница {page + 1}/{pages}",
            reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    async def first_page(call):
        await legacy.safe_answer(call)
        if call.from_user.id != legacy.ADMING_ID:
            return
        status = call.data.removeprefix("admin_bookings_status_")
        await app._render_admin_bookings_page(call, status, 0)

    # aiogram has already registered this callable in Bot_test.py; preserve identity.
    app._render_admin_bookings_page = render_page
    legacy._render_admin_bookings_page = render_page
    app.admin_bookings_list.__code__ = first_page.__code__

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^admin_bookings_page_[a-z]+_\d+$"))
    async def admin_bookings_page(call: legacy.CallbackQuery):
        await legacy.safe_answer(call)
        if call.from_user.id != legacy.ADMING_ID:
            return
        try:
            payload = call.data.removeprefix("admin_bookings_page_")
            status, page_raw = payload.rsplit("_", 1)
            page = int(page_raw)
        except (AttributeError, TypeError, ValueError):
            await legacy.safe_answer(call, "Некорректная страница.", show_alert=True)
            return
        await render_page(call, status, page)

    legacy._admin_booking_pagination_installed = True
