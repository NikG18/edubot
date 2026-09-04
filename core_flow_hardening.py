"""Late UI/runtime fixes for manually reproduced booking flow defects."""

from __future__ import annotations

from types import FunctionType

import database as db
from booking_visibility_rules import can_offer_separate_payment, is_trial_booking
from subscription_resolution import resolve_booking_subscription


class _CallbackMessageProxy:
    """Callback messages are authored by the bot; use the clicker as from_user."""

    def __init__(self, message, user):
        self._message = message
        self.from_user = user

    async def answer(self, *args, **kwargs):
        return await self._message.answer(*args, **kwargs)


def _clone_function(fn):
    return FunctionType(
        fn.__code__, fn.__globals__, name=fn.__name__,
        argdefs=fn.__defaults__, closure=fn.__closure__,
    )


def _subscription_block_text(reason: str | None, booking_id: int) -> str:
    if reason in {"subscription_payment_pending", "payment_status_unknown"}:
        return (
            "🎟 Для этого занятия уже ожидается оплата абонемента. "
            "Отдельная оплата полной стоимости не создаётся. "
            "После подтверждения оплаты абонемента откройте раздел «Оплата» ещё раз."
        )
    return (
        "⚠️ Найден абонемент, требующий проверки. Чтобы исключить двойное списание, "
        f"отдельная оплата занятия #{booking_id} заблокирована. Обратитесь в поддержку."
    )


async def _tg_back_to_my_records(call, state):
    await safe_answer(call)
    await state.clear()
    proxy = _core_callback_message_proxy(call.message, call.from_user)
    await _tg_render_records(proxy)


async def _tg_pay_booking_list(call):
    await safe_answer(call)
    user_id = call.from_user.id
    bookings = await _core_db.get_bookings_for_account(
        "telegram", user_id, statuses={"confirmed"}
    )
    tutors = await get_all_tutors()
    payable = []
    notes = []
    for booking_id, booking in bookings.items():
        if not _core_can_offer_payment(booking):
            continue
        resolved = await _core_resolve_subscription(
            legacy, int(booking_id), int(user_id),
            actor_type="student", allowed_statuses={"confirmed"},
        )
        tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Преподаватель")
        if resolved is None:
            payable.append((booking_id, booking, tutor_name))
        elif resolved.get("error"):
            notes.append(
                f"🎟 {tutor_name} · {booking['date']} {booking['time_slot']}: "
                "ожидается/проверяется абонемент; полная оплата заблокирована."
            )
        else:
            notes.append(
                f"✅ {tutor_name} · {booking['date']} {booking['time_slot']}: "
                "оплачено из абонемента."
            )

    lines = []
    buttons = []
    if payable:
        lines.append("Выберите занятие для оплаты:")
        for booking_id, booking, tutor_name in payable:
            lines.append(
                f"\n👨‍🏫 {tutor_name}\n📚 {booking['subject']}\n"
                f"📅 {booking['date']} {booking['time_slot']}"
            )
            buttons.append([InlineKeyboardButton(
                text=f"Оплатить {tutor_name} {booking['date']} {booking['time_slot']}"[:64],
                callback_data=f"pay_single_{booking_id}",
            )])
    else:
        lines.append("У вас нет занятий, требующих отдельной оплаты.")
    if notes:
        lines.append("\n" + "\n".join(notes))
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_payment_menu")])
    await call.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


async def _tg_pay_single(call, bot):
    try:
        booking_id = int(call.data.rsplit("_", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await safe_answer(call, "Некорректная запись.", show_alert=True)
        return
    booking = await _core_db.get_booking(booking_id)
    if not _core_can_offer_payment(booking):
        if _core_is_trial(booking):
            await safe_answer(call, "Пробное занятие бесплатно и не требует оплаты.", show_alert=True)
        else:
            await safe_answer(call, "Запись уже оплачена или недоступна для оплаты.", show_alert=True)
        return
    if not await _core_db.account_owns_booking("telegram", call.from_user.id, booking):
        await safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
        return

    resolved = await _core_resolve_subscription(
        legacy, booking_id, call.from_user.id,
        actor_type="student", allowed_statuses={"confirmed"},
    )
    if resolved is not None:
        if resolved.get("error"):
            await call.message.edit_text(
                _core_subscription_block_text(resolved.get("error"), booking_id)
            )
        else:
            await call.message.edit_text(
                "✅ Это занятие оплачено из вашего абонемента.\n"
                f"Осталось занятий: {resolved.get('remaining_lessons', 0)}."
            )
        return
    return await _core_original_pay_single(call, bot)


async def _tg_admin_unpaid(call):
    if call.from_user.id != ADMING_ID:
        await safe_answer(call, "⛔ Только администратор", show_alert=True)
        return
    bookings = await _core_db.get_all_bookings()
    unpaid = [
        (booking_id, booking) for booking_id, booking in bookings.items()
        if _core_can_offer_payment(booking)
    ]
    if not unpaid:
        await call.message.edit_text(
            "Нет регулярных неоплаченных заявок.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel_open")]
            ]),
        )
        return
    tutors = await get_all_tutors()
    grouped = {}
    for booking_id, booking in unpaid:
        tutor_id = booking["tutor_id"]
        data = grouped.setdefault(
            tutor_id,
            {"name": tutors.get(tutor_id, {}).get("name", "Неизвестный"), "rows": []},
        )
        data["rows"].append((booking_id, booking))
    lines = ["Регулярные занятия, ожидающие оплаты:\n"]
    buttons = []
    for data in grouped.values():
        lines.append(f"\n👨‍🏫 {data['name']}:")
        for booking_id, booking in data["rows"]:
            lines.append(
                f"  • {booking['username']}: {booking['subject']}, "
                f"{booking['date']} {booking['time_slot']}"
            )
            buttons.append([InlineKeyboardButton(
                text=f"✅ Подтвердить: {booking['username']} {booking['date']} {booking['time_slot']}"[:64],
                callback_data=f"admin_confirm_payment_{booking_id}",
            )])
    buttons.append([InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel_open")])
    await call.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


async def _tg_admin_confirm_payment(call, bot):
    try:
        booking_id = int(call.data.rsplit("_", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await safe_answer(call, "Некорректная запись.", show_alert=True)
        return
    booking = await _core_db.get_booking(booking_id)
    if _core_is_trial(booking):
        await safe_answer(
            call,
            "Пробное занятие бесплатно: подтверждать оплату для него нельзя.",
            show_alert=True,
        )
        return
    return await _core_original_admin_confirm_payment(call, bot)


async def _tg_admin_bookings_menu(call):
    await legacy.safe_answer(call)
    if call.from_user.id != legacy.ADMING_ID:
        return
    keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
        [legacy.InlineKeyboardButton(text="🎓 Пробные занятия", callback_data="admin_bookings_status_trials")],
        [legacy.InlineKeyboardButton(text="🟠 Ожидают подтверждения", callback_data="admin_bookings_status_pending")],
        [legacy.InlineKeyboardButton(text="🟡 Ожидают оплаты", callback_data="admin_bookings_status_confirmed")],
        [legacy.InlineKeyboardButton(text="🟢 Оплаченные", callback_data="admin_bookings_status_paid")],
        [legacy.InlineKeyboardButton(text="✅ Проведённые", callback_data="admin_bookings_status_completed")],
        [legacy.InlineKeyboardButton(text="🔴 Отменённые", callback_data="admin_bookings_status_cancelled")],
        [legacy.InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel_open")],
    ])
    await call.message.edit_text("📚 Управление занятиями", reply_markup=keyboard)


async def _vk_render_payment_menu(legacy, user_id: int, *, message=None, event=None):
    bookings = await db.get_bookings_for_account("vk", int(user_id), statuses={"confirmed"})
    kb = legacy.Keyboard(inline=True)
    lines = []
    payable = []
    for booking_id, booking in bookings.items():
        if not can_offer_separate_payment(booking):
            continue
        resolved = await resolve_booking_subscription(
            legacy, int(booking_id), int(user_id),
            actor_type="student", allowed_statuses={"confirmed"},
        )
        if resolved is None:
            payable.append((booking_id, booking))
        elif resolved.get("error"):
            lines.append(
                f"🎟 {booking['date']} {booking['time_slot']} — проверяется абонемент; полная оплата заблокирована."
            )
        else:
            lines.append(
                f"✅ {booking['date']} {booking['time_slot']} — оплачено из абонемента."
            )

    if payable:
        tutors = await legacy.get_all_tutors()
        lines.insert(0, "Выберите занятие для оплаты через Т-Банк:")
        for booking_id, booking in payable:
            tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Преподаватель")
            lines.append(
                f"\n👨‍🏫 {tutor_name}\n📚 {booking['subject']}\n"
                f"📅 {booking['date']} {booking['time_slot']}"
            )
            kb.add(legacy.Callback(
                f"💳 Оплатить: {tutor_name} {booking['date']}",
                payload={"cmd": "qr", "booking_id": int(booking_id)},
            ))
            kb.row()
    elif not lines:
        lines.append("У вас нет занятий, требующих отдельной оплаты.")

    kb.add(legacy.Callback(
        "🎟 Купить абонемент", payload={"cmd": "qr", "action": "subscription_start"}
    ))
    kb.row()
    kb.add(legacy.Callback("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    text = "\n".join(lines)
    if event is not None:
        await legacy.edit_event_message(event, text, keyboard=kb.get_json())
    else:
        await message.answer(text, keyboard=kb.get_json())


def install_telegram_core_flow_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_core_flow_tg_hardened", False):
        return

    legacy.legacy = legacy
    legacy._core_db = db
    legacy._core_callback_message_proxy = _CallbackMessageProxy
    legacy._core_can_offer_payment = can_offer_separate_payment
    legacy._core_is_trial = is_trial_booking
    legacy._core_resolve_subscription = resolve_booking_subscription
    legacy._core_subscription_block_text = _subscription_block_text
    legacy._core_original_pay_single = _clone_function(legacy.pay_single_booking)
    legacy._core_original_admin_confirm_payment = _clone_function(legacy.admin_confirm_payment_handler)

    replacements = (
        (legacy.back_to_my_records, _tg_back_to_my_records),
        (legacy.pay_booking_list, _tg_pay_booking_list),
        (legacy.pay_single_booking, _tg_pay_single),
        (legacy.admin_show_unpaid_bookings, _tg_admin_unpaid),
        (legacy.admin_confirm_payment_handler, _tg_admin_confirm_payment),
    )
    for target, replacement in replacements:
        if target.__code__.co_freevars or replacement.__code__.co_freevars:
            raise RuntimeError(f"Telegram core-flow replacement has closure: {replacement.__name__}")
        target.__code__ = replacement.__code__

    if app.admin_bookings_menu.__code__.co_freevars or _tg_admin_bookings_menu.__code__.co_freevars:
        raise RuntimeError("Telegram admin booking-menu replacement has closure")
    app.admin_bookings_menu.__code__ = _tg_admin_bookings_menu.__code__
    legacy._core_flow_tg_hardened = True


def install_vk_core_flow_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_core_flow_vk_hardened", False):
        return

    original_qr = legacy.qr

    async def render_payment(user_id: int, message=None, event=None):
        return await _vk_render_payment_menu(legacy, user_id, message=message, event=event)

    async def hardened_qr(event):
        payload = event.payload or {}
        if payload.get("action"):
            return await original_qr(event)
        try:
            booking_id = int(payload.get("booking_id"))
        except (TypeError, ValueError):
            return await original_qr(event)
        booking = await db.get_booking(booking_id)
        if is_trial_booking(booking):
            await legacy.answer_event(
                event, "Пробное занятие бесплатно и не требует оплаты.", snackbar=True
            )
            return
        if booking and booking.get("status") == "confirmed":
            resolved = await resolve_booking_subscription(
                legacy, booking_id, event.user_id,
                actor_type="student", allowed_statuses={"confirmed"},
            )
            if resolved is not None:
                if resolved.get("error"):
                    await legacy.edit_event_message(
                        event, _subscription_block_text(resolved.get("error"), booking_id)
                    )
                else:
                    await legacy.edit_event_message(
                        event,
                        "✅ Это занятие оплачено из вашего абонемента.\n"
                        f"Осталось занятий: {resolved.get('remaining_lessons', 0)}.",
                    )
                return
        return await original_qr(event)

    legacy._vk_render_payment_menu = render_payment
    legacy.qr = hardened_qr
    legacy._core_flow_vk_hardened = True
