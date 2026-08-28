import inspect

import database as _db
import vk_bot_legacy as legacy
from vk_bot_legacy import *


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


# Старый VK-код остаётся неизменным, но все операции брони проходят через новый аудит.
legacy.update_booking = _contextual_update_booking
legacy.delete_booking = _contextual_delete_booking
legacy.mark_booking_paid_once = _db.mark_booking_paid_once
legacy.mark_booking_payment_failed = _db.mark_booking_payment_failed
legacy.reschedule_booking = _db.reschedule_booking
legacy.move_booking_in_place = _db.move_booking_in_place
legacy.cleanup_old_bookings = _db.cleanup_old_bookings
legacy.add_booking = _db.add_booking
legacy.get_booking = _db.get_booking
legacy.get_all_bookings = _db.get_all_bookings


async def main():
    return await legacy.main()


if __name__ == "__main__":
    legacy.logging.basicConfig(level=legacy.logging.INFO, stream=legacy.sys.stdout)
    legacy.asyncio.run(main())
