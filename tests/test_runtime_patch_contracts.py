import unittest

import contact_delivery_hardening as contact
import help_text_hardening as help_text
import subscription_booking
import telegram_messaging_identity_hardening as identity
import telegram_payment_hardening as payment


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
            subscription_booking._telegram_subscription_confirm,
        )
        for function in replacements:
            with self.subTest(function=function.__name__):
                self.assertEqual(function.__code__.co_freevars, ())

    def test_new_telegram_callbacks_fit_callback_data_limit(self):
        # Telegram user ids are currently well below signed 64-bit; use the full
        # signed range plus a large tutor id as a conservative regression bound.
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


if __name__ == "__main__":
    unittest.main()
