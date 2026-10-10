"""Timezone helpers for student-facing booking UI.

Tutor schedules and booking date/time remain canonical Moscow time (UTC+3).
Student profiles store a preferred fixed UTC offset in minutes. This keeps the
existing booking schema and collision logic backward-compatible.
"""
from datetime import datetime, timedelta

MSK_OFFSET_MINUTES = 180

SUPPORTED_TIMEZONES = [
    ("UTC", 0),
    ("UTC+1", 60),
    ("UTC+2", 120),
    ("МСК (UTC+3)", 180),
    ("UTC+4", 240),
    ("UTC+5", 300),
    ("UTC+6", 360),
    ("UTC+7", 420),
    ("UTC+8", 480),
    ("UTC+9", 540),
    ("UTC+10", 600),
    ("UTC+11", 660),
    ("UTC+12", 720),
]


def timezone_label(offset_minutes: int) -> str:
    offset_minutes = int(offset_minutes)
    if offset_minutes == 180:
        return "МСК (UTC+3)"
    sign = "+" if offset_minutes >= 0 else "-"
    total = abs(offset_minutes)
    hours, minutes = divmod(total, 60)
    suffix = f":{minutes:02d}" if minutes else ""
    return f"UTC{sign}{hours}{suffix}"


def _parse_slot(date_str: str, slot: str):
    start_raw, end_raw = slot.replace(".", ":").split("-", 1)
    start = datetime.strptime(f"{date_str} {start_raw}", "%d.%m.%Y %H:%M")
    end = datetime.strptime(f"{date_str} {end_raw}", "%d.%m.%Y %H:%M")
    if end <= start:
        end += timedelta(days=1)
    return start, end


def localize_slot(date_str: str, slot: str, offset_minutes: int):
    """Convert canonical Moscow booking date/slot into student's fixed UTC offset."""
    delta = timedelta(minutes=int(offset_minutes) - MSK_OFFSET_MINUTES)
    start, end = _parse_slot(date_str, slot)
    start += delta
    end += delta
    local_date = start.strftime("%d.%m.%Y")
    if start.date() == end.date():
        local_slot = f"{start:%H:%M}-{end:%H:%M}"
    else:
        local_slot = f"{start:%H:%M}-{end:%d.%m %H:%M}"
    return local_date, local_slot


def slot_button_label(date_str: str, slot: str, offset_minutes: int) -> str:
    local_date, local_slot = localize_slot(date_str, slot, offset_minutes)
    label = timezone_label(offset_minutes)
    if int(offset_minutes) == MSK_OFFSET_MINUTES:
        return f"{slot} · {label}"
    if local_date == date_str:
        return f"{local_slot} · {label}"
    return f"{local_date} {local_slot} · {label}"


def booking_local_text(date_str: str, slot: str, offset_minutes: int) -> str:
    local_date, local_slot = localize_slot(date_str, slot, offset_minutes)
    return f"{local_date} {local_slot} ({timezone_label(offset_minutes)})"
