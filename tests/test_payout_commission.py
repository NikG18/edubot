import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from financial_rules import payout_commission_rates
from agent_report_rules import percent_amount_kop
import financial_hardening as finance


class Context:
    def __init__(self, value):
        self.value = value
    async def __aenter__(self):
        return self.value
    async def __aexit__(self, *args):
        return False


class PayoutRatesTests(unittest.TestCase):
    def test_achievement_retention_expiry(self):
        # September qualifies; October misses; November returns to base.
        self.assertEqual(payout_commission_rates(25, 20), (25, 20))
        self.assertEqual(payout_commission_rates(20, 25), (20, 20))
        self.assertEqual(payout_commission_rates(25, 25), (25, 25))

    def test_requalification_and_further_improvement(self):
        self.assertEqual(payout_commission_rates(20, 20), (20, 20))
        self.assertEqual(payout_commission_rates(20, 15), (20, 15))
        self.assertEqual(payout_commission_rates(15, 20), (15, 15))
        self.assertEqual(payout_commission_rates(20, 25), (20, 20))

    def test_money_uses_half_up_kopeks(self):
        self.assertEqual(percent_amount_kop(10, 25), 3)
        self.assertEqual(percent_amount_kop(101, 12.5), 13)


class FinancialPeriodsTests(unittest.IsolatedAsyncioTestCase):
    async def test_january_uses_december_natural_level(self):
        conn = SimpleNamespace(fetchval=AsyncMock(return_value=date(2025, 1, 1)))
        with patch.object(finance, '_month_lesson_count', AsyncMock(side_effect=[5, 41])) as count, \
             patch.object(finance, '_early_unlock_date', AsyncMock(return_value=None)):
            self.assertEqual(await finance._auto_period_rates(conn, 7, 2026, 1), ((15, 15), 5))
            self.assertEqual(count.await_args_list[1].args[2:], (2025, 12))

    async def test_generation_date_cannot_change_first_half_rate(self):
        with patch.object(finance, '_auto_period_rates', AsyncMock(return_value=((25, 20), 21))):
            self.assertEqual(await finance.calculate_auto_commission(7, 2026, 1, 1, conn=object()), (25, 21))
            self.assertEqual(await finance.calculate_auto_commission(7, 2026, 1, 2, conn=object()), (20, 21))

    async def _recalculate(self, mode='auto', frozen=None, owner=False):
        rows = [
            {'id': 1, 'date': '10.09.2026', 'booking_type': 'regular', 'stats_counted': True,
             'amount': 100000, 'commission_percent': 12},
            {'id': 2, 'date': '20.09.2026', 'booking_type': 'regular', 'stats_counted': True,
             'amount': 100000, 'commission_percent': 14},
            {'id': 3, 'date': '20.09.2026', 'booking_type': 'trial', 'stats_counted': True,
             'amount': 0, 'fallback_price': 2500, 'commission_percent': 25},
        ]
        conn = SimpleNamespace(execute=AsyncMock(), fetchval=AsyncMock(return_value=bool(frozen)),
                               fetch=AsyncMock(return_value=frozen or []))
        pool = SimpleNamespace(acquire=lambda: Context(conn))
        tutor = {'commission_mode': mode, 'commission_percent': 25, 'inn': 'test'}
        with patch.object(finance._db, '_ensure_pool', AsyncMock()), \
             patch.object(finance._db._legacy, 'pool', pool), \
             patch.object(finance._db, 'get_all_tutors', AsyncMock(return_value={7: tutor})), \
             patch.object(finance, '_month_rows', AsyncMock(return_value=rows)), \
             patch.object(finance, '_auto_period_rates', AsyncMock(return_value=((25, 20), 21))), \
             patch.object(finance.payments, 'is_operator_tutor', return_value=owner):
            await finance.recalculate_monthly_stats(7, 2026, 9)
        return conn.execute.await_args.args[1:]

    async def test_month_totals_sum_two_rates_and_exclude_trials(self):
        args = await self._recalculate()
        self.assertEqual(args[3:7], (2, 2000, 450, 1550))

    async def test_manual_uses_each_booking_snapshot(self):
        args = await self._recalculate(mode='manual')
        self.assertEqual(args[3:7], (2, 2000, 260, 1740))

    async def test_operator_commission_is_zero(self):
        args = await self._recalculate(owner=True)
        self.assertEqual(args[5:7], (0, 2000))

    async def test_finalized_first_half_survives_mode_change(self):
        args = await self._recalculate(mode='manual', frozen=[{
            'period_no': 1, 'lessons_count': 1, 'gross_amount_kop': 100000,
            'commission_amount_kop': 25000,
        }])
        self.assertEqual(args[3:7], (2, 2000, 390, 1610))

    async def test_separate_categories_with_no_paid_lessons(self):
        conn = SimpleNamespace(fetchrow=AsyncMock(return_value={'trial_lessons': 3, 'paid_lessons': 0}),
                               fetchval=AsyncMock(return_value=2))
        with patch.object(finance._db._legacy, 'pool', SimpleNamespace(acquire=lambda: Context(conn))):
            counts = await finance._category_counts(7, 2026, 9)
        self.assertEqual(counts, {'trial_lessons': 3, 'paid_lessons': 0, 'active_subscriptions': 2})
        self.assertEqual(conn.fetchrow.await_args.args[1:], (7, 2026, 9))
        self.assertEqual(conn.fetchval.await_args.args[1:], (7,))
