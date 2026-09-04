import unittest

from subscription_rules import allocated_unit_amount, next_available_unit_index


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

    def test_released_slot_is_reused_before_growing_index(self):
        occupied = {1, 2, 3, 4}
        occupied.remove(2)  # unit 2 was cancelled/released
        self.assertEqual(next_available_unit_index(12, occupied), 2)

    def test_many_cancel_rebook_cycles_never_exceed_package_size(self):
        occupied = set()
        for _ in range(50):
            unit_index = next_available_unit_index(12, occupied)
            self.assertEqual(unit_index, 1)
            occupied.add(unit_index)
            occupied.remove(unit_index)  # cancellation releases the slot again

        for expected in range(1, 13):
            unit_index = next_available_unit_index(12, occupied)
            self.assertEqual(unit_index, expected)
            occupied.add(unit_index)

        self.assertIsNone(next_available_unit_index(12, occupied))

    def test_reused_slots_keep_exact_fiscal_total(self):
        total_kop = 10001
        occupied = set()
        consumed_amounts = []

        # Several cancelled reservations repeatedly use slot 1 but never become
        # fiscalized consumption.
        for _ in range(20):
            unit_index = next_available_unit_index(12, occupied)
            self.assertEqual(unit_index, 1)
            occupied.add(unit_index)
            occupied.remove(unit_index)

        # When all twelve lessons are finally consumed, the allocation still uses
        # exactly slots 1..12 and therefore sums to the original prepayment.
        for _ in range(12):
            unit_index = next_available_unit_index(12, occupied)
            occupied.add(unit_index)
            consumed_amounts.append(allocated_unit_amount(total_kop, 12, unit_index))

        self.assertEqual(sum(consumed_amounts), total_kop)


if __name__ == "__main__":
    unittest.main()
