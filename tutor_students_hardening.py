"""Cross-platform tutor-panel student grouping without raw-id collisions."""

from __future__ import annotations

from booking_cards import tutor_list
from tutor_students_rules import group_tutor_students, platform_label
from booking_visibility_rules import payment_delivery_note

_runtime_vk_legacy = None


def _booking_platform_label(booking: dict) -> str:
    return "VK" if str(booking.get("user_platform") or "telegram").lower() == "vk" else "TG"


async def _telegram_show_students(call, bot):
    tid = int(call.data.split("_")[-1])
    actual_tid = await get_tutor_by_telegram_id(call.from_user.id)
    if actual_tid != tid:
        await safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
        return

    await safe_answer(call)
    students = _group_tutor_students(await get_all_bookings(), tid)
    text, keyboard = _render_tutor_records(students, tid)
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode=None)


async def _vk_show_students(event):
    legacy = _runtime_vk_legacy
    if legacy is None:
        raise RuntimeError("VK tutor-students hardening is not installed")

    tid = int(event.payload["cmd"].split("_")[-1])
    if await legacy.get_tutor_by_vk_id(event.user_id) != tid:
        await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
        return

    bookings = await legacy.get_all_bookings()
    students = group_tutor_students(bookings, tid)
    if not students:
        kb = legacy.Keyboard(inline=True)
        kb.add(legacy.Callback("🔙 Назад", payload={"cmd": f"back_to_tutor_panel_{tid}"}))
        await legacy.edit_event_message(
            event, "У вас пока нет активных записей.", keyboard=kb.get_json()
        )
        return

    text = "📋 Ваши ученики:\n\n"
    kb = legacy.Keyboard(inline=True)
    for student in students:
        identity_platforms = platform_label(student["platforms"])
        text += (
            f"👤 {student['username']} · {identity_platforms} "
            f"(проведено: {student['completed_lessons']})\n"
        )
        for bid, booking in student["active_bookings"]:
            status_emoji = "✅" if booking["status"] == "paid" else "⏳"
            messenger = _booking_platform_label(booking)
            text += (
                f"  {status_emoji} №{bid} [{messenger}] {booking['date']} {booking['time_slot']} "
                f"– {booking['subject']}\n"
            )
            if delivery_note := payment_delivery_note(booking):
                text += f"  {delivery_note}\n"
            if booking["status"] == "pending":
                kb.add(
                    legacy.Callback(
                        f"✅ Подтвердить №{bid}",
                        payload={"cmd": f"tutor_confirm_{bid}"},
                    )
                )
                kb.add(legacy.Callback(f"❌ Отклонить №{bid}", payload={"cmd": f"tutor_reject_{bid}"}))
                kb.row()
            elif booking["status"] == "confirmed":
                dt = legacy.datetime.strptime(
                    booking["date"] + " " + booking["time_slot"].split("-")[0],
                    "%d.%m.%Y %H:%M",
                )
                if (dt - legacy.now_msk_naive()) > legacy.timedelta(hours=24):
                    kb.add(legacy.Callback(f"❌ Отменить №{bid}", payload={"cmd": f"tutor_cancel_{bid}"}))
                    kb.add(legacy.Callback(f"🔄 Перенести №{bid}", payload={"cmd": f"tutor_reschedule_{bid}"}))
                    kb.row()
        text += "\n"

    kb.add(legacy.Callback("🔙 Назад", payload={"cmd": f"back_to_tutor_panel_{tid}"}))
    await legacy.edit_event_message(event, text, keyboard=kb.get_json())


def install_telegram_tutor_students_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_tutor_students_identity_hardened", False):
        return

    legacy._group_tutor_students = group_tutor_students
    legacy._render_tutor_records = tutor_list
    legacy._tutor_platform_label = platform_label
    legacy._tutor_booking_platform_label = _booking_platform_label
    if legacy.show_students.__code__.co_freevars or _telegram_show_students.__code__.co_freevars:
        raise RuntimeError("Telegram tutor student-panel replacement cannot use closures")
    legacy.show_students.__code__ = _telegram_show_students.__code__
    legacy._tutor_students_identity_hardened = True


def install_vk_tutor_students_hardening(app) -> None:
    global _runtime_vk_legacy
    legacy = app.legacy
    if getattr(legacy, "_tutor_students_identity_hardened", False):
        return
    _runtime_vk_legacy = legacy
    # VK's universal callback dispatcher resolves show_students from module globals
    # at click time, so replacing the function object is safe.
    legacy.show_students = _vk_show_students
    legacy._tutor_students_identity_hardened = True
