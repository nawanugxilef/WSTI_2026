import base64
import datetime
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.agents.chief_of_staff import ChiefOfStaff
from src.agents.finance_agent import FinanceAgent
from src.storage.db import get_connection, init_db
from src.storage.journal import JournalManager


class TestChiefOfStaffPipeline(unittest.TestCase):
    def setUp(self):
        self.main_conn = sqlite3.connect(":memory:")
        self.main_conn.row_factory = sqlite3.Row
        init_db(self.main_conn)

        # Seed standard accounts (including 3D Printing & Dog Walking ABN accounts)
        with self.main_conn:
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('1000', 'Cash in Bank - Everyday', 'bank')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('4000', '3D Printing Sales', 'business_revenue')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('4100', 'Dog Walking Services Revenue', 'business_revenue')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('5100', 'Materials & Consumables', 'expense')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('5400', 'Dog Walking Supplies', 'expense')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('5900', 'Personal Living Expenses', 'personal_drawings')")

        self.cos = ChiefOfStaff(main_conn=self.main_conn)
        self.jm = JournalManager(self.main_conn)

    def tearDown(self):
        self.main_conn.close()

    def test_pipeline_personal_note(self):
        summary = self.cos.process_note("Fictional Demo Cafe coffee 6.50 today")
        self.assertIn("6.50", summary.summary_text)
        self.assertEqual(summary.purpose, "personal")

        tb = self.jm.get_trial_balance()
        tot_deb = sum(r["total_debit"] for r in tb)
        tot_crd = sum(r["total_credit"] for r in tb)
        self.assertEqual(tot_deb, tot_crd)
        self.assertEqual(tot_deb, 6.50)

    def test_pipeline_business_3d_printing_note(self):
        summary = self.cos.process_note("[FICTIONAL DEMO] 3D Maker Supply 3D print nozzle kit 32.99 today")
        self.assertIn("32.99", summary.summary_text)
        self.assertEqual(summary.purpose, "business")
        self.assertEqual(summary.category, "materials_3d_printing")

        tb = self.jm.get_trial_balance()
        tot_deb = sum(r["total_debit"] for r in tb)
        tot_crd = sum(r["total_credit"] for r in tb)
        self.assertEqual(tot_deb, tot_crd)
        self.assertEqual(tot_deb, 32.99)


class TestReceiptPhotoPipeline(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.conn = get_connection(str(self.root / "fictional-demo.db"))
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        self.conn.execute("INSERT INTO accounts (code, name, type) VALUES ('1000', 'Cash in Bank', 'bank')")
        self.conn.execute("INSERT INTO accounts (code, name, type) VALUES ('5900', 'Personal Expenses', 'personal_drawings')")
        self.conn.commit()
        self.finance = FinanceAgent(api_key="")
        self.gdrive = Mock()
        self.gdrive.upload_receipt_bytes.return_value = "1FictionalDemoBackupId"
        self.cos = ChiefOfStaff(self.conn, gdrive=self.gdrive, finance_agent=self.finance)
        self.photo = "fictional-demo-receipt.png"
        self.photo_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+kv1sAAAAASUVORK5CYII="
        )
        self.receipt = {
            "merchant": "[FICTIONAL DEMO] Cafe",
            "date": datetime.date.today().isoformat(),
            "total_amount": 11.00,
            "category": "dining_coffee",
            "purpose": "personal",
            "claim_stream": "personal",
            "business_activity": "none",
            "business_pct": 0,
        }

    def _mock_ocr(self, *results):
        generate = Mock(side_effect=[SimpleNamespace(text=json.dumps(result)) for result in results])
        self.finance._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        return generate

    def test_photo_retries_missing_date_then_archives_and_posts_once(self):
        generate = self._mock_ocr({**self.receipt, "date": None}, self.receipt)
        caption = "[FICTIONAL DEMO] Coffee with Sam"

        result = self.cos.process_file(self.photo, user_note=caption, file_bytes=self.photo_bytes)
        self.assertEqual(result.status, "recorded")
        self.assertEqual(result.vendor, self.receipt["merchant"])
        self.assertIsNone(result.local_path)
        self.assertFalse((self.root / "receipts").exists())
        stored = self.cos.km.get_receipt(result.receipt_id)
        self.assertEqual(stored["notes"], caption)
        self.assertEqual(stored["gdrive_file_id"], "1FictionalDemoBackupId")
        self.assertIsNone(stored["local_path"])
        self.assertEqual(generate.call_count, 2)
        second_prompt = generate.call_args.kwargs["contents"][1]
        self.assertIn(caption, second_prompt)
        self.assertIn("Transaction date is missing", second_prompt)

        duplicate = self.cos.process_file(self.photo, user_note=caption, file_bytes=self.photo_bytes)
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(duplicate.receipt_id, 0)
        self.gdrive.upload_receipt_bytes.assert_called_once_with(
            self.photo_bytes, self.photo, file_hash=hashlib.sha256(self.photo_bytes).hexdigest()
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)
        balance = JournalManager(self.conn).get_trial_balance()
        self.assertEqual(sum(row["total_debit"] for row in balance), 11.00)
        self.assertEqual(sum(row["total_credit"] for row in balance), 11.00)

    def test_unreadable_amount_and_date_hold_photo_after_two_retries(self):
        incomplete = {**self.receipt, "date": None, "total_amount": None}
        generate = self._mock_ocr(incomplete, incomplete, incomplete)
        result = self.cos.process_file(self.photo, file_bytes=self.photo_bytes)

        self.assertEqual(generate.call_count, 3)
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.receipt_id, 0)
        self.assertIsNone(result.date)
        self.assertIsNone(result.total_amount)
        self.assertIsNone(result.local_path)
        self.assertFalse((self.root / "receipts").exists())
        self.assertEqual(result.gdrive_file_id, "1FictionalDemoBackupId")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM journal_transactions").fetchone()[0], 0)

    def test_partial_batch_posts_only_complete_aud_item(self):
        generate = self._mock_ocr({
            "document_type": "multi_receipt",
            "receipts": [self.receipt, {**self.receipt, "date": None, "total_amount": 5.50}],
        })
        caption = "[FICTIONAL DEMO] Batch"
        result = self.cos.process_batch_or_statement(self.photo, user_note=caption, file_bytes=self.photo_bytes)

        self.assertEqual(result["recorded_count"], 1)
        self.assertEqual(result["xp_earned"], 10)
        self.assertIn("Held for review (1)", result["summary_text"])
        self.assertIn(caption, generate.call_args.kwargs["contents"][1])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)
        self.assertIsNone(result["local_path"])
        self.assertEqual(result["gdrive_file_id"], "1FictionalDemoBackupId")
        self.assertFalse((self.root / "receipts").exists())

    def test_unavailable_drive_does_not_archive_locally_or_post(self):
        generate = self._mock_ocr(self.receipt)
        self.gdrive.upload_receipt_bytes.return_value = None
        self.gdrive.last_status = "not_configured"

        result = self.cos.process_file(self.photo, file_bytes=self.photo_bytes)
        batch = self.cos.process_batch_or_statement(self.photo, file_bytes=self.photo_bytes)

        self.assertEqual(result.status, "archive_unavailable")
        self.assertEqual(batch["status"], "archive_unavailable")
        self.assertEqual(batch["xp_earned"], 0)
        self.assertIsNone(result.local_path)
        self.assertFalse((self.root / "receipts").exists())
        generate.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM journal_transactions").fetchone()[0], 0)

    def test_resending_older_local_receipt_attaches_drive_without_duplicate_journal(self):
        generate = self._mock_ocr(self.receipt)
        photo_hash = hashlib.sha256(self.photo_bytes).hexdigest()
        data = {**self.receipt, "vendor": self.receipt["merchant"]}
        receipt_id = self.cos.km.record_receipt(data, file_hash=photo_hash, gdrive_status="not_configured")

        result = self.cos.process_file(self.photo, file_bytes=self.photo_bytes)

        self.assertEqual(result.status, "duplicate")
        self.assertEqual(self.cos.km.get_receipt(receipt_id)["gdrive_file_id"], "1FictionalDemoBackupId")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM journal_transactions").fetchone()[0], 1)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
