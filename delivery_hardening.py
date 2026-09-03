"""Make UI delivery acknowledgements reflect the actual bridge result.

The shared messaging API intentionally returns bool instead of raising.  Several
legacy handlers already have try/except blocks but ignored that bool and therefore
reported a green success message after Telegram/VK delivery failed.  We only
promote False to an exception for those known UI handlers; other background sends
keep their existing best-effort semantics.
"""

from __future__ import annotations

import inspect


_CHECKED_USER_CALLERS = {
    "send_reply_to_student",
    "tutor_send_message_to_student",
    "support_send_reply",
}


def _caller_name() -> str:
    frame = inspect.currentframe()
    try:
        current = frame.f_back.f_back if frame and frame.f_back else None
        for _ in range(5):
            if current is None:
                break
            name = current.f_code.co_name
            if name not in {"checked_send_to_user", "_caller_name"}:
                return name
            current = current.f_back
        return ""
    finally:
        del frame


def install_delivery_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_delivery_status_hardening_installed", False):
        return

    original_send_to_user = legacy.send_to_user

    async def checked_send_to_user(*args, **kwargs):
        result = await original_send_to_user(*args, **kwargs)
        if not result and _caller_name() in _CHECKED_USER_CALLERS:
            raise RuntimeError("message bridge reported delivery failure")
        return result

    legacy.send_to_user = checked_send_to_user
    # Wrapper modules often resolve this alias dynamically as well.
    app.send_to_user = checked_send_to_user
    legacy._delivery_status_hardening_installed = True
