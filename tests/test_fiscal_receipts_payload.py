import unittest

from payments import build_agent_receipt


class AgentReceiptPayloadTests(unittest.TestCase):
    def _build(self, *, closing=False):
        return build_agent_receipt(
            amount_kop=200000,
            description="Индивидуальное занятие по химии",
            customer_email="student@example.com",
            tutor_name="Иванов Иван Иванович",
            inn="123456789012",
            supplier_phone="+79991234567",
            payment_method="full_payment" if closing else "full_prepayment",
            closing=closing,
        )

    def test_prepayment_payload_is_ffd_12_agent_receipt(self):
        receipt = self._build()
        self.assertEqual(receipt["FfdVersion"], "1.2")
        self.assertEqual(receipt["Taxation"], "usn_income")
        self.assertEqual(receipt["Email"], "student@example.com")
        self.assertNotIn("Payments", receipt)

        item = receipt["Items"][0]
        self.assertEqual(item["PaymentMethod"], "full_prepayment")
        self.assertEqual(item["PaymentObject"], "service")
        self.assertEqual(item["Tax"], "none")
        self.assertEqual(item["MeasurementUnit"], "шт")
        self.assertEqual(item["AgentData"], {"AgentSign": "another"})
        self.assertEqual(
            item["SupplierInfo"],
            {
                "Phones": ["+79991234567"],
                "Name": "Иванов Иван Иванович",
                "Inn": "123456789012",
            },
        )

    def test_closing_payload_uses_advance_without_new_payment(self):
        receipt = self._build(closing=True)
        self.assertEqual(receipt["FfdVersion"], "1.2")
        self.assertEqual(receipt["Items"][0]["PaymentMethod"], "full_payment")
        self.assertEqual(
            receipt["Payments"],
            {
                "Cash": 0,
                "Electronic": 0,
                "AdvancePayment": 200000,
                "Credit": 0,
                "Provision": 0,
            },
        )

    def test_invalid_supplier_inn_is_rejected(self):
        with self.assertRaises(ValueError):
            build_agent_receipt(
                amount_kop=200000,
                description="Занятие",
                customer_email="student@example.com",
                tutor_name="Иванов Иван Иванович",
                inn="123",
                supplier_phone="+79991234567",
                payment_method="full_prepayment",
            )

    def test_closing_receipt_requires_full_payment(self):
        with self.assertRaises(ValueError):
            build_agent_receipt(
                amount_kop=200000,
                description="Занятие",
                customer_email="student@example.com",
                tutor_name="Иванов Иван Иванович",
                inn="123456789012",
                supplier_phone="+79991234567",
                payment_method="full_prepayment",
                closing=True,
            )


if __name__ == "__main__":
    unittest.main()
