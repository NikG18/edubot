import unittest
from decimal import Decimal

from pricing import (
    FAMILY_DISCOUNT_PERCENT,
    SUBSCRIPTION_PACKAGES,
    get_subscription_discount,
    subscription_total_kop,
    subscription_total_rub,
)


class PricingTests(unittest.TestCase):
    def test_subscription_packages_match_public_rules(self):
        self.assertEqual(SUBSCRIPTION_PACKAGES, ((12, 5), (24, 10), (36, 15)))

    def test_family_discount_is_ten_percent(self):
        self.assertEqual(FAMILY_DISCOUNT_PERCENT, 10)

    def test_unknown_package_is_rejected(self):
        with self.assertRaises(ValueError):
            get_subscription_discount(8)

    def test_subscription_totals_are_exact_to_kopecks(self):
        self.assertEqual(subscription_total_rub(1999, 12), Decimal("22788.60"))
        self.assertEqual(subscription_total_kop(1999, 12), 2278860)
        self.assertEqual(subscription_total_rub(1999, 24), Decimal("43178.40"))
        self.assertEqual(subscription_total_rub(1999, 36), Decimal("61169.40"))

    def test_nonpositive_price_is_rejected(self):
        with self.assertRaises(ValueError):
            subscription_total_rub(0, 12)


if __name__ == "__main__":
    unittest.main()
