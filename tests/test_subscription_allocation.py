import unittest

from subscription_rules import allocated_unit_amount


class SubscriptionAllocationTests(unittest.TestCase):
    def test_allocation_preserves_total_exactly(self):
        for total_kop, lessons in ((2280000, 12), (4320000, 24), (6120000, 36), (10001, 12)):
            allocated = [allocated_unit_amount(total_kop, lessons, i) for i in range(1, lessons + 1)]
            self.assertEqual(sum(allocated), total_kop)
            self.assertLessEqual(max(allocated) - min(allocated), 1)

    def test_extra_kopecks_are_deterministic(self):
        amounts = [allocated_unit_amount(10001, 12, i) for i in range(1, 13)]
        self.assertEqual(amounts[0], 834)
        self.assertEqual(amounts[-1], 833)
        self.assertEqual(sum(amounts), 10001)

    def test_invalid_allocation_rejected(self):
        with self.assertRaises(ValueError):
            allocated_unit_amount(1000, 0, 1)
        with self.assertRaises(ValueError):
            allocated_unit_amount(1000, 10, 11)


if __name__ == "__main__":
    unittest.main()
