import unittest
from pathlib import Path

from subject_booking_hardening import build_subject_catalog, catalog_item


class SubjectBookingCatalogTests(unittest.TestCase):
    def test_groups_tutors_by_subject_and_sorts_subjects(self):
        tutors = {
            "20": {
                "name": "Второй",
                "subjects": {"Физика": 2000, " химия ": 2500},
            },
            10: {
                "name": "Первый",
                "subjects": {"ХИМИЯ": 2300, "Математика": 1800},
            },
        }

        catalog = build_subject_catalog(tutors)

        self.assertEqual(
            [item["name"].casefold() for item in catalog],
            ["математика", "физика", "химия"],
        )
        chemistry = catalog[2]
        self.assertEqual(set(chemistry["tutors"]), {10, 20})
        self.assertEqual(chemistry["tutors"][10], "ХИМИЯ")
        self.assertEqual(chemistry["tutors"][20], " химия ")

    def test_ignores_empty_subjects_and_invalid_tutor_ids(self):
        catalog = build_subject_catalog({
            "bad-id": {"subjects": {"Химия": 1}},
            1: {"subjects": {"   ": 1}},
            2: {"subjects": {}},
        })
        self.assertEqual(catalog, [])

    def test_catalog_item_rejects_stale_or_invalid_indexes(self):
        catalog = [{"name": "Химия", "tutors": {1: "Химия"}}]
        self.assertEqual(catalog_item(catalog, "0"), (0, catalog[0]))
        self.assertIsNone(catalog_item(catalog, "-1"))
        self.assertIsNone(catalog_item(catalog, "1"))
        self.assertIsNone(catalog_item(catalog, "not-a-number"))

    def test_legal_entrypoints_start_with_tutors(self):
        root = Path(__file__).resolve().parents[1]
        telegram = (root / "legal_telegram.py").read_text(encoding="utf-8")
        vk = (root / "legal_vk.py").read_text(encoding="utf-8")
        for source in (telegram, vk):
            self.assertNotIn('getattr(legacy, "_subject_booking_start_', source)
            self.assertIn('make_tutors_keyboard("tutor_booking", back_callback="back_to_menu")', source)

    def test_vk_router_contains_every_subject_booking_route(self):
        source = (
            Path(__file__).resolve().parents[1] / "archive" / "vk_bot_legacy.py"
        ).read_text(encoding="utf-8")
        for route in ("booksub_", "booktutor_", "book_back_subjects", "book_back_tutors"):
            with self.subTest(route=route):
                self.assertIn(route, source)


if __name__ == "__main__":
    unittest.main()
