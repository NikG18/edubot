"""Pure cross-platform identity rules for tutor student panels."""

from __future__ import annotations


def student_identity_key(booking: dict) -> tuple:
    """Use shared student_id when linked; otherwise keep platform ids separate."""
    student_id = booking.get("student_id")
    if student_id is not None:
        return ("student", int(student_id))
    platform = str(booking.get("user_platform") or "telegram").lower()
    return ("account", platform, int(booking.get("user_id") or 0))


def group_tutor_students(bookings: dict, tutor_id: int) -> list[dict]:
    """Group one tutor's bookings by real student identity.

    Active rows are kept individually so action callbacks remain booking-specific.
    Completed count is calculated across the same identity instead of raw messenger id.
    """
    tutor_id = int(tutor_id)
    groups: dict[tuple, dict] = {}
    for booking_id, booking in bookings.items():
        if int(booking.get("tutor_id") or 0) != tutor_id:
            continue
        key = student_identity_key(booking)
        group = groups.setdefault(
            key,
            {
                "key": key,
                "username": booking.get("username") or "Ученик",
                "platforms": set(),
                "active_bookings": [],
                "completed_lessons": 0,
            },
        )
        platform = str(booking.get("user_platform") or "telegram").lower()
        group["platforms"].add(platform)
        if booking.get("username"):
            group["username"] = booking["username"]
        if booking.get("status") == "completed":
            group["completed_lessons"] += 1
        if booking.get("status") in {"pending", "confirmed", "paid"}:
            group["active_bookings"].append((int(booking_id), booking))

    result = [group for group in groups.values() if group["active_bookings"]]
    for group in result:
        group["active_bookings"].sort(key=lambda item: item[0])
    result.sort(key=lambda group: (str(group["username"]).casefold(), repr(group["key"])))
    return result


def platform_label(platforms) -> str:
    normalized = {str(value).lower() for value in platforms}
    labels = []
    if "telegram" in normalized:
        labels.append("TG")
    if "vk" in normalized:
        labels.append("VK")
    return "/".join(labels) or "—"
