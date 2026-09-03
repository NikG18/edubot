import unittest

from callback_hardening import _subject_by_index
from input_hardening import SUBJECT_NAME_MAX_BYTES


class CallbackHardeningTests(unittest.TestCase):
    def test_subject_resolution_uses_current_order(self):
        tutor = {"subjects": {"Химия": 2000, "Очень длинное название предмета " * 5: 2500}}
        self.assertEqual(_subject_by_index(tutor, 0), "Химия")
        self.assertEqual(_subject_by_index(tutor, 1), "Очень длинное название предмета " * 5)

    def test_invalid_index_is_rejected(self):
        tutor = {"subjects": {"Химия": 2000}}
        self.assertIsNone(_subject_by_index(tutor, -1))
        self.assertIsNone(_subject_by_index(tutor, 1))
        self.assertIsNone(_subject_by_index(None, 0))

    def test_all_new_callback_shapes_stay_under_telegram_limit(self):
        tutor_id = 2_147_483_647
        index = 999_999
        payloads = (
            f"subjectid_{tutor_id}_{index}",
            f"trialsubjid_{tutor_id}_{index}",
            f"buysubjid_{tutor_id}_{index}",
            f"editsubjid_{index}",
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertLessEqual(len(payload.encode("utf-8")), 64)

    def test_legacy_subject_limit_keeps_old_messages_safe(self):
        subject = "я" * (SUBJECT_NAME_MAX_BYTES // len("я".encode("utf-8")))
        self.assertLessEqual(len(subject.encode("utf-8")), SUBJECT_NAME_MAX_BYTES)
        for prefix in ("trial_subject_", "buy_subject_", "editsubj_"):
            self.assertLessEqual(len((prefix + subject).encode("utf-8")), 64)


if __name__ == "__main__":
    unittest.main()
