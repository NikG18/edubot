import unittest

from tutor_students_rules import group_tutor_students, platform_label


class TutorStudentsRulesTests(unittest.TestCase):
    def test_linked_telegram_and_vk_accounts_are_one_student(self):
        bookings = {
            1: {"tutor_id": 7, "student_id": 100, "user_id": 111, "user_platform": "telegram", "username": "Иван", "status": "completed"},
            2: {"tutor_id": 7, "student_id": 100, "user_id": 222, "user_platform": "vk", "username": "Иван", "status": "pending"},
            3: {"tutor_id": 7, "student_id": 100, "user_id": 111, "user_platform": "telegram", "username": "Иван", "status": "paid"},
        }
        groups = group_tutor_students(bookings, 7)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["completed_lessons"], 1)
        self.assertEqual({bid for bid, _ in groups[0]["active_bookings"]}, {2, 3})
        self.assertEqual(platform_label(groups[0]["platforms"]), "TG/VK")

    def test_equal_unlinked_numeric_ids_do_not_merge_across_platforms(self):
        bookings = {
            1: {"tutor_id": 7, "student_id": None, "user_id": 555, "user_platform": "telegram", "username": "TG", "status": "pending"},
            2: {"tutor_id": 7, "student_id": None, "user_id": 555, "user_platform": "vk", "username": "VK", "status": "confirmed"},
        }
        groups = group_tutor_students(bookings, 7)
        self.assertEqual(len(groups), 2)
        self.assertEqual({platform_label(group["platforms"]) for group in groups}, {"TG", "VK"})

    def test_completed_count_is_not_confused_with_paid_future_booking(self):
        bookings = {
            1: {"tutor_id": 7, "student_id": 100, "user_id": 1, "user_platform": "telegram", "username": "Иван", "status": "completed"},
            2: {"tutor_id": 7, "student_id": 100, "user_id": 1, "user_platform": "telegram", "username": "Иван", "status": "completed"},
            3: {"tutor_id": 7, "student_id": 100, "user_id": 1, "user_platform": "telegram", "username": "Иван", "status": "paid"},
        }
        group = group_tutor_students(bookings, 7)[0]
        self.assertEqual(group["completed_lessons"], 2)
        self.assertEqual([bid for bid, _ in group["active_bookings"]], [3])

    def test_other_tutor_is_excluded(self):
        bookings = {
            1: {"tutor_id": 7, "student_id": 100, "user_id": 1, "user_platform": "telegram", "username": "A", "status": "pending"},
            2: {"tutor_id": 8, "student_id": 100, "user_id": 1, "user_platform": "telegram", "username": "A", "status": "completed"},
        }
        groups = group_tutor_students(bookings, 7)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["completed_lessons"], 0)


if __name__ == "__main__":
    unittest.main()
