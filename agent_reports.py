from __future__ import annotations

import logging
from datetime import datetime

import database as db
import payments
from agent_report_rules import period_bounds, period_is_closed
from agent_report_service import (
    available_months,
    create_snapshot,
    get_report,
    send_report,
    summary_text,
)


def install_telegram_agent_reports(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_agent_reports_installed", False):
        return

    original_admin_keyboard = legacy.admin_actions_keyboard

    def admin_keyboard_with_reports():
        keyboard = original_admin_keyboard()
        rows = keyboard.inline_keyboard
        rows.insert(max(0, len(rows) - 1), [legacy.InlineKeyboardButton(
            text="📄 Отчёты репетиторам", callback_data="admin_agent_reports"
        )])
        return keyboard

    legacy.admin_actions_keyboard = admin_keyboard_with_reports

    async def require_admin(call) -> bool:
        if call.from_user.id == legacy.ADMING_ID:
            return True
        await legacy.safe_answer(call, "⛔ Только администратор", show_alert=True)
        return False

    @legacy.dp.callback_query(legacy.F.data == "admin_agent_reports")
    async def reports_tutors(call: legacy.CallbackQuery):
        await legacy.safe_answer(call)
        if not await require_admin(call):
            return
        tutors = await db.get_all_tutors()
        buttons = []
        for tutor_id, tutor in tutors.items():
            if payments.is_operator_tutor(tutor.get("inn")):
                continue
            buttons.append([legacy.InlineKeyboardButton(
                text=str(tutor.get("name") or f"Репетитор #{tutor_id}")[:64],
                callback_data=f"agent_report_tutor_{int(tutor_id)}",
            )])
        buttons.append([legacy.InlineKeyboardButton(
            text="🔙 В админ-панель", callback_data="admin_panel_open"
        )])
        text = "📄 Отчёты репетиторам\nВыберите репетитора."
        if len(buttons) == 1:
            text = "Нет сторонних репетиторов для агентских отчётов."
        await call.message.edit_text(
            text, reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons)
        )

    @legacy.dp.callback_query(legacy.F.data.regexp(r"^agent_report_tutor_\d+$"))
    async def reports_months(call: legacy.CallbackQuery):
        await legacy.safe_answer(call)
        if not await require_admin(call):
            return
        tutor_id = int(call.data.rsplit("_", 1)[1])
        tutors = await db.get_all_tutors()
        tutor = tutors.get(tutor_id)
        if not tutor or payments.is_operator_tutor(tutor.get("inn")):
            await call.message.edit_text(
                "Репетитор не найден или не относится к агентской схеме."
            )
            return
        months = await available_months(tutor_id)
        buttons = [[legacy.InlineKeyboardButton(
            text=f"{year}-{month:02d}",
            callback_data=f"agent_report_month_{tutor_id}_{year}_{month}",
        )] for year, month in months]
        if not buttons:
            buttons.append([legacy.InlineKeyboardButton(
                text="Нет месяцев с занятиями", callback_data="admin_agent_reports"
            )])
        buttons.append([legacy.InlineKeyboardButton(
            text="🔙 К репетиторам", callback_data="admin_agent_reports"
        )])
        await call.message.edit_text(
            f"📄 Отчёты: {legacy.html.quote(str(tutor['name']))}\nВыберите месяц:",
            reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    @legacy.dp.callback_query(
        legacy.F.data.regexp(r"^agent_report_month_\d+_\d{4}_\d{1,2}$")
    )
    async def reports_periods(call: legacy.CallbackQuery):
        await legacy.safe_answer(call)
        if not await require_admin(call):
            return
        parts = call.data.split("_")
        tutor_id, year, month = int(parts[3]), int(parts[4]), int(parts[5])
        today = datetime.now(db.MSK).date()
        buttons = []
        for period_no in (1, 2):
            start, end = period_bounds(year, month, period_no)
            mark = "✅" if period_is_closed(today, year, month, period_no) else "⏳"
            buttons.append([legacy.InlineKeyboardButton(
                text=f"{mark} {start:%d.%m}-{end:%d.%m}",
                callback_data=f"agent_report_period_{tutor_id}_{year}_{month}_{period_no}",
            )])
        buttons.append([legacy.InlineKeyboardButton(
            text="🔙 К месяцам", callback_data=f"agent_report_tutor_{tutor_id}"
        )])
        await call.message.edit_text(
            f"Выберите отчётный период за {year}-{month:02d}.\n"
            "⏳ Период можно сформировать только после его окончания.",
            reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    @legacy.dp.callback_query(
        legacy.F.data.regexp(r"^agent_report_period_\d+_\d{4}_\d{1,2}_[12]$")
    )
    async def report_preview(call: legacy.CallbackQuery):
        await legacy.safe_answer(call)
        if not await require_admin(call):
            return
        tutor_id, year, month, period_no = map(int, call.data.split("_")[3:7])
        start, end = period_bounds(year, month, period_no)
        if not period_is_closed(datetime.now(db.MSK).date(), year, month, period_no):
            await legacy.safe_answer(call, "Период ещё не завершён.", show_alert=True)
            return
        existing = await get_report(tutor_id, year, month, period_no)
        tutor = (await db.get_all_tutors()).get(tutor_id, {})
        buttons = []
        if existing:
            report, _ = existing
            complete = bool(report.get("tutor_sent_at") and report.get("archive_sent_at"))
            status = (
                "✅ Отправлен репетитору и в архивную группу"
                if complete else "🟡 Сформирован, отправка не завершена"
            )
            text = (
                summary_text(report, str(tutor.get("name") or "Репетитор"))
                + f"\n\n{status}"
            )
            if not complete:
                buttons.append([legacy.InlineKeyboardButton(
                    text="📤 Завершить отправку",
                    callback_data=f"agent_report_send_{tutor_id}_{year}_{month}_{period_no}",
                )])
        else:
            text = (
                f"📄 {legacy.html.quote(str(tutor.get('name') or 'Репетитор'))}\n"
                f"Период: {start:%d.%m.%Y}-{end:%d.%m.%Y}\n\n"
                "PDF будет сформирован из зафиксированных занятий. После формирования "
                "отчёт становится snapshot и не пересчитывается задним числом."
            )
            buttons.append([legacy.InlineKeyboardButton(
                text="📄 Сформировать и отправить PDF",
                callback_data=f"agent_report_send_{tutor_id}_{year}_{month}_{period_no}",
            )])
        buttons.append([legacy.InlineKeyboardButton(
            text="🔙 К периодам",
            callback_data=f"agent_report_month_{tutor_id}_{year}_{month}",
        )])
        await call.message.edit_text(
            text, reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=buttons)
        )

    @legacy.dp.callback_query(
        legacy.F.data.regexp(r"^agent_report_send_\d+_\d{4}_\d{1,2}_[12]$")
    )
    async def report_send(call: legacy.CallbackQuery, bot):
        await legacy.safe_answer(call)
        if not await require_admin(call):
            return
        tutor_id, year, month, period_no = map(int, call.data.split("_")[3:7])
        try:
            report, _, created = await create_snapshot(tutor_id, year, month, period_no)
            result = await send_report(bot, report)
        except ValueError as exc:
            messages = {
                "report_period_not_closed": "Период ещё не завершён.",
                "report_has_no_lessons": (
                    "В этом периоде нет засчитанных занятий — отчёт не сформирован."
                ),
                "tutor_not_found": "Репетитор не найден.",
                "tutor_has_no_telegram_id": (
                    "У репетитора не указан Telegram ID — PDF не отправлен."
                ),
                "reports_channel_not_configured": (
                    "REPORTS_CHANNEL_ID не задан или задан неверно — PDF не отправлен."
                ),
                "operator_tutor_has_no_agent_report": (
                    "Для собственных занятий ИП агентский отчёт не формируется."
                ),
                "commission_mode_changed_between_reports": (
                    "Между первым и вторым отчётом изменился режим комиссии. "
                    "Автоматическая корректировка остановлена — нужна проверка администратора."
                ),
            }
            await call.message.answer(f"⚠️ {messages.get(str(exc), str(exc))}")
            return
        except Exception:
            logging.exception("Could not create/send agent report")
            await call.message.answer(
                "⚠️ Отчёт сформировать или отправить не удалось. Данные не удалены; "
                "после устранения ошибки отправку можно повторить."
            )
            return

        tutor_name = (await db.get_all_tutors()).get(
            tutor_id, {}
        ).get("name", "Репетитор")
        fresh = await get_report(tutor_id, year, month, period_no)
        current = fresh[0] if fresh else report
        await call.message.answer(
            ("✅ Отчёт сформирован и отправлен." if created
             else "✅ Отправка отчёта завершена.")
            + "\n\n"
            + summary_text(current, str(tutor_name))
            + f"\nАрхивная группа: {'✅' if result['archive_sent'] else '❌'}"
            + f"\nРепетитор: {'✅' if result['tutor_sent'] else '❌'}"
        )

    legacy._agent_reports_installed = True
