import os
import unittest

# Pure receipt tests do not connect to PostgreSQL, but legacy database modules read
# configuration during import.
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1:1/unused")
os.environ["FISCAL_AGENT_SIGN"] = "another"
os.environ["FISCAL_TAXATION"] = "usn_income"

import fiscalization as fiscal  # noqa: E402


PROFILE = {
    "supplier_name": "Иванов Иван Иванович",
    "supplier_inn": "123456789012",
    "supplier_phone": "+79991234567",
}


class FiscalizationPayloadTests(unittest.TestCase):
    def test_prepayment_receipt_contains_agent_supplier_and_service_fields(self):
        receipt = fiscal.build_agent_prepayment_receipt(
            amount_kop=250000,
            description="Занятие: математика",
            customer_email="student@example.com",
            profile=PROFILE,
        )
        self.assertEqual(receipt["Taxation"], "usn_income")
        self.assertEqual(receipt["Email"], "student@example.com")
        self.assertEqual(len(receipt["Items"]), 1)
        item = receipt["Items"][0]
        self.assertEqual(item["Price"], 250000)
        self.assertEqual(item["Amount"], 250000)
        self.assertEqual(item["Quantity"], 1)
        self.assertEqual(item["Tax"], "none")
        self.assertEqual(item["PaymentMethod"], "full_prepayment")
        self.assertEqual(item["PaymentObject"], "service")
        self.assertEqual(item["MeasurementUnit"], "шт")
        self.assertEqual(item["AgentData"], {"AgentSign": "another"})
        self.assertEqual(item["SupplierInfo"]["Inn"], PROFILE["supplier_inn"])
        self.assertEqual(item["SupplierInfo"]["Name"], PROFILE["supplier_name"])
        self.assertEqual(item["SupplierInfo"]["Phones"], [PROFILE["supplier_phone"]])

    def test_closing_receipt_uses_advance_payment_not_new_electronic_payment(self):
        prepayment = fiscal.build_agent_prepayment_receipt(
            amount_kop=250000,
            description="Занятие: математика",
            customer_email="student@example.com",
            profile=PROFILE,
        )
        closing = fiscal.build_agent_closing_receipt(prepayment)
        self.assertEqual(closing["Items"][0]["PaymentMethod"], "full_payment")
        self.assertEqual(closing["Payments"]["AdvancePayment"], 250000)
        self.assertEqual(closing["Payments"]["Electronic"], 0)
        # Исходный snapshot не мутируется.
        self.assertEqual(prepayment["Items"][0]["PaymentMethod"], "full_prepayment")

    def test_agent_sign_has_no_implicit_legal_default(self):
        old = fiscal.FISCAL_AGENT_SIGN
        try:
            fiscal.FISCAL_AGENT_SIGN = ""
            with self.assertRaises(fiscal.FiscalizationError):
                fiscal.build_agent_prepayment_receipt(
                    amount_kop=10000,
                    description="Тест",
                    customer_email="student@example.com",
                    profile=PROFILE,
                )
        finally:
            fiscal.FISCAL_AGENT_SIGN = old

    def test_phone_normalization(self):
        self.assertEqual(fiscal.normalize_supplier_phone("8 (999) 123-45-67"), "+79991234567")
        with self.assertRaises(fiscal.FiscalProfileError):
            fiscal.normalize_supplier_phone("123")

    def test_npd_supplier_inn_must_be_12_digits(self):
        self.assertEqual(fiscal.validate_supplier_inn("1234 5678 9012"), "123456789012")
        with self.assertRaises(fiscal.FiscalProfileError):
            fiscal.validate_supplier_inn("1234567890")


if __name__ == "__main__":
    unittest.main()
