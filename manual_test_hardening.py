"""Fixes for manually reproduced navigation and records-channel defects.

This module is intentionally installed late, after the core booking and tutor-panel
hardening layers, so its identity-preserving handler patch is the final Telegram
"back to my records" behavior.
"""

from __future__ import annotations

import booking_records as records
import subscription_hardening as subs
from records_channel_hardening import install_records_channel_hardening


class _TutorCallbackProxy:
    def __init__(self, call, data: str):
        self._call = call
        self.data = data
        self.from_user = call.from_user
        self.message = call.message

    def __getattr__(self, name):
        return getattr(self._call, name)


async def _tg_role_aware_back_to_records(call, state):
    await safe_answer(call)
    await state.clear()
    tutor_id = await get_tutor_by_telegram_id(call.from_user.id)
    if tutor_id is not None:
        proxy = _manual_tutor_callback_proxy(call, f"tutor_students_{tutor_id}")
        await show_students(proxy, None)
        return
    proxy = _core_callback_message_proxy(call.message, call.from_user)
    await _tg_render_records(proxy)


_original_records_renderer = None


async def _record_renderer_with_subscription_type(booking_id: int):
    text = await _manual_original_records_renderer(int(booking_id))
    if not text:
        return text
    try:
        usage = await subs.get_booking_usage(int(booking_id))
    except Exception:
        return text
    if usage:
        text = text.replace("🏷 Тип: Обычное", "🏷 Тип: Абонемент", 1)
        text = text.replace("· Обычное\n", "· Абонемент\n", 1)
    return text


def install_telegram_manual_test_hardening(app) -> None:
    legacy = app.legacy
    if getattr(legacy, "_manual_test_tg_hardened", False):
        return

    # Central booking_records owns the one-card-per-booking records channel.
    # Disable only the old Telegram handlers' direct send/delete operations;
    # booking_records.RECORDS_CHANNEL_ID is a separate module-level value.
    legacy.RECORDS_CHANNEL_ID = None

    legacy._manual_tutor_callback_proxy = _TutorCallbackProxy
    if legacy.back_to_my_records.__code__.co_freevars or _tg_role_aware_back_to_records.__code__.co_freevars:
        raise RuntimeError("Role-aware Telegram records navigation cannot use closures")
    legacy.back_to_my_records.__code__ = _tg_role_aware_back_to_records.__code__
    legacy._manual_test_tg_hardened = True


def install_records_runtime_hardening(app) -> None:
    global _original_records_renderer
    install_records_channel_hardening()
    if getattr(records, "_manual_records_runtime_hardened", False):
        return
    _original_records_renderer = records.render_booking_record
    # Function executes in this module, so keep its dependency explicit rather
    # than using __code__ replacement.
    globals()["_manual_original_records_renderer"] = _original_records_renderer
    records.render_booking_record = _record_renderer_with_subscription_type
    records._manual_records_runtime_hardened = True
