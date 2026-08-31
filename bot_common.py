import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

MSK = ZoneInfo("Europe/Moscow")
EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.I)


def now_msk() -> datetime:
    return datetime.now(MSK)


def now_msk_naive() -> datetime:
    return now_msk().replace(tzinfo=None)


def parse_booking_time(booking: dict, end: bool = False) -> datetime:
    parts = booking["time_slot"].split("-")
    time_part = parts[-1 if end else 0].replace(".", ":")
    return datetime.strptime(f"{booking['date']} {time_part}", "%d.%m.%Y %H:%M")


def booking_is_actionable(booking: dict, hours: int = 24) -> bool:
    try:
        return parse_booking_time(booking) - now_msk_naive() > timedelta(hours=hours)
    except (ValueError, KeyError, AttributeError):
        return False


def valid_email(value: str) -> bool:
    value = (value or "").strip()
    return bool(value and len(value) <= 254 and EMAIL_RE.fullmatch(value))


async def actor_is_booking_owner(booking: Optional[dict], actor_id: int,
                                 platform: str = "telegram") -> bool:
    if not booking:
        return False
    from database import account_owns_booking
    return await account_owns_booking(platform, actor_id, booking)


async def actor_is_tutor_for_booking(booking: Optional[dict], actor_platform_id: int, platform: str) -> bool:
    if not booking:
        return False
    if platform == "telegram":
        from database import get_tutor_by_telegram_id
        tid = await get_tutor_by_telegram_id(actor_platform_id)
    elif platform == "vk":
        from database import get_tutor_by_vk_id
        tid = await get_tutor_by_vk_id(actor_platform_id)
    else:
        return False
    return tid == booking.get("tutor_id")


async def process_booking_payment_status(booking_id: int, status: str):
    """Единая идемпотентная обработка статуса оплаты. Возвращает (changed, booking).

    Если T-Bank сообщает об успешной оплате уже после отмены занятия, занятие не
    восстанавливается. Вместо этого платёж фиксируется в истории, а бронь помечается
    как требующая возврата.
    """
    from database import (
        add_booking_event,
        get_booking,
        mark_booking_paid_once,
        mark_booking_payment_failed,
        update_booking,
    )

    if status in ("CONFIRMED", "AUTHORIZED"):
        booking = await get_booking(booking_id)
        if not booking:
            return False, None

        if booking.get("status") == "cancelled":
            if (booking.get("refund_status") or "none") != "required":
                await add_booking_event(
                    booking_id,
                    "late_payment_after_cancel",
                    old_status="cancelled",
                    new_status="cancelled",
                    actor_type="payment",
                    details={
                        "payment_id": booking.get("tinkoff_payment_id"),
                        "amount": booking.get("amount"),
                    },
                )
                booking = await update_booking(
                    booking_id,
                    refund_status="required",
                    refund_updated_at=now_msk(),
                )
            # False намеренно: вызывающий код не должен отправлять сообщение
            # «занятие подтверждено» для уже отменённого занятия.
            return False, booking

        return await mark_booking_paid_once(booking_id)

    if status in ("REJECTED", "CANCELED"):
        return await mark_booking_payment_failed(booking_id)

    return False, None
