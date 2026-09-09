from types import SimpleNamespace
import hashlib
import unittest

from student_acquisition import (
    source_from_payload,
    telegram_start_source,
    vk_start_source,
)


class AcquisitionParsingTests(unittest.TestCase):
    def test_telegram_deep_link_source(self):
        self.assertEqual(
            telegram_start_source("/start yandex_search"),
            ("yandex_search", "yandex_search"),
        )

    def test_telegram_bot_username_and_campaign_query(self):
        self.assertEqual(
            telegram_start_source(
                "/start@Ganzhaedubot utm_source=VK%20Ads&utm_campaign=september"
            ),
            (
                "vk-ads",
                "utm_source=VK Ads&utm_campaign=september",
            ),
        )

    def test_direct_start_is_explicit(self):
        self.assertEqual(telegram_start_source("/start"), ("direct", None))

    def test_vk_native_ref_is_preferred(self):
        message = SimpleNamespace(
            ref="yandex_maps", payload='{"source":"ignored"}', text="Начать"
        )
        self.assertEqual(vk_start_source(message), ("yandex_maps", "yandex_maps"))

    def test_vk_json_payload_is_supported(self):
        message = SimpleNamespace(
            payload='{"utm_source":"vk_target"}', text="Начать"
        )
        self.assertEqual(vk_start_source(message), ("vk_target", "vk_target"))

    def test_vk_raw_callback_shape_is_supported(self):
        message = SimpleNamespace(
            payload=None,
            text="Начать",
            raw={"object": {"message": {"ref": "partner_school"}}},
        )
        self.assertEqual(
            vk_start_source(message), ("partner_school", "partner_school")
        )

    def test_source_is_bounded_and_sanitized(self):
        source, raw = source_from_payload("  Яндекс / Реклама!  ")
        self.assertEqual(source, "яндекс-_-реклама")
        self.assertEqual(raw, "Яндекс / Реклама!")
        self.assertLessEqual(len(source), 64)


class AcquisitionDatabaseContractTests(unittest.TestCase):
    def test_schema_stores_first_and_last_touch(self):
        source = __import__("pathlib").Path(__file__).resolve().parents[1]
        database_source = (source / "database.py").read_text(encoding="utf-8")
        for column in (
            "acquisition_source",
            "acquisition_platform",
            "acquisition_payload",
            "acquired_at",
            "last_source",
            "last_source_platform",
            "last_source_payload",
            "last_seen_at",
            "source_visits",
        ):
            self.assertIn(column, database_source)
        self.assertIn("COALESCE(acquisition_source,$2)", database_source)
        self.assertIn("source_visits=source_visits+1", database_source)

    def test_account_link_merge_preserves_acquisition(self):
        source = __import__("pathlib").Path(__file__).resolve().parents[1]
        database_source = (source / "database.py").read_text(encoding="utf-8")
        self.assertIn("first_profile = min(", database_source)
        self.assertIn("last_profile = max(", database_source)
        self.assertIn('int(target_profile.get("source_visits") or 0)', database_source)

    def test_privacy_policy_discloses_acquisition_tracking_and_hash_matches(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        policy = (root / "legal" / "03_privacy_policy.md").read_bytes()
        policy_text = policy.decode("utf-8")
        common = (root / "legal_common.py").read_text(encoding="utf-8")
        self.assertIn("источник перехода/рекламная метка", policy_text)
        self.assertIn("анализ эффективности каналов привлечения", policy_text)
        self.assertIn(hashlib.sha256(policy).hexdigest(), common)


if __name__ == "__main__":
    unittest.main()
