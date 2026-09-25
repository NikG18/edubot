import unittest
from datetime import date

from agent_report_rules import (
    period_bounds,
    period_is_closed,
    report_key,
)


class AgentReportRulesTests(unittest.TestCase):
    def test_period_bounds_leap_year(self):
        self.assertEqual(
            period_bounds(2028, 2, 1),
            (date(2028, 2, 1), date(2028, 2, 15)),
        )
        self.assertEqual(
            period_bounds(2028, 2, 2),
            (date(2028, 2, 16), date(2028, 2, 29)),
        )

    def test_period_closes_next_day(self):
        self.assertFalse(period_is_closed(date(2026, 9, 15), 2026, 9, 1))
        self.assertTrue(period_is_closed(date(2026, 9, 16), 2026, 9, 1))

    def test_report_key_is_stable(self):
        self.assertEqual(report_key(7, 2026, 9, 2), "AR-202609-P2-T7")


if __name__ == "__main__":
    unittest.main()
