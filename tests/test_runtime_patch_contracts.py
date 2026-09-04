import unittest

import contact_delivery_hardening as contact
import financial_display_hardening as financial_display
import help_text_hardening as help_text
from receipt_retry_policy import closing_receipt_claim_action
import telegram_messaging_identity_hardening as identity
import telegram_payment_hardening as payment
import student_navigation_hardening as navigation
import subscription_purchase_hardening as subscription_purchase
import subscription_cancel_hardening as subscription_cancel
import tutor_confirmation_hardening as tutor_confirmation
import tutor_students_hardening as tutor_students
from tutor_students_rules import group_tutor_students, platform_label
import vk_admin_stats_hardening as vk_stats
import vk_restart_hardening as vk_restart


class RuntimePatchContractTests(unittest.TestCase):
    def test_registered_handler_replacements_are_closure_free(self):
        replacements = (
            help_text._telegram_help,
            help_text._vk_help,
            payment._stale_payment_method,
            contact._telegram_student_to_tutor,
            contact._vk_student_to_tutor,
            identity._telegram_reply_button,
            identity._telegram_tutor_contact_start,
            identity._telegram_tutor_contact_chosen,
            financial_display._tutor_stats_menu,
            financial_display._tutor_stats_month,
            tutor_students._telegram_show_students,
            vk_stats._vk_admin_stats_menu,
            vk_stats._vk_admin_stats_tutors_overview,
            vk_stats._vk_admin_stats_tutors_month,
            vk_stats._vk_admin_stats_students,
            vk_restart._fresh_start,
            navigation._telegram_fresh_start,
            navigation._telegram_fresh_back,
            navigation._vk_fresh_back,
            subscription_purchase._telegram_confirm_buy_subscription,
        )
        for function in replacements:
            with self.subTest(function=function.__name__):
                self.assertEqual(function.__code__.co_freevars, ())

    def test_new_telegram_callbacks_fit_callback_data_limit(self):
        student_id = 2**63 - 1
        tutor_id = 2**31 - 1
        callbacks = (
            f"reply_tg_{student_id}_{tutor_id}",
            f"reply_vk_{student_id}_{tutor_id}",
            f"tutor_contact_student_tg_{student_id}",
            f"tutor_contact_student_vk_{student_id}",
        )
        for callback in callbacks:
            with self.subTest(callback=callback):
                self.assertLessEqual(len(callback.encode("utf-8")), 64)

    def test_help_text_uses_current_discount_and_payment_rules(self):
        text = help_text.CURRENT_HELP_TEXT
        self.assertIn("12 занятий — скидка 5%", text)
        self.assertIn("24 — 10%", text)
        self.assertIn("36 — 15%", text)
        self.assertIn("Семейная скидка — 10%", text)
        self.assertNotIn("Семейная скидка — 20%", text)
        self.assertIn("Скидки и акции не суммируются", text)
        self.assertIn("Не переводите оплату вручную по номеру телефона", text)
        self.assertNotIn("+7(933)", text)
        self.assertNotIn("+7933", text)

    def test_closing_receipt_retry_policy(self):
        expected = {
            "submitted": "already_submitted",
            "sent": "already_submitted",
            "sending": "in_progress",
            "unknown": "unknown",
            "failed": "retry",
            "prepared": "not_retryable",
            None: "not_retryable",
        }
        for status, action in expected.items():
            with self.subTest(status=status):
                self.assertEqual(closing_receipt_claim_action(status), action)

    def test_subscription_payment_details_are_student_only(self):
        student_text = tutor_confirmation._subscription_block_text(
            "subscription_payment_pending", 42
        )
        tutor_text = tutor_confirmation._tutor_subscription_block_text(
            "subscription_payment_pending"
        )
        self.assertIn("банк ещё не подтвердил", student_text)
        self.assertIn("откройте раздел «Оплата»", student_text)
        self.assertEqual(tutor_text, "✅ Занятие подтверждено. Ученик уведомлён.")
        self.assertNotIn("банк", tutor_text.lower())
        self.assertNotIn("оплат", tutor_text.lower())

    def test_student_navigation_resets_in_memory_state(self):
        telegram_source = navigation._telegram_fresh_start.__code__.co_names
        telegram_back_source = navigation._telegram_fresh_back.__code__.co_names
        vk_source = navigation._vk_fresh_back.__code__.co_names
        self.assertIn("clear", telegram_source)
        self.assertIn("clear", telegram_back_source)
        self.assertIn("delete", vk_source)

    def test_every_cancellation_path_releases_subscription_unit(self):
        source = __import__("inspect").getsource(
            subscription_cancel.install_subscription_cancel_release
        )
        self.assertIn("_db.change_booking_status = db_change_with_release", source)
        self.assertIn('str(new_status) == "cancelled"', source)
        self.assertIn("release_booking_unit", source)

    def test_tutor_panel_groups_linked_accounts_but_not_equal_unlinked_ids(self):
        bookings = {
            1: {"tutor_id": 7, "student_id": 100, "user_id": 111, "user_platform": "telegram", "username": "Иван", "status": "completed"},
            2: {"tutor_id": 7, "student_id": 100, "user_id": 222, "user_platform": "vk", "username": "Иван", "status": "pending"},
            3: {"tutor_id": 7, "student_id": None, "user_id": 555, "user_platform": "telegram", "username": "TG", "status": "pending"},
            4: {"tutor_id": 7, "student_id": None, "user_id": 555, "user_platform": "vk", "username": "VK", "status": "confirmed"},
        }
        groups = group_tutor_students(bookings, 7)
        self.assertEqual(len(groups), 3)
        linked = next(group for group in groups if group["key"] == ("student", 100))
        self.assertEqual(linked["completed_lessons"], 1)
        self.assertEqual(platform_label(linked["platforms"]), "TG/VK")

    def test_calendar_months_do_not_use_30_day_approximation(self):
        months = vk_stats.previous_calendar_months(2026, 3, 5)
        self.assertEqual(months, [(2026, 3), (2026, 2), (2026, 1), (2025, 12), (2025, 11)])

    def test_vk_stats_page_slice_clamps_out_of_range_page(self):
        page, max_page, rows = vk_stats._page_slice(list(range(25)), 99, 10)
        self.assertEqual((page, max_page), (2, 2))
        self.assertEqual(rows, [20, 21, 22, 23, 24])


if __name__ == "__main__":
    unittest.main()
