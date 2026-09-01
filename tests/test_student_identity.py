import unittest
from datetime import datetime

from student_identity import (
    generate_link_code,
    hash_link_code,
    is_late_trial_cancellation,
    normalize_email,
    normalize_link_code,
)


class LinkCodeTests(unittest.TestCase):
    def test_generated_code_is_valid_and_copy_friendly(self):
        code = generate_link_code()
        self.assertEqual(len(code), 8)
        self.assertEqual(normalize_link_code(code), code)
        self.assertNotIn("0", code)
        self.assertNotIn("O", code)

    def test_normalize_accepts_spaces_and_hyphens(self):
        self.assertEqual(normalize_link_code("ab2d-3ef4"), "AB2D3EF4")

    def test_hash_is_stable_after_normalization(self):
        self.assertEqual(hash_link_code("AB2D3EF4"), hash_link_code("ab2d-3ef4"))

    def test_invalid_code_is_rejected(self):
        self.assertIsNone(normalize_link_code("ABC"))
        self.assertIsNone(normalize_link_code("OOOOOOOO"))


class TrialCancellationTests(unittest.TestCase):
    def test_more_than_24_hours_does_not_consume_trial(self):
        now = datetime(2026, 8, 30, 12, 0)
        self.assertFalse(is_late_trial_cancellation("31.08.2026", "12:01-13:01", now))

    def test_exactly_24_hours_consumes_trial(self):
        now = datetime(2026, 8, 30, 12, 0)
        self.assertTrue(is_late_trial_cancellation("31.08.2026", "12:00-13:00", now))

    def test_less_than_24_hours_consumes_trial(self):
        now = datetime(2026, 8, 30, 12, 0)
        self.assertTrue(is_late_trial_cancellation("31.08.2026", "11:59-12:59", now))


class EmailNormalizationTests(unittest.TestCase):
    def test_email_is_trimmed_and_casefolded(self):
        self.assertEqual(normalize_email("  Student@Example.COM "), "student@example.com")


if __name__ == "__main__":
    unittest.main()
