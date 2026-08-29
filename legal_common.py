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
        "hash": "acf00b0f464e4f364a1e46fd7bc487d7ab5fe5e98168137cb6a782b5881c51a9",
    },
    "student_offer": {
        "title": "Публичная оферта",
        "url": f"{DOC_BASE_URL}/02_student_offer.md",
        "hash": "b27ea2071205a7975a6ef444540928d73f9f90eb457b6aebe1c9b671ec4792fa",
    },
    "privacy_policy": {
        "title": "Политика обработки ПД",
        "url": f"{DOC_BASE_URL}/03_privacy_policy.md",
        "hash": "b66def1c2591503da7a35de89771132353b101d73ba6aecddcf15f48d1b42aeb",
    },
    "tutor_pd_consent": {
        "title": "Согласие репетитора на обработку ПД",
        "url": f"{DOC_BASE_URL}/04_tutor_pd_consent.md",
        "hash": "34b245e8cb48c9621d38fd9e2c1ba06683211177e64e00edefa1c4de216e09a5",
    },
    "tutor_distribution_consent": {
        "title": "Согласие на распространение ПД",
        "url": f"{DOC_BASE_URL}/05_tutor_distribution_consent.md",
        "hash": "23b497d7861d06dde6eae2956d82315f1bc304f2c4445b96b0b9ccb258b30cfa",
    },
    "user_consent": {
        "title": "Согласие пользователя",
        "url": f"{DOC_BASE_URL}/06_user_consent.md",
        "hash": "42ddd5ce0d6500822513bacca79dd1304c2e7b12d4f9528db8f3c2191856d296",
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
            CREATE UNIQUE INDEX IF NOT EXISTS uq_student_privacy_notice_once
                ON legal_acceptances(subject_id, subject_role, platform, doc_type, doc_version, action)
                WHERE booking_id IS NULL
                  AND subject_role='student'
                  AND doc_type='privacy_policy'
                  AND action IN ('presented_before_data_collection', 'continued_after_privacy_notice');
            """
        )

        # В старой схеме pending_subscriptions мог существовать без UNIQUE(payment_id),
        # хотя add_pending_subscription использует ON CONFLICT(payment_id).
        # Один T-Bank PaymentId может соответствовать только одному ожидающему абонементу,
        # поэтому при исторических дублях оставляем самую раннюю строку и добавляем
        # уникальный индекс, который также является arbiter для ON CONFLICT.
        await conn.execute(
            """
            DELETE FROM pending_subscriptions newer
            USING pending_subscriptions older
            WHERE newer.payment_id = older.payment_id
              AND newer.id > older.id;

            CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_subscriptions_payment_id
                ON pending_subscriptions(payment_id);
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


async def _booking_document_was_presented(user_id: int, platform: str, doc_type: str, booking_id: int) -> bool:
    """Не плодим одинаковые presented-события для одной записи и редакции документа."""
    await ensure_legal_schema()
    async with _db._legacy.pool.acquire() as conn:
        return bool(await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM legal_acceptances
                WHERE subject_id=$1
                  AND subject_role='student'
                  AND platform=$2
                  AND doc_type=$3
                  AND doc_version=$4
                  AND action='presented_before_payment'
                  AND booking_id=$5
            )
            """,
            int(user_id), platform, doc_type, DOC_VERSION, int(booking_id),
        ))


async def record_student_docs_presented(user_id: int, platform: str, booking_id: int | None = None) -> bool:
    """Фиксирует показ документов и возвращает True, если для этой точки появился новый показ."""
    newly_presented = False
    for doc_type in ("student_offer", "privacy_policy"):
        if booking_id is not None and await _booking_document_was_presented(
            user_id, platform, doc_type, booking_id
        ):
            continue
        await record_legal_event(
            user_id,
            "student",
            platform,
            doc_type,
            "presented_before_payment",
            "presented",
            booking_id=booking_id,
        )
        newly_presented = True
    return newly_presented


async def student_privacy_notice_completed(user_id: int, platform: str) -> bool:
    """Проверяет, продолжал ли пользователь после показа текущей Политики ПД."""
    await ensure_legal_schema()
    doc = DOCS["privacy_policy"]
    async with _db._legacy.pool.acquire() as conn:
        return bool(await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM legal_acceptances
                WHERE subject_id=$1
                  AND subject_role='student'
                  AND platform=$2
                  AND doc_type='privacy_policy'
                  AND doc_version=$3
                  AND doc_hash=$4
                  AND action='continued_after_privacy_notice'
                  AND result='continued'
            )
            """,
            int(user_id), platform, DOC_VERSION, doc["hash"],
        ))


async def _record_student_privacy_action(user_id: int, platform: str, action: str, result: str, context: str):
    await ensure_legal_schema()
    doc = DOCS["privacy_policy"]
    async with _db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO legal_acceptances
                (subject_id,subject_role,platform,doc_type,doc_version,doc_hash,
                 action,result,booking_id,metadata,accepted_at)
            VALUES($1,'student',$2,'privacy_policy',$3,$4,$5,$6,NULL,$7::jsonb,$8)
            ON CONFLICT DO NOTHING
            """,
            int(user_id),
            platform,
            DOC_VERSION,
            doc["hash"],
            action,
            result,
            json.dumps({"context": context}, ensure_ascii=False),
            datetime.now(MSK),
        )


async def record_student_privacy_presented(user_id: int, platform: str, context: str):
    await _record_student_privacy_action(
        user_id,
        platform,
        "presented_before_data_collection",
        "presented",
        context,
    )


async def record_student_privacy_continued(user_id: int, platform: str, context: str):
    await _record_student_privacy_action(
        user_id,
        platform,
        "continued_after_privacy_notice",
        "continued",
        context,
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
