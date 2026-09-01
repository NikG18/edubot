import unittest

from pricing import FAMILY_DISCOUNT_PERCENT, SUBSCRIPTION_PACKAGES, get_subscription_discount


class PricingTests(unittest.TestCase):
    def test_subscription_packages_match_public_rules(self):
        self.assertEqual(SUBSCRIPTION_PACKAGES, ((4, 0), (8, 3), (12, 5), (24, 10)))

    def test_family_discount_is_ten_percent(self):
        self.assertEqual(FAMILY_DISCOUNT_PERCENT, 10)

    def test_unknown_package_is_rejected(self):
        with self.assertRaises(ValueError):
            get_subscription_discount(36)


if __name__ == "__main__":
    unittest.main()
