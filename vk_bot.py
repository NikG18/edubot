import inspect
from datetime import timedelta

import database as _db
import vk_bot_legacy as legacy
from vk_bot_legacy import *

TRIAL_PREFIX = "Пробное: "


def _caller_context():
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back and frame.f_back.f_back else None
        if not caller:
            return "", None
        actor_id = None
        event = caller.f_locals.get("event")
        if event is not None:
            actor_id = getattr(event, "user_id", None)
        message = caller.f_locals.get("message")
        if actor_id is None and message is not None:
            actor_id = getattr(message, "from_id", None)
        return caller.f_code.co_name, actor_id
    finally:
        del frame


def _stack_has_caller(name: str) -> bool:
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame else None
        for _ in range(8):
            if current is None:
                break
            if current.f_code.co_name == name:
                return True
            current = current.f_back
        return False
    finally:
        del frame


def _is_trial(booking) -> bool:
    return bool(booking and str(booking.get("subject") or "").startswith(TRIAL_PREFIX))


async def _contextual_update_booking(booking_id, **kwargs):
    caller, actor_id = _caller_context()
    status = kwargs.get("status")
    if status == "cancelled":
        if caller == "cancel_student_booking":
            kwargs.setdefault("_actor_type", "student")
            kwargs.setdefault("_actor_id", actor_id)
            kwargs.setdefault("_reason", "Отменено учеником")
        elif caller == "tutor_cancel_booking":
            kwargs.setdefault("_actor_type", "tutor")
            kwargs.setdefault("_actor_id", actor_id)
            kwargs.setdefault("_reason", "Отменено преподавателем")
    elif status == "confirmed":
        kwargs.setdefault("_actor_type", "tutor")
        kwargs.setdefault("_actor_id", actor_id)
        kwargs.setdefault("_event_type", "confirmed")
    return await _db.update_booking(booking_id, **kwargs)


async def _contextual_delete_booking(booking_id: int):
    _caller, actor_id = _caller_context()
    changed, booking = await _db.change_booking_status(
        booking_id,
        "cancelled",
        event_type="rejected",
        actor_type="tutor",
        actor_id=actor_id,
        reason="Заявка отклонена преподавателем",
        expected_statuses={"pending"},
    )
    return booking if changed else booking


# ---------------------------------------------------------------------------
# VK compatibility fixes
# ---------------------------------------------------------------------------
_original_open_link = legacy.OpenLink


def _compat_open_link(label, link, *args, **kwargs):
    return _original_open_link(link, label, *args, **kwargs)


legacy.OpenLink = _compat_open_link

# VK inline keyboard допускает максимум 10 кнопок. Старые экраны дат
# добавляют ещё кнопку навигации, поэтому показываем максимум 9 дат.
_original_get_available_dates = legacy.get_available_dates


async def _vk_safe_available_dates(tutor_id: int, days_ahead=30):
    dates = await _original_get_available_dates(tutor_id, days_ahead=days_ahead)
    return dates[:9]


legacy.get_available_dates = _vk_safe_available_dates


# ---------------------------------------------------------------------------
# Бесплатные пробные занятия
# ---------------------------------------------------------------------------
_original_add_booking = _db.add_booking


async def _contextual_add_booking(tutor_id, user_id, username, subject, date, time_slot,
                                  channel_msg_id=None, user_platform="telegram"):
    if _stack_has_caller("confirm_trial_booking") and not str(subject).startswith(TRIAL_PREFIX):
        subject = f"{TRIAL_PREFIX}{subject}"
    return await _original_add_booking(
        tutor_id, user_id, username, subject, date, time_slot,
        channel_msg_id=channel_msg_id, user_platform=user_platform,
    )


_original_tutor_confirm_booking = legacy.tutor_confirm_booking


async def _trial_aware_tutor_confirm_booking(event):
    try:
        booking_id = int(event.payload["cmd"].rsplit("_", 1)[1])
    except (KeyError, ValueError, TypeError):
        return await _original_tutor_confirm_booking(event)

    booking = await _db.get_booking(booking_id)
    if not _is_trial(booking):
        return await _original_tutor_confirm_booking(event)

    tutor_id = await legacy.get_tutor_by_vk_id(event.user_id)
    if not booking or tutor_id != booking.get("tutor_id"):
        await legacy.answer_event(event, "Доступ запрещён.", snackbar=True)
        return
    if booking.get("status") != "pending":
        await legacy.edit_event_message(event, "Заявка уже обработана.")
        return

    await _db.update_booking(
        booking_id,
        status="confirmed",
        amount=0,
        commission_percent=0,
        _actor_type="tutor",
        _actor_id=event.user_id,
        _event_type="confirmed",
    )
    clean_subject = str(booking["subject"])[len(TRIAL_PREFIX):]
    await legacy.send_to_user(
        booking["user_id"],
        booking.get("user_platform", "vk"),
        (
            "✅ Бесплатное пробное занятие подтверждено!\n"
            f"📚 {clean_subject}\n"
            f"📅 {booking['date']} 🕒 {booking['time_slot']}\n\n"
            "Оплата и email не требуются. Пробное занятие можно отменить или перенести без ограничения 24 часа."
        ),
    )
    await legacy.edit_event_message(event, "✅ Бесплатное пробное занятие подтверждено. Оплата не требуется.")


legacy.tutor_confirm_booking = _trial_aware_tutor_confirm_booking

# В старом VK UI правило 24 часов вычисляется через now_msk_naive().
# Для пробной записи сдвигаем только эту проверку, если в текущем handler
# уже есть конкретная trial booking. Обычные платные занятия не затрагиваются.
_original_now_msk_naive = legacy.now_msk_naive


def _trial_aware_now_msk_naive():
    now = _original_now_msk_naive()
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame else None
        for _ in range(8):
            if current is None:
                break
            for key in ("booking", "b"):
                value = current.f_locals.get(key)
                if isinstance(value, dict) and _is_trial(value):
                    return now - timedelta(days=2)
            current = current.f_back
    finally:
        del frame
    return now


legacy.now_msk_naive = _trial_aware_now_msk_naive


# Старый VK-код остаётся неизменным, но все операции брони проходят через новый аудит.
legacy.update_booking = _contextual_update_booking
legacy.delete_booking = _contextual_delete_booking
legacy.mark_booking_paid_once = _db.mark_booking_paid_once
legacy.mark_booking_payment_failed = _db.mark_booking_payment_failed
legacy.reschedule_booking = _db.reschedule_booking
legacy.move_booking_in_place = _db.move_booking_in_place
legacy.cleanup_old_bookings = _db.cleanup_old_bookings
legacy.add_booking = _contextual_add_booking
legacy.get_booking = _db.get_booking
legacy.get_all_bookings = _db.get_all_bookings


async def main():
    return await legacy.main()


if __name__ == "__main__":
    legacy.logging.basicConfig(level=legacy.logging.INFO, stream=legacy.sys.stdout)
    legacy.asyncio.run(main())
