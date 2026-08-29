import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import database as _db

DOC_VERSION = "2026-08-29"
DOC_BASE_URL = os.environ.get(
    "LEGAL_DOCS_BASE_URL",
    "https://github.com/NikG18/edubot/blob/main/legal",
).rstrip("/")

DOCS = {
    "agent_agreement": {
        "title": "Агентский договор",
        "url": f"{DOC_BASE_URL}/01_agent_agreement.md",
        "hash": "1ec844080835d91e903e3d00c8f0da8d5355b8d6a6c1136bbb7ee5fc7506cabc",
    },
    "student_offer": {
        "title": "Публичная оферта",
        "url": f"{DOC_BASE_URL}/02_student_offer.md",
        "hash": "ff769c75c6bbd669b71aa38462e5c18268b17d97585857eb24a04b405c6e76b3",
    },
    "privacy_policy": {
        "title": "Политика обработки ПД",
        "url": f"{DOC_BASE_URL}/03_privacy_policy.md",
        "hash": "e7e8b77b4a50abf598a6ec1beb4c7e2f9f354b8617382167236d53ee6d383e18",
    },
    "tutor_pd_consent": {
        "title": "Согласие репетитора на обработку ПД",
        "url": f"{DOC_BASE_URL}/04_tutor_pd_consent.md",
        "hash": "23d35fc33a1d757ffd930cd3b03420b3f4a188c949360c78ba95cd26c4d0d7d2",
    },
    "tutor_distribution_consent": {
        "title": "Согласие на распространение ПД",
        "url": f"{DOC_BASE_URL}/05_tutor_distribution_consent.md",
        "hash": "28919bd788dec4529d53c9e13eaee0361c3e17b2133b132a16a98f79e9eedeaa",
    },
    "user_consent": {
        "title": "Согласие пользователя",
        "url": f"{DOC_BASE_URL}/06_user_consent.md",
        "hash": "d7a3b58bc98a6e262ffc0fa0746b6026f5ed33ad85295a7e070e5e506a22c684",
    },
}

STUDENT_DOC_TYPES = ("student_offer", "privacy_policy", "user_consent")
TUTOR_DOC_TYPES = (
    "agent_agreement",
    "privacy_policy",
    "tutor_pd_consent",
    "tutor_distribution_consent",
)
MSK = ZoneInfo("Europe/Moscow")


async def ensure_legal_schema():
    await _db._ensure_pool()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS legal_acceptances (
                id BIGSERIAL PRIMARY KEY,
                subject_id BIGINT NOT NULL,
                subject_role TEXT NOT NULL,
                platform TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                doc_version TEXT NOT NULL,
                doc_hash TEXT NOT NULL,
                action TEXT NOT NULL,
                result TEXT NOT NULL,
                booking_id INTEGER NULL REFERENCES bookings(id) ON DELETE SET NULL,
                external_ref TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_legal_acceptances_subject
                ON legal_acceptances(subject_role, subject_id, accepted_at DESC);
            CREATE INDEX IF NOT EXISTS idx_legal_acceptances_booking
                ON legal_acceptances(booking_id, accepted_at DESC)
                WHERE booking_id IS NOT NULL;
            """
        )


async def record_legal_event(
    subject_id: int,
    subject_role: str,
    platform: str,
    doc_type: str,
    action: str,
    result: str,
    *,
    booking_id: int | None = None,
    external_ref: str | None = None,
    metadata: dict | None = None,
):
    doc = DOCS[doc_type]
    await ensure_legal_schema()
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO legal_acceptances
                (subject_id,subject_role,platform,doc_type,doc_version,doc_hash,
                 action,result,booking_id,external_ref,metadata,accepted_at)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12)
            """,
            int(subject_id),
            subject_role,
            platform,
            doc_type,
            DOC_VERSION,
            doc["hash"],
            action,
            result,
            booking_id,
            external_ref,
            json.dumps(metadata or {}, ensure_ascii=False),
            datetime.now(MSK),
        )


async def record_student_docs_presented(user_id: int, platform: str, booking_id: int | None = None):
    for doc_type in ("student_offer", "privacy_policy"):
        await record_legal_event(
            user_id,
            "student",
            platform,
            doc_type,
            "presented_before_payment",
            "presented",
            booking_id=booking_id,
        )


async def record_tutor_yandex_acceptance(
    tutor_id: int,
    doc_type: str,
    *,
    result: str = "accepted",
    external_ref: str,
    metadata: dict | None = None,
):
    if doc_type not in {"agent_agreement", "tutor_pd_consent", "tutor_distribution_consent"}:
        raise ValueError("Unsupported tutor legal document")
    await record_legal_event(
        tutor_id,
        "tutor",
        "yandex_form",
        doc_type,
        "yandex_form_response",
        result,
        external_ref=external_ref,
        metadata=metadata,
    )


def install_payment_acceptance_hook():
    if getattr(_db, "_legal_paid_hook_installed", False):
        return _db.mark_booking_paid_once

    original = _db.mark_booking_paid_once

    async def _mark_booking_paid_with_legal(booking_id: int):
        changed, booking = await original(booking_id)
        if changed and booking:
            try:
                await record_legal_event(
                    booking["user_id"],
                    "student",
                    booking.get("user_platform", "telegram"),
                    "student_offer",
                    "payment_success",
                    "accepted",
                    booking_id=booking_id,
                    metadata={"payment_id": booking.get("tinkoff_payment_id")},
                )
            except Exception:
                import logging
                logging.exception("Не удалось зафиксировать акцепт оферты booking=%s", booking_id)
        return changed, booking

    _db.mark_booking_paid_once = _mark_booking_paid_with_legal
    _db._legal_paid_hook_installed = True
    return _mark_booking_paid_with_legal


def install_subscription_acceptance_hook():
    if getattr(_db, "_legal_subscription_hook_installed", False):
        return _db.activate_subscription

    original = _db.activate_subscription

    async def _activate_subscription_with_legal(payment_id: str):
        pending = await _db.get_pending_subscription_by_payment_id(payment_id)
        activated = await original(payment_id)
        if activated and pending:
            try:
                await record_legal_event(
                    pending["user_id"],
                    "student",
                    pending.get("user_platform", "telegram"),
                    "student_offer",
                    "subscription_payment_success",
                    "accepted",
                    metadata={
                        "payment_id": payment_id,
                        "tutor_id": pending.get("tutor_id"),
                        "subject": pending.get("subject"),
                        "total_lessons": pending.get("total_lessons"),
                    },
                )
            except Exception:
                import logging
                logging.exception("Не удалось зафиксировать акцепт оферты по абонементу payment=%s", payment_id)
        return activated

    _db.activate_subscription = _activate_subscription_with_legal
    _db._legal_subscription_hook_installed = True
    try:
        import webhook_server
        webhook_server.activate_subscription = _activate_subscription_with_legal
    except Exception:
        pass
    return _activate_subscription_with_legal
