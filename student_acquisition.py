"""Parse safe acquisition tags from Telegram and VK entry points."""

from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import parse_qs, unquote_plus


DIRECT_SOURCE = "direct"
_SOURCE_KEYS = ("utm_source", "source", "ref", "start")
_START_RE = re.compile(r"^(?:/start(?:@\w+)?|start|начать)(?:\s+(.+))?$", re.IGNORECASE)


def _as_mapping(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    value = value.strip()
    if not value.startswith("{"):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def source_from_payload(payload: str | None) -> tuple[str, str | None]:
    """Return a normalized source and the bounded original campaign payload."""
    raw = unquote_plus(str(payload or "").strip())[:256] or None
    if raw is None:
        return DIRECT_SOURCE, None

    candidate = raw
    parsed = parse_qs(raw.lstrip("?"), keep_blank_values=False)
    for key in _SOURCE_KEYS:
        values = parsed.get(key)
        if values and values[0].strip():
            candidate = values[0]
            break

    candidate = unicodedata.normalize("NFKC", candidate).strip().casefold()
    candidate = re.sub(r"\s+", "-", candidate)
    candidate = re.sub(r"[^0-9a-zа-яё._:-]+", "_", candidate)
    candidate = candidate.strip("_.:-")[:64]
    return candidate or DIRECT_SOURCE, raw


def telegram_start_source(text: str | None) -> tuple[str, str | None]:
    match = _START_RE.match(str(text or "").strip())
    return source_from_payload(match.group(1) if match else None)


def vk_start_source(message) -> tuple[str, str | None]:
    """Extract VK's native ``ref`` or a compatible payload/text marker."""
    for name in ("ref", "ref_source", "source"):
        value = getattr(message, name, None)
        if value:
            return source_from_payload(value)

    payload = _as_mapping(getattr(message, "payload", None))
    for key in _SOURCE_KEYS:
        if payload.get(key):
            return source_from_payload(payload[key])

    raw = getattr(message, "raw", None)
    if isinstance(raw, dict):
        candidates = (
            raw,
            raw.get("object") or {},
            (raw.get("object") or {}).get("message") or {},
        )
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("ref", "ref_source", "source"):
                if candidate.get(key):
                    return source_from_payload(candidate[key])

    match = _START_RE.match(str(getattr(message, "text", "") or "").strip())
    return source_from_payload(match.group(1) if match else None)

