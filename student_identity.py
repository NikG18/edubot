"""Pure helpers for cross-platform student identities and trial bookings."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta


LINK_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
LINK_CODE_LENGTH = 8
LINK_CODE_TTL = timedelta(minutes=10)
_LINK_CODE_RE = re.compile(r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}$")


def normalize_email(value: str) -> str:
    """Нормализует e-mail для внутреннего сопоставления без изменения адреса."""
    return (value or "").strip().casefold()


def generate_link_code() -> str:
    """Return a copy-friendly, high-entropy, one-time account link code."""
    return "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))


def normalize_link_code(value: str) -> str | None:
    code = re.sub(r"[\s-]+", "", (value or "").upper())
    return code if _LINK_CODE_RE.fullmatch(code) else None


def hash_link_code(code: str) -> str:
    normalized = normalize_link_code(code)
    if not normalized:
        raise ValueError("invalid account link code")
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def booking_start(date: str, time_slot: str) -> datetime:
    start = time_slot.split("-", 1)[0].replace(".", ":")
    return datetime.strptime(f"{date} {start}", "%d.%m.%Y %H:%M")


def is_late_trial_cancellation(
    date: str,
    time_slot: str,
    now: datetime,
    *,
    notice: timedelta = timedelta(hours=24),
) -> bool:
    """A student cancellation at or inside the 24-hour boundary consumes a trial."""
    return booking_start(date, time_slot) - now <= notice
