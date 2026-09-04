"""Late runtime fixes for the core booking/payment flows found during manual testing.

This layer deliberately installs after the older compatibility modules.  It fixes
callback identity, keeps free trials out of payment surfaces, and prevents a regular
full-price charge while a matching subscription is active or still being confirmed.
"""

from __future__ import annotations

import logging

import database as _db
import payments as _payments
import subscription_hardening as _subs
import tutor_confirmation_hardening as _confirmation
from booking_visibility_rules import can_offer_separate_payment, is_trial_booking


class _CallbackMessageProxy:
    """Use callback clicker identity while delegating Telegram message.answer()."""

    def __init__(self, message, user):
        self._message = message
        self.from_user = user

    async def answer(self, *args, **kwargs):
        return await self._message.answer(*args, **kwargs)


def _subscription_error_text(reason: str | None, booking_id: int) -> str:
    reason = str(reason or "subscription_check_failed")
    if reason in {"subscription_payment_pending", "payment_status_unknown"}:
        return (
            "🎟 Для этого преподавателя и предмета уже оформляется абонемент. "
            "Его платёж ещё не подтверждён банком, поэтому отдельная оплата занятия "
            "не создаётся. Проверьте оплату абонемента чуть позже."
        )
    if reason == "duplicate_pending_subscriptions":
        return (
            "⚠️ Найдено несколько незавершённых оплат абонемента. Отдельная оплата "
            f"занятия #{booking_id} заблокирована, чтобы не списать деньги дважды. "
            "Обратитесь в поддержку."
        )
    return (
        "⚠️ Не удалось безопасно применить найденный абонемент. Отдельная оплата "
        f"занятия #{booking_id} не создавалась. Обратитесь в поддержку."
    )


async def _pending_subscription_state(booking: dict) -> dict | None:
    """Reconcile one matching pending package purchase before any ordinary charge."""
    student_id = booking.get("student_id")
    if not student_id:
        return None
    await _subs.ensure_subscription_schema()
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM pending_subscriptions
            WHERE student_id=$1 AND tutor_id=$2 AND subject=$3
            ORDER BY id DESC
            """,
            int(student_id), int(booking["tutor_id"]), str(booking["subject"]),
        )
    if not rows:
        return None
    if len(rows) > 1:
        logging.error(
            "Multiple pending package payments for booking=%s student=%s tutor=%s subject=%s",
            booking.get("id"), student_id, booking.get("tutor_id"), booking.get("subject"),
        )
        return {"error": "duplicate_pending_subscriptions"}

    pending = rows[0]
    payment_id = str(pending["payment_id"] or "")
    if not payment_id:
        return {"error": "subscription_payment_pending"}

    state = await _payments.check_payment(payment_id)
    if not state.get("Success"):
        return {"error": "payment_status_unknown", "payment_id": payment_id}
    status = str(state.get("Status") or "").upper()
    if status in {"CONFIRMED", "AUTHORIZED"}:
        activated = await _subs.activate_subscription(payment_id)
        if not activated:
            return {"error": "subscription_activation_failed", "payment_id": payment_id}
        return {"activated": True, "payment_id": payment_id}
    if status in {"REJECTED", "CANCELED"}:
        async with _db._legacy.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pending_subscriptions WHERE id=$1",
                int(pending["id"]),
            )
        return None
    return {"error": "subscription_payment_pending", "payment_id": payment_id, "status": status}


async def resolve_subscription_for_booking(
    legacy,
    booking_id: int,
    actor_id: int,
    *,
    actor_type: str,
    allowed_statuses: set[str],
) -> dict | None:
    """Use an existing package unit, block on a pending package, or return None.

    None is the *only* result that permits the caller to create an ordinary payment.
    """
    booking = await _db.get_booking(int(booking_id))
    if not booking or is_trial_booking(booking) or booking.get("status") not in allowed_statuses:
        return None

    pending = await _pending_subscription_state(booking)
    if pending and pending.get("error"):
        return pending

    # Activation may have created a new subscriptions row; lock and re-read booking.
    await _subs.ensure_subscription_schema()
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM bookings WHERE id=$1 FOR UPDATE", int(booking_id)
            )
            if not current or current["status"] not in allowed_statuses or current["booking_type"] == "trial":
                return None

            existing = await conn.fetchrow(
                "SELECT * FROM subscription_usages WHERE booking_id=$1 FOR UPDATE",
                int(booking_id),
            )
            if existing and existing["status"] in {"reserved", "consumed"}:
                subscription = await conn.fetchrow(
                    "SELECT remaining_lessons FROM subscriptions WHERE id=$1",
                    existing["subscription_id"],
                )
                await conn.execute(
                    """
                    UPDATE bookings
                    SET status='paid',subscription_id=$1,subscription_unit_index=$2,
                        subscription_unit_amount=$3,amount=$3,updated_at=NOW()
                    WHERE id=$4
                    """,
                    existing["subscription_id"], existing["unit_index"],
                    existing["amount_kop"], int(booking_id),
                )
                result = {
                    **dict(existing),
                    "remaining_lessons": int(subscription["remaining_lessons"] or 0) if subscription else 0,
                    "already_reserved": True,
                }
            elif existing and existing["status"] == "released":
                return {"error": "released_booking_usage"}
            else:
                subscription = await conn.fetchrow(
                    """
                    SELECT * FROM subscriptions
                    WHERE student_id=$1 AND tutor_id=$2 AND subject=$3
                      AND remaining_lessons>0
                    ORDER BY
                      CASE WHEN active=1 AND payment_id IS NOT NULL THEN 0 ELSE 1 END,
                      activated_at NULLS LAST,
                      id
                    LIMIT 1 FOR UPDATE
                    """,
                    current["student_id"], current["tutor_id"], current["subject"],
                )
                if not subscription:
                    return None
                if not subscription["payment_id"]:
                    return {"error": "legacy_subscription_requires_migration"}
                if int(subscription["active"] or 0) != 1:
                    return {"error": "subscription_quarantined"}
                if not _confirmation.subscription_flow._subscription_fiscal_snapshot_complete(subscription):
                    return {"error": "subscription_fiscal_snapshot_incomplete"}

                reservation = await _subs.reserve_locked_subscription_unit(
                    conn, int(booking_id), subscription
                )
                if not reservation:
                    return {"error": "subscription_ledger_inconsistent"}
                old_status = str(current["status"])
                await conn.execute(
                    """
                    UPDATE bookings
                    SET status='paid',subscription_id=$1,subscription_unit_index=$2,
                        subscription_unit_amount=$3,amount=$3,updated_at=NOW()
                    WHERE id=$4
                    """,
                    subscription["id"], reservation["unit_index"],
                    reservation["amount_kop"], int(booking_id),
                )
                try:
                    await _db._add_booking_event(
                        conn, int(booking_id), "subscription_reserved", old_status, "paid",
                        str(actor_type), int(actor_id),
                        {
                            "subscription_id": int(subscription["id"]),
                            "unit_index": int(reservation["unit_index"]),
                            "amount_kop": int(reservation["amount_kop"]),
                            "remaining_lessons": int(reservation["remaining_lessons"]),
                        },
                    )
                except Exception:
                    logging.exception("Could not record recovered subscription reservation booking=%s", booking_id)
                result = {**reservation, "already_reserved": False}

    await _db._sync_booking_record_safely(int(booking_id))
    return result


async def _telegram_back_to_my_records(call, state):
    await safe_answer(call)
    await state.clear()
    proxy = _core_callback_message_proxy(call.message, call.from_user)
    await _tg_render_records(proxy)


async def _telegram_pay_booking_list(call):
    await safe_answer(call)
    user_id = call.from_user.id
    bookings = await _core_db.get_bookings_for_account(
        "telegram", user_id, statuses={"confirmed"}
    )
    tutors = await get_all_tutors()
    payable = []
    notes = []
    for bid, booking in bookings.items():
        if not _core_can_offer_separate_payment(booking):
            continue
        resolved = await _core_resolve_subscription(
            legacy, int(bid), user_id,
            actor_type="student", allowed_statuses={"confirmed"},
        )
        tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Преподаватель")
        if resolved is None:
            payable.append((bid, booking, tutor_name))
        elif resolved.get("error"):
            notes.append(
                f"🎟 {tutor_name} · {booking['date']} {booking['time_slot']}: "
                "отдельная оплата заблокирована — проверяется абонемент."
            )
        else:
            notes.append(
                f"✅ {tutor_name} · {booking['date']} {booking['time_slot']}: "
                "занятие оплачено из абонемента."
            )

    keyboard = []
    lines = []
    if payable:
        lines.append("Выберите занятие для оплаты:")
        for bid, booking, tutor_name in payable:
            lines.append(
                f"\n👨‍🏫 {tutor_name}\n📚 {booking['subject']}\n"
                f"📅 {booking['date']} {booking['time_slot']}"
            )
            keyboard.append([InlineKeyboardButton(
                text=f"Оплатить {tutor_name} {booking['date']} {booking['time_slot']}"[:64],
                callback_data=f"pay_single_{bid}",
            )])
    else:
        lines.append("У вас нет занятий, требующих отдельной оплаты.")
    if notes:
        lines.append("\n" + "\n".join(notes))
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_payment_menu")])
    await call.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


async def _telegram_pay_single(call, bot):
    try:
        bid = int(call.data.rsplit("_", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await safe_answer(call, "Некорректная запись.", show_alert=True)
        return
    booking = await _core_db.get_booking(bid)
    if not _core_can_offer_separate_payment(booking):
        if _core_is_trial_booking(booking):
            await safe_answer(call, "Пробное занятие бесплатно и не требует оплаты.", show_alert=True)
        else:
            await safe_answer(call, "Запись уже оплачена или недоступна для оплаты.", show_alert=True)
        return
    if not await _core_db.account_owns_booking("telegram", call.from_user.id, booking):
        await safe_answer(call, "⛔ Доступ запрещён.", show_alert=True)
        return

    resolved = await _core_resolve_subscription(
        legacy, bid, call.from_user.id,
        actor_type="student", allowed_statuses={"confirmed"},
    )
    if resolved is not None:
        if resolved.get("error"):
            await call.message.edit_text(_core_subscription_error_text(resolved.get("error"), bid))
        else:
            await call.message.edit_text(
                "✅ Это занятие оплачено из вашего абонемента.\n"
                f"Осталось занятий: {resolved.get('remaining_lessons', 0)}."
            )
        return
    return await _core_original_pay_single(call, bot)


async def _telegram_admin_unpaid(call):
    if call.from_user.id != ADMING_ID:
        await safe_answer(call, "⛔ Только администратор", show_alert=True)
        return
    bookings = await _core_db.get_all_bookings()
    unpaid = [
        (bid, booking) for bid, booking in bookings.items()
        if _core_can_offer_separate_payment(booking)
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
    for bid, booking in unpaid:
        tid = booking["tutor_id"]
        grouped.setdefault(
            tid,
            {"tutor_name": tutors.get(tid, {}).get("name", "Неизвестный"), "bookings": []},
        )["bookings"].append((bid, booking))
    lines = ["Регулярные занятия, ожидающие оплаты:\n"]
    keyboard = []
    for data in grouped.values():
        lines.append(f"\n👨‍🏫 {data['tutor_name']}:")
        for bid, booking in data["bookings"]:
            lines.append(
                f"  • {booking['username']}: {booking['subject']}, "
                f"{booking['date']} {booking['time_slot']}"
            )
            keyboard.append([InlineKeyboardButton(
                text=f"✅ Подтвердить: {booking['username']} {booking['date']} {booking['time_slot']}"[:64],
                callback_data=f"admin_confirm_payment_{bid}",
            )])
    keyboard.append([InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel_open")])
    await call.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


async def _telegram_admin_confirm_payment(call, bot):
    try:
        bid = int(call.data.rsplit("_", 1)[1])
    except (AttributeError, TypeError, ValueError):
        await safe_answer(call, "Некорректная запись.", show_alert=True)
        return
    booking = await _core_db.get_booking(bid)
    if _core_is_trial_booking(booking):
        await safe_answer(
            call, "Пробное занятие бесплатно: подтверждать оплату для него нельзя.", show_alert=True
        )
        return
    return await _core_original_admin_confirm_payment(call, bot)


async def _telegram_admin_bookings_menu(call):
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


async def _telegram_tutor_confirm(call, bot, state):
    await safe_answer(call)
    try:
        booking_id = int(call.data.rsplit("_", 1)[1])
    except (AttributeError, TypeError, ValueError, IndexError):
        await call.message.edit_text("Некорректная кнопка подтверждения.")
        return
    booking = await _core_db.get_booking(booking_id)
    if not booking:
        await call.message.edit_text("Заявка не найдена.")
        return
    if not await _require_booking_tutor(call, booking):
        return
    if booking.get("status") != "pending":
        await call.message.edit_text("Заявка уже обработана.")
        return
    if _core_is_trial_booking(booking):
        changed = await _core_confirmation._confirm_trial(legacy, booking, call.from_user.id)
        await state.clear()
        await call.message.edit_text(
            "✅ Бесплатное пробное занятие подтверждено. Оплата не требуется."
            if changed else "Заявка уже обработана."
        )
        return

    resolved = await _core_resolve_subscription(
        legacy, booking_id, call.from_user.id,
        actor_type="tutor", allowed_statuses={"pending"},
    )
    if resolved is not None:
        await state.clear()
        if resolved.get("error"):
            text = _core_subscription_error_text(resolved.get("error"), booking_id)
            await call.message.edit_text(text)
            await send_to_user(
                booking["user_id"], booking.get("user_platform", "telegram"), text
            )
        else:
            await send_to_user(
                booking["user_id"], booking.get("user_platform", "telegram"),
                "✅ Занятие подтверждено преподавателем и оплачено из абонемента.\n"
                f"📚 {booking['subject']}\n📅 {booking['date']} 🕒 {booking['time_slot']}\n"
                f"Осталось занятий: {resolved.get('remaining_lessons', 0)}."
            )
            await call.message.edit_text(
                "✅ Занятие подтверждено. Оплата списана из абонемента.\n"
                f"Остаток: {resolved.get('remaining_lessons', 0)} занятий."
            )
        return

    result = await _core_confirmation._confirm_regular(
        legacy, booking, call.from_user.id, telegram_bot=bot, source=call
    )
    await state.clear()
    if result["kind"] == "email_required":
        await call.message.edit_text("✅ Заявка подтверждена. Ожидаем e-mail ученика для создания платежа.")
    elif result["kind"] == "payment_created":
        await call.message.edit_text("✅ Заявка подтверждена. Ссылка на оплату отправлена ученику.")
    elif result["kind"] == "payment_created_link_pending":
        await call.message.edit_text(
            "⚠️ Платёж создан, но ссылку ученику доставить не удалось. "
            "Новый платёж создавать не нужно: ученик может снова открыть раздел «Оплата»."
        )
    elif result["kind"] == "subscription":
        await call.message.edit_text(
            "✅ Занятие подтверждено. Оплата списана из абонемента.\n"
            f"Остаток: {result.get('remaining', 0)} занятий."
        )
    else:
        await call.message.edit_text(
            "⚠️ Заявка подтверждена, но платёж создать не удалось. "
            "Ученик может повторить оплату из раздела «Оплата»."
        )


async def _vk_tutor_confirm(legacy, event):
    try:
        booking_id = int(event.payload["cmd"].rsplit("_", 1)[1])
    except (KeyError, TypeError, ValueError, IndexError):
        await legacy.answer_event(event, "Некорректная кнопка подтверждения.", snackbar=True)
        return
    booking = await _db.get_booking(booking_id)
    if not booking:
        await legacy.edit_event_message(event, "Заявка не найдена.")
        return
    tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
    if tutor_id != booking.get("tutor_id"):
        await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
        return
    if booking.get("status") != "pending":
        await legacy.edit_event_message(event, "Заявка уже обработана.")
        return
    if is_trial_booking(booking):
        changed = await _confirmation._confirm_trial(legacy, booking, event.user_id)
        await legacy.edit_event_message(
            event,
            "✅ Бесплатное пробное занятие подтверждено. Оплата не требуется."
            if changed else "Заявка уже обработана.",
        )
        return

    resolved = await resolve_subscription_for_booking(
        legacy, booking_id, event.user_id,
        actor_type="tutor", allowed_statuses={"pending"},
    )
    if resolved is not None:
        if resolved.get("error"):
            text = _subscription_error_text(resolved.get("error"), booking_id)
            await legacy.edit_event_message(event, text)
            await legacy.send_to_user(
                booking["user_id"], booking.get("user_platform", "vk"), text
            )
        else:
            await legacy.send_to_user(
                booking["user_id"], booking.get("user_platform", "vk"),
                "✅ Занятие подтверждено преподавателем и оплачено из абонемента.\n"
                f"📚 {booking['subject']}\n📅 {booking['date']} 🕒 {booking['time_slot']}\n"
                f"Осталось занятий: {resolved.get('remaining_lessons', 0)}."
            )
            await legacy.edit_event_message(
                event,
                "✅ Занятие подтверждено. Оплата списана из абонемента.\n"
                f"Остаток: {resolved.get('remaining_lessons', 0)} занятий.",
            )
        return

    result = await _confirmation._confirm_regular(
        legacy, booking, event.user_id, telegram_bot=None, source=event
    )
    messages = {
        "email_required": "✅ Заявка подтверждена. Ожидаем e-mail ученика для создания платежа.",
        "payment_created": "✅ Заявка подтверждена. Ссылка на оплату отправлена ученику.",
        "payment_created_link_pending": (
            "⚠️ Платёж создан, но ссылку ученику доставить не удалось. "
            "Новый платёж создавать не нужно: ученик может снова открыть раздел «Оплата»."
        ),
        "payment_failed": (
            "⚠️ Заявка подтверждена, но платёж создать не удалось. "
            "Ученик может повторить оплату из раздела «Оплата»."
        ),
    }
    await legacy.edit_event_message(
        event,
        messages.get(result.get("kind"), "✅ Заявка обработана."),
    )


async def _vk_render_payment_menu(legacy, user_id: int, *, message=None, event=None):
    bookings = await _db.get_bookings_for_account("vk", int(user_id), statuses={"confirmed"})
    kb = legacy.Keyboard(inline=True)
    lines = []
    payable = []
    for bid, booking in bookings.items():
        if not can_offer_separate_payment(booking):
            continue
        resolved = await resolve_subscription_for_booking(
            legacy, int(bid), int(user_id),
            actor_type="student", allowed_statuses={"confirmed"},
        )
        if resolved is None:
            payable.append((bid, booking))
        elif resolved.get("error"):
            lines.append(
                f"🎟 {booking['date']} {booking['time_slot']} — отдельная оплата заблокирована: проверяется абонемент."
            )
        else:
            lines.append(
                f"✅ {booking['date']} {booking['time_slot']} — оплачено из абонемента."
            )

    if payable:
        tutors = await legacy.get_all_tutors()
        lines.insert(0, "Выберите занятие для оплаты через Т-Банк:")
        for bid, booking in payable:
            tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Преподаватель")
            lines.append(
                f"\n👨‍🏫 {tutor_name}\n📚 {booking['subject']}\n"
                f"📅 {booking['date']} {booking['time_slot']}"
            )
            kb.add(legacy.Callback(
                f"💳 Оплатить: {tutor_name} {booking['date']}",
                payload={"cmd": "qr", "booking_id": int(bid)},
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


def install_telegram_core_booking_flow_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_core_booking_flow_tg_hardened", False):
        return

    # Inject names used by closure-free code transplanted into Bot_test_legacy globals.
    legacy.legacy = legacy
    legacy._core_db = _db
    legacy._core_callback_message_proxy = _CallbackMessageProxy
    legacy._core_can_offer_separate_payment = can_offer_separate_payment
    legacy._core_is_trial_booking = is_trial_booking
    legacy._core_resolve_subscription = resolve_subscription_for_booking
    legacy._core_subscription_error_text = _subscription_error_text
    legacy._core_confirmation = _confirmation
    legacy._core_original_pay_single = legacy.pay_single_booking
    legacy._core_original_admin_confirm_payment = legacy.admin_confirm_payment_handler

    replacements = (
        (legacy.back_to_my_records, _telegram_back_to_my_records),
        (legacy.pay_booking_list, _telegram_pay_booking_list),
        (legacy.pay_single_booking, _telegram_pay_single),
        (legacy.admin_show_unpaid_bookings, _telegram_admin_unpaid),
        (legacy.admin_confirm_payment_handler, _telegram_admin_confirm_payment),
        (legacy.tutor_confirm_booking, _telegram_tutor_confirm),
    )
    for target, replacement in replacements:
        if target.__code__.co_freevars or replacement.__code__.co_freevars:
            raise RuntimeError(f"core Telegram replacement cannot use closures: {replacement.__name__}")
        target.__code__ = replacement.__code__

    # admin_bookings_menu is registered in Bot_test.py rather than Bot_test_legacy.py.
    if app.admin_bookings_menu.__code__.co_freevars or _telegram_admin_bookings_menu.__code__.co_freevars:
        raise RuntimeError("admin bookings menu replacement cannot use closures")
    app.admin_bookings_menu.__code__ = _telegram_admin_bookings_menu.__code__

    legacy._core_booking_flow_tg_hardened = True


def install_vk_core_booking_flow_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_core_booking_flow_vk_hardened", False):
        return

    original_qr = legacy.qr

    async def render_payment(user_id: int, message=None, event=None):
        return await _vk_render_payment_menu(
            legacy, user_id, message=message, event=event
        )

    async def hardened_qr(event):
        payload = event.payload or {}
        # Subscription purchase callbacks already belong to vk_payment_hardening.
        if payload.get("action"):
            return await original_qr(event)
        try:
            booking_id = int(payload.get("booking_id"))
        except (TypeError, ValueError):
            return await original_qr(event)
        booking = await _db.get_booking(booking_id)
        if is_trial_booking(booking):
            await legacy.answer_event(
                event, "Пробное занятие бесплатно и не требует оплаты.", snackbar=True
            )
            return
        if booking and booking.get("status") == "confirmed":
            resolved = await resolve_subscription_for_booking(
                legacy, booking_id, event.user_id,
                actor_type="student", allowed_statuses={"confirmed"},
            )
            if resolved is not None:
                if resolved.get("error"):
                    await legacy.edit_event_message(
                        event, _subscription_error_text(resolved.get("error"), booking_id)
                    )
                else:
                    await legacy.edit_event_message(
                        event,
                        "✅ Это занятие оплачено из вашего абонемента.\n"
                        f"Осталось занятий: {resolved.get('remaining_lessons', 0)}.",
                    )
                return
        return await original_qr(event)

    async def tutor_confirm(event):
        return await _vk_tutor_confirm(legacy, event)

    legacy._vk_render_payment_menu = render_payment
    legacy.qr = hardened_qr
    legacy.tutor_confirm_booking = tutor_confirm
    app.tutor_confirm_booking = tutor_confirm
    legacy._core_booking_flow_vk_hardened = True
