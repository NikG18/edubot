"""Keep tutor statistics labels aligned with the financial calculation layer."""

from __future__ import annotations


async def _tutor_stats_menu(call):
    tid = int(call.data.split("_")[2])
    actual_tid = await get_tutor_by_telegram_id(call.from_user.id)
    if actual_tid != tid:
        await safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
        return
    fin = await get_tutor_financials(tid)
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    comm_percent = float(fin.get("commission_percent") or 0)
    comm_label = f"{comm_percent:g}"
    auto_suffix = " (авто)" if tutor and tutor.get("commission_mode") == "auto" else ""
    text = (
        f"📊 Статистика за всё время\n"
        f"• Проведено занятий: {fin['total_lessons']}\n"
        f"• Общий доход: {fin['total_income']:.2f} руб.\n"
        f"• Комиссия за всё время: {fin['commission_amount']:.2f} руб.\n"
        f"• Доход после комиссии: {fin['net_income']:.2f} руб.\n"
        f"• Текущая ставка комиссии: {comm_label}%{auto_suffix}\n\n"
        "Выберите месяц для детализации:"
    )
    now = now_msk_naive()
    months = []
    current_index = now.year * 12 + (now.month - 1)
    for offset in range(12):
        month_index = current_index - offset
        year = month_index // 12
        month = month_index % 12 + 1
        months.append((year, month))
    buttons = [
        [InlineKeyboardButton(
            text=f"{y}-{m:02d}", callback_data=f"tutor_stats_month_{tid}_{y}_{m}"
        )]
        for y, m in months
    ]
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_tutor_panel_{tid}")
    ])
    await call.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


async def _tutor_stats_month(call):
    parts = call.data.split("_")
    tid = int(parts[3])
    actual_tid = await get_tutor_by_telegram_id(call.from_user.id)
    if actual_tid != tid:
        await safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
        return
    year = int(parts[4])
    month = int(parts[5])
    fin = await get_tutor_financials(tid, year, month)
    comm_percent = float(fin.get("commission_percent") or 0)
    comm_label = f"{comm_percent:g}"
    text = (
        f"📊 Статистика за {year}-{month:02d}\n"
        f"• Проведено занятий: {fin['total_lessons']}\n"
        f"• Доход: {fin['total_income']:.2f} руб.\n"
        f"• Ставка комиссии месяца: {comm_label}%\n"
        f"• Комиссия: {fin['commission_amount']:.2f} руб.\n"
        f"• Доход после комиссии: {fin['net_income']:.2f} руб."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔙 К общей статистике", callback_data=f"tutor_stats_{tid}"
        )],
    ])
    await call.message.edit_text(text, reply_markup=keyboard)


def install_telegram_financial_display_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_financial_display_hardened", False):
        return

    for current, replacement in (
        (legacy.tutor_stats_menu, _tutor_stats_menu),
        (legacy.tutor_stats_month, _tutor_stats_month),
    ):
        if current.__code__.co_freevars or replacement.__code__.co_freevars:
            raise RuntimeError("Tutor statistics replacement cannot use closures")
        current.__code__ = replacement.__code__

    legacy._financial_display_hardened = True
