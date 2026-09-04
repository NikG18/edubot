"""Server-side-safe VK administration: statistics only, with pagination."""

from __future__ import annotations

_runtime_legacy = None
_TUTOR_PAGE_SIZE = 10
_STUDENT_PAGE_SIZE = 20


def previous_calendar_months(year: int, month: int, count: int = 12) -> list[tuple[int, int]]:
    current = int(year) * 12 + (int(month) - 1)
    result = []
    for offset in range(max(0, int(count))):
        index = current - offset
        result.append((index // 12, index % 12 + 1))
    return result


def _page_number(event) -> int:
    try:
        return max(0, int((event.payload or {}).get("page", 0)))
    except (TypeError, ValueError):
        return 0


def _page_slice(rows, page: int, page_size: int):
    total = len(rows)
    max_page = max(0, (total - 1) // page_size)
    page = min(max(0, int(page)), max_page)
    start = page * page_size
    return page, max_page, rows[start:start + page_size]


async def _require_admin(event) -> bool:
    legacy = _runtime_legacy
    if legacy is None:
        raise RuntimeError("VK admin statistics hardening is not installed")
    if int(event.user_id) != int(legacy.ADMIN_VK_ID):
        await legacy.answer_event(event, "⛔ Доступ запрещён.", snackbar=True)
        return False
    return True


def _add_pager(legacy, kb, *, cmd: str, page: int, max_page: int):
    buttons = []
    if page > 0:
        buttons.append(legacy.Callback("⬅️", payload={"cmd": cmd, "page": page - 1}))
    if page < max_page:
        buttons.append(legacy.Callback("➡️", payload={"cmd": cmd, "page": page + 1}))
    if buttons:
        kb.add(*buttons)
        kb.row()


async def _vk_admin_stats_menu(event):
    legacy = _runtime_legacy
    if not await _require_admin(event):
        return
    kb = legacy.Keyboard(inline=True)
    kb.add(legacy.Callback("👨‍🏫 Статистика по репетиторам", payload={"cmd": "admin_stats_tutors"}))
    kb.row()
    kb.add(legacy.Callback("👤 Статистика по ученикам", payload={"cmd": "admin_stats_students"}))
    kb.row()
    kb.add(legacy.Callback("🔙 В админ-панель", payload={"cmd": "admin_panel_open"}))
    await legacy.edit_event_message(
        event, "📊 Административная статистика\nВыберите раздел:", keyboard=kb.get_json()
    )


async def _vk_admin_stats_tutors_overview(event):
    legacy = _runtime_legacy
    if not await _require_admin(event):
        return
    stats = list(await legacy.get_all_tutors_stats())
    page, max_page, visible = _page_slice(stats, _page_number(event), _TUTOR_PAGE_SIZE)

    total_lessons = sum(float(row.get("total_lessons") or 0) for row in stats)
    total_income = sum(float(row.get("total_income") or 0) for row in stats)
    total_commission = sum(float(row.get("commission") or 0) for row in stats)
    lines = [f"📊 Репетиторы — страница {page + 1}/{max_page + 1}:\n"]
    for row in visible:
        lines.extend([
            f"👨‍🏫 {row['name']}",
            f"   Занятий: {int(row.get('total_lessons') or 0)}",
            f"   Доход: {float(row.get('total_income') or 0):.2f} руб.",
            f"   Комиссия: {float(row.get('commission') or 0):.2f} руб.",
            f"   После комиссии: {float(row.get('net_income') or 0):.2f} руб.",
            "",
        ])
    lines.extend([
        "📌 Общий итог:",
        f"   Всего занятий: {int(total_lessons)}",
        f"   Общий доход: {total_income:.2f} руб.",
        f"   Общая комиссия: {total_commission:.2f} руб.",
    ])

    kb = legacy.Keyboard(inline=True)
    _add_pager(legacy, kb, cmd="admin_stats_tutors", page=page, max_page=max_page)
    now = legacy.now_msk_naive()
    month_buttons = []
    for year, month in previous_calendar_months(now.year, now.month, 12):
        month_buttons.append(
            legacy.Callback(
                f"{year}-{month:02d}",
                payload={"cmd": f"admin_stats_tutors_month_{year}_{month}", "page": 0},
            )
        )
        if len(month_buttons) == 3:
            kb.add(*month_buttons)
            kb.row()
            month_buttons = []
    if month_buttons:
        kb.add(*month_buttons)
        kb.row()
    kb.add(legacy.Callback("🔙 К разделам статистики", payload={"cmd": "admin_stats"}))
    await legacy.edit_event_message(event, "\n".join(lines), keyboard=kb.get_json())


async def _vk_admin_stats_tutors_month(event):
    legacy = _runtime_legacy
    if not await _require_admin(event):
        return
    parts = str((event.payload or {}).get("cmd") or "").split("_")
    try:
        year = int(parts[4])
        month = int(parts[5])
    except (IndexError, TypeError, ValueError):
        await legacy.answer_event(event, "Некорректный месяц.", snackbar=True)
        return
    if not 1 <= month <= 12:
        await legacy.answer_event(event, "Некорректный месяц.", snackbar=True)
        return

    stats = list(await legacy.get_all_tutors_stats_by_month(year, month))
    page, max_page, visible = _page_slice(stats, _page_number(event), _TUTOR_PAGE_SIZE)
    total_lessons = sum(float(row.get("total_lessons") or 0) for row in stats)
    total_income = sum(float(row.get("total_income") or 0) for row in stats)
    total_commission = sum(float(row.get("commission") or 0) for row in stats)
    lines = [f"📊 Репетиторы за {year}-{month:02d} — {page + 1}/{max_page + 1}:\n"]
    for row in visible:
        lines.extend([
            f"👨‍🏫 {row['name']}",
            f"   Занятий: {int(row.get('total_lessons') or 0)}",
            f"   Доход: {float(row.get('total_income') or 0):.2f} руб.",
            f"   Комиссия: {float(row.get('commission') or 0):.2f} руб.",
            f"   После комиссии: {float(row.get('net_income') or 0):.2f} руб.",
            "",
        ])
    lines.extend([
        "📌 Итог месяца:",
        f"   Занятий: {int(total_lessons)}",
        f"   Доход: {total_income:.2f} руб.",
        f"   Комиссия: {total_commission:.2f} руб.",
    ])
    cmd = f"admin_stats_tutors_month_{year}_{month}"
    kb = legacy.Keyboard(inline=True)
    _add_pager(legacy, kb, cmd=cmd, page=page, max_page=max_page)
    kb.add(legacy.Callback("🔙 К общей статистике", payload={"cmd": "admin_stats_tutors"}))
    await legacy.edit_event_message(event, "\n".join(lines), keyboard=kb.get_json())


async def _vk_admin_stats_students(event):
    legacy = _runtime_legacy
    if not await _require_admin(event):
        return
    stats = list(await legacy.get_students_stats())
    page, max_page, visible = _page_slice(stats, _page_number(event), _STUDENT_PAGE_SIZE)
    if not stats:
        text = "Нет данных по ученикам."
    else:
        lines = [f"📊 Ученики — страница {page + 1}/{max_page + 1}:\n"]
        for row in visible:
            accounts = []
            if row.get("telegram_id") is not None:
                accounts.append(f"TG {row['telegram_id']}")
            if row.get("vk_id") is not None:
                accounts.append(f"VK {row['vk_id']}")
            account_text = " / ".join(accounts) or "нет messenger-id"
            lines.extend([
                f"👤 {row['username']} · student #{row['student_id']}",
                f"   Аккаунты: {account_text}",
                f"   Проведено занятий: {int(row.get('completed_lessons') or 0)}",
                f"   Остаток абонементов: {int(row.get('remaining_subscription_lessons') or 0)}",
                "",
            ])
        text = "\n".join(lines)

    kb = legacy.Keyboard(inline=True)
    _add_pager(legacy, kb, cmd="admin_stats_students", page=page, max_page=max_page)
    kb.add(legacy.Callback("🔙 К разделам статистики", payload={"cmd": "admin_stats"}))
    await legacy.edit_event_message(event, text, keyboard=kb.get_json())


def install_vk_admin_stats_hardening(app) -> None:
    global _runtime_legacy
    legacy = app.legacy
    if getattr(legacy, "_vk_admin_stats_hardened", False):
        return
    _runtime_legacy = legacy
    # Universal VK callback dispatcher resolves these names dynamically.
    legacy.admin_stats_menu = _vk_admin_stats_menu
    legacy.admin_stats_tutors_overview = _vk_admin_stats_tutors_overview
    legacy.admin_stats_tutors_month = _vk_admin_stats_tutors_month
    legacy.admin_stats_students = _vk_admin_stats_students
    legacy._vk_admin_stats_hardened = True
