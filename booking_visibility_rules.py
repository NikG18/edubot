"""Pure booking visibility rules shared by Telegram/VK/admin UI."""

from __future__ import annotations


REGULAR_STATUSES = {"pending", "confirmed", "paid", "completed", "cancelled"}


def is_trial_booking(booking: dict | None) -> bool:
    if not booking:
        return False
    if str(booking.get("booking_type") or "").lower() == "trial":
        return True
    return str(booking.get("subject") or "").startswith("Пробное: ")


def can_offer_separate_payment(booking: dict | None) -> bool:
    """Only confirmed regular lessons may be offered for an individual charge."""
    return bool(
        booking
        and booking.get("status") == "confirmed"
        and not is_trial_booking(booking)
    )


def payment_delivery_note(booking: dict | None) -> str:
    """A payment substate never replaces confirmed or makes a lesson paid."""
    if not booking or booking.get("status") != "confirmed" or is_trial_booking(booking):
        return ""
    return {
        "awaiting_email": "📧 Ожидаем e-mail ученика; платёж ещё не отправлен",
        "not_sent": "💳 Ссылка на оплату ещё не отправлена",
        "sent": "💳 Ссылка на оплату отправлена",
    }.get(booking.get("payment_delivery_state"), "")


def admin_booking_matches(booking: dict | None, section: str) -> bool:
    """Keep trials out of regular status buckets and expose one dedicated section."""
    if not booking:
        return False
    section = str(section or "").lower()
    if section == "trials":
        return is_trial_booking(booking)
    if section not in REGULAR_STATUSES:
        return False
    return not is_trial_booking(booking) and booking.get("status") == section
