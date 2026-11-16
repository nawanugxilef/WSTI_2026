import base64
import datetime
import sqlite3
import unittest

from src.agents.chief_of_staff import ChiefOfStaff
from src.agents.finance_agent import ParsedReceipt
from src.email_scout.scout import Email, EmailScout, looks_like_receipt, same_vendor
from src.email_scout.sources import _plain_text, demo_inbox
from src.storage.db import init_db


def _email(i, subject, body, day="2026-11-10"):
    return Email(f"t:{i}", "Shop <a@shop.example>", subject, datetime.date.fromisoformat(day), body)


class FakeFinance:
    """Stands in for Gemini: returns a fixed reading per email body."""

    def __init__(self, readings):
        self.readings = readings

    def parse_note(self, text, **kwargs):
        for key, reading in self.readings.items():
            if key in text:
                return ParsedReceipt(**reading)
        raise AssertionError("unexpected email")


class TestEmailScout(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        with self.conn:
            for code, name, typ in [("1000", "Cash in Bank", "bank"), ("5100", "Materials", "expense"),
                                    ("5900", "Personal Living Expenses", "personal_drawings")]:
                self.conn.execute("INSERT INTO accounts (code, name, type) VALUES (?, ?, ?)", (code, name, typ))
        base = {"category": "materials_3d_printing", "currency": "AUD"}
        self.cos = ChiefOfStaff(main_conn=self.conn, finance_agent=FakeFinance({
            "FILAMENT": {**base, "vendor": "Fictional Filament Co", "date": "2026-11-10", "total_amount": 54.0},
            "NOZZLE": {**base, "vendor": "Fictional Maker Supply", "date": "2026-11-11", "total_amount": 19.5},
            "NOTOTAL": {**base, "vendor": "Somewhere", "date": "2026-11-11", "total_amount": None},
        }))
        self.scout = EmailScout(self.cos)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]

    def test_new_receipt_is_recorded_then_repeat_and_rescan_are_not(self):
        first = self.scout.scan_one(_email(1, "Tax invoice", "FILAMENT total $54.00"))
        self.assertEqual(first.status, "recorded")
        self.assertEqual(self.count(), 1)

        # Same purchase arriving again (e.g. a second copy of the invoice) is marked, not recorded.
        again = self.scout.scan_one(_email(2, "Your receipt", "FILAMENT total $54.00", day="2026-11-12"))
        self.assertEqual((again.status, again.receipt_id), ("repeat", first.receipt_id))
        self.assertEqual(self.count(), 1)

        # Scanning the same message twice never touches Gemini or the ledger again.
        self.assertEqual(self.scout.scan_one(_email(1, "Tax invoice", "x")).status, "already_scanned")

    def test_different_receipt_is_recorded(self):
        self.scout.scan_one(_email(1, "Tax invoice", "FILAMENT total $54.00"))
        self.assertEqual(self.scout.scan_one(_email(3, "Receipt", "NOZZLE total $19.50")).status, "recorded")
        self.assertEqual(self.count(), 2)

    def test_newsletter_and_missing_total_are_not_recorded(self):
        self.assertEqual(self.scout.scan_one(_email(4, "Top stories", "Read more")).status, "not_receipt")
        self.assertEqual(self.scout.scan_one(_email(5, "Receipt", "NOTOTAL shipping $0 later")).status, "needs_review")
        self.assertEqual(self.count(), 0)

    def test_helpers(self):
        self.assertTrue(same_vendor("OFFICEWORKS PARRAMATTA", "Officeworks"))
        self.assertFalse(same_vendor("Bunnings", "Officeworks"))
        self.assertFalse(looks_like_receipt(_email(6, "Sale!", "Big sale this weekend")))
        self.assertEqual(len(demo_inbox()), 4)
        data = base64.urlsafe_b64encode(b"<p>Total&nbsp;$5</p>").decode().rstrip("=")
        self.assertEqual(_plain_text({"mimeType": "text/html", "body": {"data": data}}), "Total $5")


if __name__ == "__main__":
    unittest.main()
