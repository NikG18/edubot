import unittest
from datetime import date, timedelta

from financial_rules import (
    booking_commission_rub,
    booking_revenue_rub,
    commission_rate,
    early_fifteen_unlock_date,
)


class FinancialRulesTests(unittest.TestCase):
    def test_base_commission(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=20,
                full_months_since_first_lesson=10,
            ).percent,
            25,
        )

    def test_twenty_percent_after_two_full_months(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=21,
                full_months_since_first_lesson=2,
            ).percent,
            20,
        )

    def test_standard_fifteen_percent_after_four_months(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=41,
                full_months_since_first_lesson=4,
            ).percent,
            15,
        )

    def test_early_unlock_allows_fifteen_before_four_months(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=41,
                full_months_since_first_lesson=2,
                early_fifteen_unlocked=True,
            ).percent,
            15,
        )

    def test_early_unlock_does_not_replace_monthly_41_lesson_condition(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=40,
                full_months_since_first_lesson=2,
                early_fifteen_unlocked=True,
            ).percent,
            20,
        )

    def test_early_unlock_is_permanent_input_not_repeated_achievement(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=45,
                full_months_since_first_lesson=3,
                early_fifteen_unlocked=True,
            ).percent,
            15,
        )

    def test_rolling_sixty_day_window_unlocks_at_100th_lesson(self):
        first = date(2026, 1, 1)
        lesson_dates = []
        for day in range(50):
            lesson_dates.extend([first + timedelta(days=day)] * 2)
        self.assertEqual(
            early_fifteen_unlock_date(lesson_dates, first),
            first + timedelta(days=49),
        )

    def test_rolling_sixty_day_window_can_start_after_first_day(self):
        first = date(2026, 1, 1)
        lesson_dates = [first]
        start = first + timedelta(days=30)
        for day in range(50):
            lesson_dates.extend([start + timedelta(days=day)] * 2)
        self.assertEqual(
            early_fifteen_unlock_date(lesson_dates, first),
            start + timedelta(days=49),
        )

    def test_unlock_must_happen_inside_first_four_months(self):
        first = date(2026, 1, 1)
        start = date(2026, 5, 1)
        lesson_dates = []
        for day in range(50):
            lesson_dates.extend([start + timedelta(days=day)] * 2)
        self.assertIsNone(early_fifteen_unlock_date(lesson_dates, first))

    def test_rate_is_retained_for_one_following_month(self):
        self.assertEqual(
            commission_rate(
                lessons_this_month=5,
                full_months_since_first_lesson=6,
                early_fifteen_unlocked=True,
                previous_month_percent=15,
            ).percent,
            15,
        )
        self.assertEqual(
            commission_rate(
                lessons_this_month=5,
                full_months_since_first_lesson=6,
                previous_month_percent=25,
            ).percent,
            25,
        )

    def test_fifteen_percent_is_retained_even_when_current_natural_rate_is_twenty(self):
        decision = commission_rate(
            lessons_this_month=25,
            full_months_since_first_lesson=6,
            early_fifteen_unlocked=True,
            previous_month_percent=15,
        )
        self.assertEqual(decision.percent, 15)
        self.assertIn("retained", decision.reason)

    def test_twenty_percent_is_not_retained_over_a_current_fifteen_percent_month(self):
        decision = commission_rate(
            lessons_this_month=45,
            full_months_since_first_lesson=6,
            previous_month_percent=20,
        )
        self.assertEqual(decision.percent, 15)

    def test_retention_cannot_chain_when_previous_natural_rate_is_twenty_five(self):
        decision = commission_rate(
            lessons_this_month=5,
            full_months_since_first_lesson=8,
            previous_month_percent=25,
        )
        self.assertEqual(decision.percent, 25)

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
