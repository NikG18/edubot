import unittest

from financial_rules import booking_commission_rub, booking_revenue_rub, commission_rate


class FinancialRulesTests(unittest.TestCase):
    def test_base_commission(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=20,
                full_months_since_first_lesson=10,
                first_60_days_lessons=150,
            ).percent,
            25,
        )

    def test_twenty_percent_after_two_full_months(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=21,
                full_months_since_first_lesson=2,
                first_60_days_lessons=0,
            ).percent,
            20,
        )

    def test_fifteen_requires_first_sixty_day_qualification(self):
        not_qualified = commission_rate(
            lessons_this_month=50,
            full_months_since_first_lesson=5,
            first_60_days_lessons=100,
        )
        qualified = commission_rate(
            lessons_this_month=50,
            full_months_since_first_lesson=5,
            first_60_days_lessons=101,
        )
        self.assertEqual(not_qualified.percent, 20)
        self.assertEqual(qualified.percent, 15)

    def test_rate_is_retained_for_one_following_month(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=5,
                full_months_since_first_lesson=6,
                first_60_days_lessons=150,
                previous_month_percent=15,
            ).percent,
            15,
        )
        self.assertEqual(
            commission_rate(
                lessons_this_month=5,
                full_months_since_first_lesson=6,
                first_60_days_lessons=150,
                previous_month_percent=25,
            ).percent,
            25,
        )

    def test_free_trial_never_becomes_revenue(self):
        booking = {
            "booking_type": "trial",
            "stats_counted": True,
            "amount": 0,
            "commission_percent": 25,
        }
        self.assertEqual(booking_revenue_rub(booking, fallback_price_rub=2500), 0)
        self.assertEqual(booking_commission_rub(booking, 2500), 0)

    def test_direct_owner_booking_keeps_zero_commission(self):
        booking = {
            "booking_type": "regular",
            "stats_counted": True,
            "amount": 200000,
            "commission_percent": 0,
        }
        revenue = booking_revenue_rub(booking, fallback_price_rub=5000)
        self.assertEqual(revenue, 2000)
        self.assertEqual(booking_commission_rub(booking, revenue), 0)

    def test_booking_commission_snapshot_is_used(self):
        booking = {
            "booking_type": "regular",
            "stats_counted": True,
            "amount": 200000,
            "commission_percent": 20,
        }
        revenue = booking_revenue_rub(booking)
        self.assertEqual(booking_commission_rub(booking, revenue), 400)


if __name__ == "__main__":
    unittest.main()
