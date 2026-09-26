import unittest

from agent_report_pdf import build_report_pdf


class AgentReportPdfTests(unittest.TestCase):
    def test_builds_cyrillic_pdf(self):
        report = {
            "report_key": "AR-202609-P1-T2",
            "period_label": "01.09.2026-15.09.2026",
            "agent_name": "ИП Тест",
            "agent_inn": "123456789012",
            "tutor_name": "Иванов Иван Иванович",
            "tutor_inn": "987654321098",
            "lessons_count": 1,
            "gross_kop": 250000,
            "commission_adjustment_kop": 0,
            "commission_kop": 62500,
            "tutor_kop": 187500,
            "created_label": "16.09.2026 09:00 МСК",
        }
        items = [{
            "lesson_date": "10.09.2026",
            "time_slot": "18:00-19:00",
            "student_name": "Анна",
            "subject": "Химия",
            "outcome": "Проведено",
            "gross_kop": 250000,
            "commission_percent": 25,
            "commission_kop": 62500,
            "tutor_kop": 187500,
        }]
        pdf = build_report_pdf(report, items)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)


if __name__ == "__main__":
    unittest.main()
