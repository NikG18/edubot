import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Optional

import database as db

DOC_VERSION = "2026-08-29"
DOC_DIR = Path(os.environ.get("LEGAL_DOCS_DIR", Path(__file__).with_name("legal_docs")))
PUBLIC_BASE = os.environ.get(
    "LEGAL_PUBLIC_BASE_URL",
    "https://raw.githubusercontent.com/NikG18/edubot/main/legal_docs",
).rstrip("/")
TUTOR_YANDEX_FORM_URL = os.environ.get("TUTOR_YANDEX_FORM_URL", "").strip()

DOCS = {
    "legal_map": ("00_legal_map.docx", "Юридическая карта и чек-лист"),
    "tutor_agreement": ("01_tutor_agreement.docx", "Агентский договор с репетитором"),
    "student_offer": ("02_student_offer.docx", "Публичная оферта для ученика"),
    "privacy_policy": ("03_privacy_policy.docx", "Политика обработки персональных данных"),
    "tutor_pd_consent": ("04_tutor_pd_consent.docx", "Согласие репетитора на обработку ПД"),
    "tutor_distribution_consent": ("05_tutor_distribution_consent.docx", "Согласие репетитора на распространение ПД"),
    "student_optional_consent": ("06_student_optional_consent.docx", "Согласие пользователя на дополнительные ПД/рекламу"),
    "internal_pd_policy": ("07_internal_pd_policy.docx", "Внутреннее положение по ПД"),
    "incident_policy": ("08_incident_policy.docx", "Регламент инцидентов и уничтожения ПД"),
    "claims_policy": ("09_claims_policy.docx", "Регламент претензий и возвратов"),
    "legal_tech_spec": ("10_legal_tech_spec.docx", "Актуальное юридическое ТЗ"),
}

STUDENT_DOCS = ("student_offer", "privacy_policy", "student_optional_consent", "claims_policy")
TUTOR_DOCS = ("tutor_agreement", "privacy_policy", "tutor_pd_consent", "tutor_distribution_consent", "claims_policy")
ADMIN_DOCS = tuple(DOCS)
PAYMENT_DOCS = ("student_offer", "privacy_policy")


def doc_path(doc_type: str) -> Path:
    return DOC_DIR / DOCS[doc_type][0]


def doc_url(doc_type: str) -> str:
    return f"{PUBLIC_BASE}/{DOCS[doc_type][0]}"


def doc_hash(doc_type: str) -> str:
    path = doc_path(doc_type)
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def ensure_schema():
    await db._ensure_pool()
    async with db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS legal_acceptances (
                id BIGSERIAL PRIMARY KEY,
                subject_id BIGINT NOT NULL,
                subject_role TEXT NOT NULL,
                platform TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                doc_version TEXT NOT NULL,
                doc_hash TEXT,
                action TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT 'accepted',
                booking_id INTEGER,
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


async def log_legal_event(
    subject_id: int,
    subject_role: str,
    platform: str,
    doc_type: str,
    action: str,
    *,
    result: str = "accepted",
    booking_id: Optional[int] = None,
    external_ref: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    await ensure_schema()
    async with db._legacy.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO legal_acceptances
              (subject_id,subject_role,platform,doc_type,doc_version,doc_hash,
               action,result,booking_id,external_ref,metadata)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
            """,
            int(subject_id), subject_role, platform, doc_type, DOC_VERSION,
            doc_hash(doc_type), action, result, booking_id, external_ref,
            json.dumps(metadata or {}, ensure_ascii=False),
        )


async def already_logged(subject_id: int, doc_type: str, action: str, booking_id: Optional[int] = None) -> bool:
    await ensure_schema()
    async with db._legacy.pool.acquire() as conn:
        return bool(await conn.fetchval(
            """
            SELECT EXISTS(
              SELECT 1 FROM legal_acceptances
              WHERE subject_id=$1 AND doc_type=$2 AND doc_version=$3 AND action=$4
                AND ($5::INTEGER IS NULL OR booking_id=$5)
            )
            """,
            int(subject_id), doc_type, DOC_VERSION, action, booking_id,
        ))


async def record_tutor_yandex_acceptance(
    tutor_id: int,
    external_ref: str,
    *,
    agreement: bool,
    pd_consent: bool,
    distribution_consent: bool,
    distribution_fields: Optional[Iterable[str]] = None,
):
    """Регистрирует результат Яндекс Формы после импорта/вебхука."""
    mapping = [
        ("tutor_agreement", agreement, {}),
        ("tutor_pd_consent", pd_consent, {}),
        ("tutor_distribution_consent", distribution_consent,
         {"allowed_fields": list(distribution_fields or [])}),
    ]
    for doc_type, accepted, metadata in mapping:
        await log_legal_event(
            tutor_id, "tutor", "yandex_form", doc_type, "accepted",
            result="accepted" if accepted else "declined",
            external_ref=external_ref,
            metadata=metadata,
        )


async def mark_document_presented(subject_id: int, platform: str, doc_type: str, booking_id: Optional[int] = None):
    if not await already_logged(subject_id, doc_type, "presented", booking_id):
        await log_legal_event(subject_id, "student", platform, doc_type, "presented", booking_id=booking_id)
