import unittest

from booking_visibility_rules import (
    admin_booking_matches,
    can_offer_separate_payment,
    is_trial_booking,
)


class BookingVisibilityRulesTests(unittest.TestCase):
    def test_trial_never_appears_as_separately_payable(self):
        booking = {
            "booking_type": "trial",
            "status": "confirmed",
            "subject": "Химия",
        }
        self.assertTrue(is_trial_booking(booking))
        self.assertFalse(can_offer_separate_payment(booking))

    def test_legacy_trial_prefix_is_also_protected(self):
        booking = {
            "booking_type": "regular",
            "status": "confirmed",
            "subject": "Пробное: Физика",
        }
        self.assertTrue(is_trial_booking(booking))
        self.assertFalse(can_offer_separate_payment(booking))

    def test_confirmed_regular_can_be_offered_for_payment(self):
        booking = {
            "booking_type": "regular",
            "status": "confirmed",
            "subject": "Математика",
        }
        self.assertTrue(can_offer_separate_payment(booking))

    def test_trial_is_only_in_dedicated_admin_section(self):
        booking = {
            "booking_type": "trial",
            "status": "confirmed",
            "subject": "Химия",
        }
        self.assertTrue(admin_booking_matches(booking, "trials"))
        self.assertFalse(admin_booking_matches(booking, "confirmed"))

    def test_regular_booking_remains_in_status_section(self):
        booking = {
            "booking_type": "regular",
            "status": "confirmed",
            "subject": "Химия",
        }
        self.assertFalse(admin_booking_matches(booking, "trials"))
        self.assertTrue(admin_booking_matches(booking, "confirmed"))


if __name__ == "__main__":
    unittest.main()
