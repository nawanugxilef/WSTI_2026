import unittest
from src.agents.qa_agent import QAAgent


class TestQAAgent(unittest.TestCase):
    def setUp(self):
        self.qa = QAAgent(gst_rate=0.10, max_retries=2)

    def test_valid_receipt(self):
        data = {
            "vendor": "[FICTIONAL DEMO] Sydney 3D Maker Supplies",
            "date": "2026-10-15",
            "total_amount": 33.00,
            "tax_amount": 3.00,
            "currency": "AUD",
            "category": "materials_3d_printing",
            "purpose": "business",
        }
        res = self.qa.inspect_receipt(data)
        self.assertTrue(res.passed)
        self.assertEqual(len(res.issues), 0)

    def test_future_date_fails(self):
        data = {
            "vendor": "[FICTIONAL DEMO] Harbour Cafe",
            "date": "2099-01-01",
            "total_amount": 10.00,
            "currency": "AUD",
        }
        res = self.qa.inspect_receipt(data)
        self.assertFalse(res.passed)
        self.assertTrue(any("in the future" in issue for issue in res.issues))

    def test_gst_exceeding_max_share(self):
        # In Australia GST is 10% inclusive (total / 11). For $20, GST cannot be $8.00!
        data = {
            "vendor": "[FICTIONAL DEMO] Demo Office Store",
            "date": "2026-10-15",
            "total_amount": 20.00,
            "tax_amount": 8.00,
            "currency": "AUD",
        }
        res = self.qa.inspect_receipt(data)
        self.assertFalse(res.passed)
        self.assertTrue(any("exceeds expected maximum" in issue for issue in res.issues))

    def test_non_aud_currency_rejected(self):
        data = {
            "vendor": "[FICTIONAL DEMO] Foreign Store",
            "date": "2026-10-15",
            "total_amount": 50.00,
            "currency": "EUR",
        }
        res = self.qa.inspect_receipt(data)
        self.assertFalse(res.passed)
        self.assertTrue(any("must be in AUD" in issue for issue in res.issues))

    def test_max_retries_flags_instead_of_blocking(self):
        data = {
            "vendor": "[FICTIONAL DEMO] Unclear Receipt",
            "date": "2099-01-01",
            "total_amount": 10.00,
        }
        res = self.qa.inspect_receipt(data, retry_count=2)
        self.assertFalse(res.passed)
        self.assertTrue(res.flagged)
        self.assertIsNotNone(res.flag_reason)


if __name__ == "__main__":
    unittest.main()
