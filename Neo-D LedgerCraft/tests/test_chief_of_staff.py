import base64
import datetime
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.agents.chief_of_staff import ChiefOfStaff
from src.agents.finance_agent import FinanceAgent
from src.agents.receipt_progress import read_receipt_progress
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

    def _process_matching_photos(self, **second_fields):
        other_photo = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNg+M8AAAICAQB7CYF4AAAAAElFTkSuQmCC"
        )
        second_receipt = {**self.receipt, **second_fields}
        self._mock_ocr(self.receipt, second_receipt, second_receipt, second_receipt)
        first = self.cos.process_file(self.photo, file_bytes=self.photo_bytes)
        second = self.cos.process_file("fictional-demo-other-receipt.png", file_bytes=other_photo)
        return first, second

    def _journal_references(self):
        return [
            row["reference"]
            for row in self.conn.execute("SELECT reference FROM journal_transactions ORDER BY reference")
        ]

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
        self.assertEqual(duplicate.exact_duplicate_of, result.receipt_id)
        self.gdrive.upload_receipt_bytes.assert_called_once_with(
            self.photo_bytes, self.photo, file_hash=hashlib.sha256(self.photo_bytes).hexdigest()
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)
        balance = JournalManager(self.conn).get_trial_balance()
        self.assertEqual(sum(row["total_debit"] for row in balance), 11.00)
        self.assertEqual(sum(row["total_credit"] for row in balance), 11.00)
        state = read_receipt_progress(self.root / "fictional-demo.db")
        events = state["events"]
        self.assertEqual([event["stage"] for event in events], [
            "neo_intake", "corgi_reading", "bear_checking", "girraffe_filing", "terminal",
            "neo_intake", "terminal",
        ])
        self.assertEqual([event["seq"] for event in events], [1, 2, 3, 4, 5, 1, 2])
        self.assertEqual([event["outcome"] for event in events if event["stage"] == "terminal"], [
            "recorded", "duplicate",
        ])
        self.assertEqual(len({event["run_id"] for event in events}), 2)
        snapshot = (self.root / "receipt_progress.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(snapshot), state)
        for private_value in (caption, self.receipt["merchant"], "1FictionalDemoBackupId", self.photo):
            self.assertNotIn(private_value, snapshot)

    def test_confirming_possible_duplicate_posts_once_and_survives_later_correction(self):
        first, second = self._process_matching_photos()
        original_reference = f"receipt:{first.receipt_id}"
        distinct_reference = f"receipt:{second.receipt_id}"

        self.assertGreater(second.receipt_id, first.receipt_id)
        self.assertEqual(second.status, "flagged")
        self.assertIsNone(second.exact_duplicate_of)
        self.assertIn(f"existing receipt #{first.receipt_id}", second.flag_reason)
        self.assertEqual(self._journal_references(), [original_reference])

        with self.assertRaisesRegex(ValueError, "no longer has that possible duplicate match"):
            self.cos.confirm_distinct_receipt(second.receipt_id, second.receipt_id)
        self.assertEqual(self.cos.km.get_receipt(second.receipt_id)["status"], "flagged")
        self.assertEqual(self._journal_references(), [original_reference])

        confirmed = self.cos.confirm_distinct_receipt(second.receipt_id, first.receipt_id)
        self.assertEqual(confirmed.receipt_id, second.receipt_id)
        self.assertIsNone(confirmed.flag_reason)
        self.assertEqual(self._journal_references(), [original_reference, distinct_reference])

        repeated = self.cos.confirm_distinct_receipt(second.receipt_id, first.receipt_id)
        self.assertEqual(repeated.status, confirmed.status)
        corrected = self.cos.correct_receipt(second.receipt_id, category="general")
        self.assertEqual(corrected.category, "general")
        self.assertIsNone(corrected.flag_reason)
        self.assertEqual(self._journal_references(), [original_reference, distinct_reference])
        balance = JournalManager(self.conn).get_trial_balance()
        self.assertEqual(sum(row["total_debit"] for row in balance), 22.00)
        self.assertEqual(sum(row["total_credit"] for row in balance), 22.00)

    def test_correcting_original_does_not_hold_it_against_its_possible_duplicate(self):
        self.receipt.update(
            category="materials_3d_printing",
            purpose="business",
            claim_stream="abn",
            business_activity="printing_3d",
            business_pct=100,
            tax_amount=1.00,
        )
        first, second = self._process_matching_photos()
        self.assertEqual(first.status, "recorded")
        self.assertEqual(second.status, "flagged")
        self.assertIn(f"existing receipt #{first.receipt_id}", second.flag_reason)
        reference = f"receipt:{first.receipt_id}"
        self.assertEqual(self._journal_references(), [reference])

        split = self.cos.correct_receipt(first.receipt_id, business_pct=50)
        self.assertEqual(split.status, "corrected")
        self.assertEqual(split.business_pct, 50)
        self.assertEqual(split.claimable_amount, 5.50)
        self.assertEqual(split.personal_share, 5.50)
        self.assertEqual(split.tax_amount, 0.50)
        self.assertEqual(split.gdrive_file_id, first.gdrive_file_id)
        self.assertEqual(self._journal_references(), [reference])

        personal = self.cos.correct_receipt(first.receipt_id, claim_stream="personal")
        self.assertEqual(personal.status, "corrected")
        self.assertEqual(personal.business_pct, 0)
        self.assertEqual(personal.claimable_amount, 0)
        self.assertEqual(personal.personal_share, 11.00)
        self.assertEqual(personal.gdrive_file_id, first.gdrive_file_id)
        self.assertEqual(self._journal_references(), [reference])
        held = self.cos.km.get_receipt(second.receipt_id)
        self.assertEqual(held["status"], "flagged")
        self.assertEqual(held["flag_reason"], second.flag_reason)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 2)
        self.assertEqual(self.gdrive.upload_receipt_bytes.call_count, 2)

    def test_journal_failure_leaves_possible_duplicate_held_and_retryable(self):
        first, second = self._process_matching_photos()
        journal = self.cos.km.journal
        post_transaction = journal.post_transaction

        def fail_after_writing(*args, **kwargs):
            post_transaction(*args, **kwargs)
            raise sqlite3.OperationalError("synthetic journal failure")

        with patch.object(journal, "post_transaction", side_effect=fail_after_writing):
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic journal failure"):
                self.cos.confirm_distinct_receipt(second.receipt_id, first.receipt_id)

        still_held = self.cos.km.get_receipt(second.receipt_id)
        self.assertEqual(still_held["status"], "flagged")
        self.assertEqual(still_held["flag_reason"], second.flag_reason)
        self.assertEqual(self._journal_references(), [f"receipt:{first.receipt_id}"])

        self.cos.confirm_distinct_receipt(second.receipt_id, first.receipt_id)
        self.assertEqual(self._journal_references(), [f"receipt:{first.receipt_id}", f"receipt:{second.receipt_id}"])

    def test_confirming_distinct_receipt_preserves_independent_gst_hold(self):
        first, second = self._process_matching_photos(tax_amount=2.00)
        self.assertIn("Likely duplicate", second.flag_reason)
        self.assertIn("GST amount", second.flag_reason)

        updated = self.cos.confirm_distinct_receipt(second.receipt_id, first.receipt_id)

        self.assertEqual(updated.status, "flagged")
        self.assertIn("GST amount", updated.flag_reason)
        self.assertNotIn("Likely duplicate", updated.flag_reason)
        self.assertEqual(self._journal_references(), [f"receipt:{first.receipt_id}"])

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
        progress = read_receipt_progress(self.root / "fictional-demo.db")["events"]
        self.assertEqual([event["stage"] for event in progress], [
            "neo_intake", "corgi_reading", "bear_checking", "terminal",
        ])
        self.assertEqual(progress[-1]["outcome"], "needs_review")

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
        progress = read_receipt_progress(self.root / "fictional-demo.db")["events"]
        self.assertEqual([event["stage"] for event in progress], ["neo_intake", "terminal"] * 2)
        self.assertEqual([event["outcome"] for event in progress if event["stage"] == "terminal"], [
            "archive_unavailable", "archive_unavailable",
        ])

    def test_resending_older_local_receipt_attaches_drive_without_duplicate_journal(self):
        generate = self._mock_ocr(self.receipt)
        photo_hash = hashlib.sha256(self.photo_bytes).hexdigest()
        data = {**self.receipt, "vendor": self.receipt["merchant"]}
        receipt_id = self.cos.km.record_receipt(data, file_hash=photo_hash, gdrive_status="not_configured")

        result = self.cos.process_file(self.photo, file_bytes=self.photo_bytes)

        self.assertEqual(result.status, "duplicate")
        self.assertEqual(result.receipt_id, 0)
        self.assertEqual(result.exact_duplicate_of, receipt_id)
        self.assertEqual(self.cos.km.get_receipt(receipt_id)["gdrive_file_id"], "1FictionalDemoBackupId")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM journal_transactions").fetchone()[0], 1)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
