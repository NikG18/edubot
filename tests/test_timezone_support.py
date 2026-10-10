import unittest

from timezone_support import booking_local_text, localize_slot, slot_button_label, timezone_label


class TimezoneSupportTests(unittest.TestCase):
    def test_moscow_is_identity(self):
        self.assertEqual(localize_slot("10.10.2026", "18:00-19:00", 180),
                         ("10.10.2026", "18:00-19:00"))

    def test_eastward_conversion(self):
        self.assertEqual(localize_slot("10.10.2026", "18:00-19:00", 300),
                         ("10.10.2026", "20:00-21:00"))
        self.assertIn("UTC+5", slot_button_label("10.10.2026", "18:00-19:00", 300))

    def test_conversion_can_cross_midnight(self):
        self.assertEqual(localize_slot("10.10.2026", "22:00-23:00", 600),
                         ("11.10.2026", "05:00-06:00"))
        self.assertIn("11.10.2026", booking_local_text("10.10.2026", "22:00-23:00", 600))

    def test_timezone_labels(self):
        self.assertEqual(timezone_label(180), "МСК (UTC+3)")
        self.assertEqual(timezone_label(330), "UTC+5:30")


if __name__ == "__main__":
    unittest.main()
