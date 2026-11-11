import asyncio
import datetime
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.error import BadRequest, NetworkError

from src.agents.chief_of_staff import ChiefOfStaff, IntakeSummary
from src.agents.finance_agent import FinanceAgent, ParsedReceipt, ReceiptItem
from src.interfaces.cli import seed_accounts
from src.interfaces.telegram_bot import (
    TelegramReceiptBot,
    build_correction_keyboard,
    parse_correction_tokens,
)
from src.interfaces.web_server import build_dashboard_payload
from src.storage.categories import ATO_SUGGESTION_DISCLAIMER
from src.storage.db import get_connection, init_db
from src.storage.gdrive import GDriveAdapter, extract_verified_drive_file_id
from src.storage.journal import JournalManager


class TestTelegramUXAndCorrections(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.db_path = os.path.join(self.tmp_dir.name, "test_ledger.db")
        self.conn = get_connection(self.db_path)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        seed_accounts(self.conn)
        self.jm = JournalManager(self.conn)

    def assert_compact_receipt_keyboard(self, keyboard, receipt_id, selected):
        rows = keyboard.inline_keyboard
        self.assertEqual([len(row) for row in rows], [1, 3])
        self.assertEqual(rows[0][0].text, "✏️ Category")
        self.assertEqual(rows[0][0].callback_data, f"corr:{receipt_id}:menu:controls")
        for button, (stream, label) in zip(rows[1], (("abn", "ABN"), ("payg", "PAYG"), ("personal", "Personal"))):
            self.assertIn(label, button.text)
            self.assertEqual(button.callback_data, f"corr:{receipt_id}:stream:{stream}")
            self.assertEqual("✓" in button.text, stream == selected)
        self.assertFalse(any(button.url for row in rows for button in row))

    def test_defect_1_explicit_gst_zero_preserved_on_110_aud_abn(self):
        """
        Regression test (1): Explicit GST=0 on a $110 AUD ABN receipt must stay $0.00
        and never be overwritten with $10.00 (1/11th).
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # Route A: Typed note with explicit GST=0
        summary_note = cos.process_note("Fictional 3D Maker Supply nozzle kit $110.00 gst=0 today")
        self.assertEqual(summary_note.claim_stream, "abn")
        self.assertEqual(summary_note.total_amount, 110.0)
        self.assertEqual(summary_note.claimable_amount, 110.0)
        self.assertEqual(summary_note.gst_status, "explicit")
        self.assertEqual(summary_note.raw_tax_amount, 0.0)
        self.assertEqual(summary_note.tax_amount, 0.0)

        # Route B: Direct record_receipt with explicit tax_amount=0.0
        rec_id_2 = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] GST Free 3D Supplier",
                "date": "2026-10-15",
                "total_amount": 110.00,
                "tax_amount": 0.0,
                "currency": "AUD",
                "category": "materials_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "tax_label": "Cost of sales",
            }
        )
        rec_2 = cos.km.get_receipt(rec_id_2)
        self.assertEqual(rec_2["gst_status"], "explicit")
        self.assertEqual(rec_2["raw_tax_amount"], 0.0)
        self.assertEqual(rec_2["tax_amount"], 0.0)

    def test_defect_2_vendor_only_correction_preserves_explicit_gst_and_unknown_gst(self):
        """
        Regression test (2): Vendor-only (or category/activity) correction on a receipt with
        explicit GST=$2.00 on $110.00 must preserve $2.00 (never overwrite to $10.00),
        and unknown GST must remain unknown ($0.00 claimed) across corrections.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # 1. Explicit GST = $2.00 on $110.00 AUD ABN receipt
        s_exp = cos.process_note("Fictional 3D Maker Supply resin order $110.00 gst=2.00 today")
        self.assertEqual(s_exp.gst_status, "explicit")
        self.assertEqual(s_exp.raw_tax_amount, 2.0)
        self.assertEqual(s_exp.tax_amount, 2.0)

        # Vendor-only correction must NOT change $2.00 GST to $10.00
        s_renamed = cos.correct_receipt(
            receipt_id=s_exp.receipt_id,
            vendor="[FICTIONAL DEMO] Renamed 3D Supplier",
        )
        self.assertEqual(s_renamed.vendor, "[FICTIONAL DEMO] Renamed 3D Supplier")
        self.assertEqual(s_renamed.gst_status, "explicit")
        self.assertEqual(s_renamed.raw_tax_amount, 2.0)
        self.assertEqual(s_renamed.tax_amount, 2.0)

        # 50% Business % correction scales the explicit $2.00 GST to $1.00 (and back to $2.00 at 100%)
        s_50 = cos.correct_receipt(receipt_id=s_exp.receipt_id, business_pct=50.0)
        self.assertEqual(s_50.tax_amount, 1.0)
        self.assertEqual(s_50.raw_tax_amount, 2.0)

        s_100 = cos.correct_receipt(receipt_id=s_exp.receipt_id, business_pct=100.0)
        self.assertEqual(s_100.tax_amount, 2.0)

        # 2. Unknown GST (no GST stated in note) stays unknown ($0.00 claimed)
        s_unk = cos.process_note("Fictional 3D Maker Supply build plate $110.00 today")
        self.assertEqual(s_unk.gst_status, "unknown")
        self.assertIsNone(s_unk.raw_tax_amount)
        self.assertEqual(s_unk.tax_amount, 0.0)
        self.assertIn("GST: `Unknown`", s_unk.summary_text)

        # Vendor-only correction on unknown GST keeps it unknown ($0.00)
        s_unk_corr = cos.correct_receipt(
            receipt_id=s_unk.receipt_id,
            vendor="[FICTIONAL DEMO] Sydney Build Plates",
        )
        self.assertEqual(s_unk_corr.gst_status, "unknown")
        self.assertIsNone(s_unk_corr.raw_tax_amount)
        self.assertEqual(s_unk_corr.tax_amount, 0.0)

    def test_defect_3_needs_review_segregated_from_confirmed_mytax_totals(self):
        """
        Regression test (3): Flagged or low-confidence (needs-review) business expenses
        must be segregated from confirmed myTax claim totals until reviewed/corrected.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # 1. Record a confirmed/high-confidence business receipt ($55.00 AUD, GST=$5.00)
        confirmed = cos.process_note("Fictional 3D Maker Supply nozzle $55.00 gst=5.00 today")
        self.assertEqual(confirmed.status, "recorded")

        # 2. Record a low-confidence / needs-review business receipt ($88.00 AUD, GST=$8.00)
        needs_review_id = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Ambiguous Hardware Merchant",
                "date": "2026-10-20",
                "total_amount": 88.00,
                "tax_amount": 8.00,
                "currency": "AUD",
                "category": "tools_equipment",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "tax_label": "Depreciation / Instant asset write-off",
                "confidence": 0.60,
                "uncertainty_note": "Ambiguous category — confirm tools_equipment vs personal",
            },
            flagged=False,
        )

        # Verify get_mytax_summary includes ONLY the $55.00 confirmed receipt
        confirmed_mytax = self.jm.get_mytax_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(confirmed_mytax), 1)
        self.assertEqual(confirmed_mytax[0]["gross_amount"], 55.0)
        self.assertEqual(confirmed_mytax[0]["total_gst"], 5.0)

        # Verify get_mytax_review_summary holds the segregated $88.00 item
        review_mytax = self.jm.get_mytax_review_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(review_mytax), 1)
        self.assertEqual(review_mytax[0]["gross_amount"], 88.0)
        self.assertEqual(review_mytax[0]["review_state"], "needs_review")

        # Verify dashboard payload segregates total_claimable ($55.00) vs pending_review_claimable ($88.00)
        dash = build_dashboard_payload(self.db_path)
        self.assertEqual(dash["mytax"]["total_claimable"], 55.0)
        self.assertEqual(dash["mytax"]["pending_review_claimable"], 88.0)
        self.assertEqual(dash["mytax"]["needs_review_count"], 1)

        # 3. Once owner confirms the complete classification (category, activity, business_pct), it moves into confirmed claimable totals
        # Category correction alone on a needs-review receipt does NOT confirm unrelated activity/pct fields
        cos.correct_receipt(receipt_id=needs_review_id, category="tools_equipment")
        dash_cat_only = build_dashboard_payload(self.db_path)
        self.assertEqual(dash_cat_only["mytax"]["total_claimable"], 55.0)

        # Confirming activity and pct completes confirmation and moves into confirmed claimable totals
        cos.correct_receipt(
            receipt_id=needs_review_id,
            business_activity="printing_3d",
            business_pct=100.0,
        )
        dash_after = build_dashboard_payload(self.db_path)
        self.assertEqual(dash_after["mytax"]["total_claimable"], 143.0)  # 55.0 + 88.0
        self.assertEqual(dash_after["mytax"]["pending_review_claimable"], 0.0)

    def test_defect_4_unconfirmed_ai_vendor_guess_not_promoted_to_confidence_1(self):
        """
        Regression test (4): Unconfirmed AI/heuristic vendor guesses must NOT be saved
        as confidence 1.0 rules in Vendor Memory. Only explicit owner corrections promote a vendor.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # 1st receipt from Fictional 3D Maker Supplies (classified by heuristic, unconfirmed by owner)
        r1 = cos.process_note("Vendor: [FICTIONAL DEMO] 3D Maker Supply; nozzle $30.00 today")
        self.assertNotEqual(r1.classification_source, "vendor_memory")
        self.assertLess(r1.confidence, 1.0)
        self.assertIsNone(cos.km.memory.lookup(r1.vendor, confirmed_only=True))

        # 2nd receipt from the same vendor BEFORE any owner correction:
        # Must still be an unconfirmed suggestion (NOT confidence 1.0 vendor_memory!)
        r2 = cos.process_note("Vendor: [FICTIONAL DEMO] 3D Maker Supply; build plate $45.00 today")
        self.assertNotEqual(r2.classification_source, "vendor_memory")
        self.assertLess(r2.confidence, 1.0)
        self.assertIn("AI Suggestion", r2.summary_text)

        # Now owner explicitly corrects r2 (confirming vendor rule at 80% business_pct)
        cos.correct_receipt(receipt_id=r2.receipt_id, business_pct=80.0)
        self.assertIsNotNone(cos.km.memory.lookup(r2.vendor, confirmed_only=True))

        # 3rd receipt from the same vendor AFTER owner correction:
        # Now uses confirmed Vendor Memory with confidence 1.0 and 80% business_pct!
        r3 = cos.process_note("Vendor: [FICTIONAL DEMO] 3D Maker Supply; resin $50.00 today")
        self.assertEqual(r3.classification_source, "vendor_memory")
        self.assertEqual(r3.confidence, 1.0)
        self.assertEqual(r3.business_pct, 80.0)
        self.assertEqual(r3.claimable_amount, 40.0)

    def test_defect_5_card_details_and_personal_conditional_controls(self):
        """
        Regression test (5): Receipt card must display Date, Payment, and Items;
        Personal entries must hide Business % and ABN Activity controls in both card text and keyboard.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # Personal entry
        pers = cos.process_note("Fictional Demo Cafe flat white coffee $6.50 today")
        self.assertEqual(pers.claim_stream, "personal")
        self.assertIn("*Date*:", pers.summary_text)
        self.assertIn("*Payment*:", pers.summary_text)
        self.assertIn("*Items*:", pers.summary_text)
        self.assertNotIn("*Business %*:", pers.summary_text)
        self.assertNotIn("*Activity*:", pers.summary_text)

        # Personal keyboard must NOT contain pct or ABN activity buttons
        pers_kb = build_correction_keyboard(pers.receipt_id, claim_stream="personal", expanded=True)
        pers_callbacks = [btn.callback_data for row in pers_kb.inline_keyboard for btn in row]
        self.assertFalse(any(":pct:" in cb for cb in pers_callbacks))
        self.assertFalse(any(":act:" in cb for cb in pers_callbacks))
        self.assertIn(f"corr:{pers.receipt_id}:stream:abn", pers_callbacks)
        self.assertIn(f"corr:{pers.receipt_id}:cat:dining_coffee", pers_callbacks)

        # ABN entry keyboard MUST contain pct and ABN activity buttons
        abn_kb = build_correction_keyboard(pers.receipt_id, claim_stream="abn", expanded=True)
        abn_callbacks = [btn.callback_data for row in abn_kb.inline_keyboard for btn in row]
        self.assertTrue(any(":pct:50" in cb for cb in abn_callbacks))
        self.assertTrue(any(":act:dog_walking" in cb for cb in abn_callbacks))
        self.assert_compact_receipt_keyboard(
            build_correction_keyboard(pers.receipt_id, claim_stream="payg"), pers.receipt_id, "payg"
        )

    def test_defect_6_gdrive_cli_rejects_arbitrary_non_error_output(self):
        """
        Regression test (6): GDriveAdapter must reject arbitrary non-error stdout strings
        (e.g. 'Uploading file...', 'Done', '100%') and only succeed on a verified Drive file ID.
        """
        self.assertIsNone(extract_verified_drive_file_id("Uploading file..."))
        self.assertIsNone(extract_verified_drive_file_id("Done"))
        self.assertIsNone(extract_verified_drive_file_id("Success"))
        self.assertIsNone(extract_verified_drive_file_id(""))
        self.assertEqual(
            extract_verified_drive_file_id("Uploaded receipt.jpg (ID: 1AbCdEfGhIjKlMnOpQrStUvWxYz12345)"),
            "1AbCdEfGhIjKlMnOpQrStUvWxYz12345",
        )

        sample_file = os.path.join(self.tmp_dir.name, "fictional_receipt.jpg")
        with open(sample_file, "wb") as f:
            f.write(b"fictional-image-content")

        gdrive = GDriveAdapter(receipts_folder_id="1ValidFictionalFolderId987654321")
        with patch("subprocess.run") as mock_run:
            # Arbitrary non-error single-line stdout without a verified file ID must fail
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gdrive"], returncode=0, stdout="Upload completed", stderr=""
            )
            file_id = gdrive.upload_receipt(sample_file)
            self.assertIsNone(file_id)
            self.assertEqual(gdrive.last_status, "failed")
            self.assertIn("Unverified gdrive output", gdrive.last_error)

            # Valid `(ID: ...)` output succeeds
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gdrive"],
                returncode=0,
                stdout="File uploaded (ID: 1FictionalVerifiedDriveId999888)",
                stderr="",
            )
            valid_id = gdrive.upload_receipt(sample_file)
            self.assertEqual(valid_id, "1FictionalVerifiedDriveId999888")
            self.assertEqual(gdrive.last_status, "uploaded")

    def test_defect_7_offline_photo_never_invents_amounts_or_posts_journal(self):
        """
        Regression test (7): When multimodal OCR is offline or unavailable for a photo/PDF,
        the system must NEVER invent fictitious amounts or post journal entries.
        """
        raw_bytes = b"[FICTIONAL DEMO] unreadable receipt photo"
        drive = MagicMock()
        drive.upload_receipt_bytes.return_value = "1FictionalArchivedReceipt987"

        cos_offline = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=drive,
            finance_agent=FinanceAgent(api_key=""),  # Offline (no Gemini client)
        )
        res = cos_offline.process_file("fictional_unreadable_photo.jpg", file_bytes=raw_bytes)

        self.assertEqual(res.status, "extraction_unavailable")
        self.assertEqual(res.receipt_id, 0)
        self.assertIsNone(res.total_amount)
        self.assertIsNone(res.date)
        self.assertIn("Finance could not read the receipt.", res.summary_text)
        self.assertIn("No financial record or journal entry was posted", res.summary_text)
        self.assertEqual(res.gdrive_status, "uploaded")
        self.assertEqual(res.gdrive_file_id, "1FictionalArchivedReceipt987")
        self.assertIsNone(res.local_path)
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir.name, "receipts")))

        # Verify ZERO rows were inserted into receipts or journal_transactions
        rec_count = self.conn.execute("SELECT COUNT(*) as c FROM receipts").fetchone()["c"]
        tx_count = self.conn.execute("SELECT COUNT(*) as c FROM journal_transactions").fetchone()["c"]
        self.assertEqual(rec_count, 0)
        self.assertEqual(tx_count, 0)

    def test_photo_route_with_mocked_ocr_and_corrections_recalculation(self):
        """
        Tests photo intake (when OCR returns structured data) + typed-note corrections across
        Category, Business %, Stream (ABN/PAYG/Personal), Activity, and Vendor without duplicate entries.
        """
        raw_bytes = b"[FICTIONAL DEMO] receipt photo"
        drive = MagicMock()
        drive.upload_receipt_bytes.return_value = "1FictionalArchivedReceipt987"

        mock_finance = MagicMock(spec=FinanceAgent)
        mock_finance._normalize_parsed_receipt = FinanceAgent(api_key="")._normalize_parsed_receipt
        mock_finance.parse_document.return_value = FinanceAgent(api_key="")._normalize_parsed_receipt(
            ParsedReceipt(
                vendor="[FICTIONAL DEMO] Sydney 3D Maker Supplies",
                date="2026-10-25",
                total_amount=110.00,
                tax_amount=10.00,
                gst_status="explicit",
                currency="AUD",
                category="materials_3d_printing",
                purpose="business",
                claim_stream="abn",
                business_activity="printing_3d",
                business_pct=100.0,
                tax_label="Cost of sales",
                payment_method="Bank Account (1000)",
                confidence=0.92,
                items=[ReceiptItem(description="Hardened steel nozzle pack", amount=110.00)],
            )
        )

        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=drive,
            finance_agent=mock_finance,
        )
        photo_res = cos.process_file("fictional_ocr_photo.jpg", file_bytes=raw_bytes)
        rec_id = photo_res.receipt_id
        self.assertEqual(photo_res.status, "recorded")
        self.assertEqual(photo_res.total_amount, 110.0)
        self.assertEqual(photo_res.tax_amount, 10.0)
        self.assertEqual(photo_res.gdrive_status, "uploaded")
        self.assertIn("Original saved and verified.", photo_res.summary_text)
        self.assertIsNone(photo_res.local_path)
        self.assertIn(ATO_SUGGESTION_DISCLAIMER, photo_res.summary_text)

        # Correct Business % to 50% and Activity to dog_walking
        corr = cos.correct_receipt(
            receipt_id=rec_id,
            category="dog_walking_supplies",
            claim_stream="abn",
            business_activity="dog_walking",
            business_pct=50.0,
        )
        self.assertEqual(corr.business_pct, 50.0)
        self.assertEqual(corr.claimable_amount, 55.0)
        self.assertEqual(corr.personal_share, 55.0)
        self.assertEqual(corr.tax_amount, 5.0)

        # Verify exactly 1 journal transaction exists (no duplicates)
        tx_count = self.conn.execute(
            "SELECT COUNT(*) as c FROM journal_transactions WHERE reference = ?",
            (f"receipt:{rec_id}",),
        ).fetchone()["c"]
        self.assertEqual(tx_count, 1)

        tb = {r["code"]: r for r in self.jm.get_trial_balance()}
        self.assertEqual(tb["5400"]["total_debit"], 55.0)
        self.assertEqual(tb["5900"]["total_debit"], 55.0)
        self.assertEqual(tb["1000"]["total_credit"], 110.0)

    def test_telegram_handlers_and_callback_flow(self):
        """Tests Telegram bot text & offline photo handlers and callback button correction."""
        bot = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=12345,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot.conn.close)

        parsed_cmd = parse_correction_tokens(
            ["1", "pct=50", "stream=abn", "activity=dog_walking", "category=car_fuel_maintenance", "gst=0"]
        )
        self.assertEqual(parsed_cmd["receipt_id"], 1)
        self.assertEqual(parsed_cmd["business_pct"], 50.0)
        self.assertEqual(parsed_cmd["tax_amount"], 0.0)

        # 1. Incoming Telegram text message (ABN)
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "Fictional 3D Maker Supply build plate $55.00 gst=5.00 today"
        update.message.reply_chat_action = AsyncMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        original_xp = bot._get_companion_state()["total_xp"]
        asyncio.run(bot.handle_text(update, context))
        update.message.reply_text.assert_called_once()
        note_reply = update.message.reply_text.await_args
        sent_text = note_reply.args[0]
        sent_kb = note_reply.kwargs["reply_markup"]
        self.assertIn("55.00", sent_text)
        self.assertIn("AUD", sent_text)
        self.assertEqual(sent_text.splitlines()[-1], "🐾 Womby: +10 XP")
        self.assertIsNone(note_reply.kwargs.get("parse_mode"))
        self.assertEqual(bot._get_companion_state()["total_xp"], original_xp + 10)
        self.assert_compact_receipt_keyboard(sent_kb, 1, "abn")

        # 2. Callback button correction to Personal -> hides % and activity buttons
        cb_update = MagicMock()
        cb_update.effective_chat.id = 12345
        cb_update.callback_query.data = "corr:1:stream:personal"
        cb_update.callback_query.answer = AsyncMock()
        cb_update.callback_query.edit_message_text = AsyncMock()

        asyncio.run(bot.handle_correction_callback(cb_update, context))
        cb_update.callback_query.edit_message_text.assert_called_once()
        edited_reply = cb_update.callback_query.edit_message_text.await_args
        self.assertIsNone(edited_reply.kwargs.get("parse_mode"))
        self.assert_compact_receipt_keyboard(edited_reply.kwargs["reply_markup"], 1, "personal")

        # 3. Drive archive unavailable -> no new record, link or correction buttons
        photo_update = MagicMock()
        photo_update.effective_chat.id = 12345
        photo_update.message.caption = ""
        photo_update.message.reply_chat_action = AsyncMock()
        photo_update.message.reply_text = AsyncMock()

        photo_obj = MagicMock()
        photo_obj.file_id = "fictional_photo_id"
        photo_obj.file_unique_id = "fictional_unique_photo_1"
        photo_obj.file_size = None
        photo_update.message.photo = [photo_obj]

        tg_file_mock = SimpleNamespace(
            file_size=None,
            download_as_bytearray=AsyncMock(return_value=bytearray(b"[FICTIONAL DEMO] Telegram photo")),
        )
        context.bot.get_file = AsyncMock(return_value=tg_file_mock)

        with patch.object(bot.gdrive, "upload_receipt_bytes", return_value=None):
            asyncio.run(bot.handle_photo(photo_update, context))

        photo_update.message.reply_text.assert_awaited_once()
        progress_edit = photo_update.message.reply_text.return_value.edit_text
        progress_edit.assert_awaited_once()
        photo_reply_text = progress_edit.call_args[0][0]
        self.assertIn("Drive unavailable · Not posted", photo_reply_text)
        self.assertIn("Keep your original", photo_reply_text)
        self.assertIsNone(progress_edit.call_args.kwargs["reply_markup"])
        self.assertEqual(bot._get_companion_state()["total_xp"], original_xp + 10)
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir.name, "receipts")))

    def test_terse_telegram_note_uses_sydney_capture_day_and_one_companion_line(self):
        """[FICTIONAL DEMO] A short expense uses the Sydney day the owner sent it."""
        bot = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=12345,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot.conn.close)
        bot.conn.execute(
            "UPDATE companion_state SET reward_target_xp=10, reward_name=? WHERE id=1",
            ("[FICTIONAL DEMO] Tea break",),
        )
        bot.conn.commit()
        sydney_day = datetime.date.today() - datetime.timedelta(days=1)
        utc_sent = datetime.datetime.combine(
            sydney_day - datetime.timedelta(days=1), datetime.time(14, 5), tzinfo=datetime.timezone.utc,
        )
        message = SimpleNamespace(
            text="coffe 6.50", date=utc_sent,
            reply_chat_action=AsyncMock(), reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=12345))
        # Use a fixed Sydney summer offset in the Windows test environment.
        sydney_summer = datetime.timezone(datetime.timedelta(hours=11))
        with patch("src.interfaces.telegram_bot.ZoneInfo", return_value=sydney_summer) as timezone_lookup:
            asyncio.run(bot.handle_text(update, SimpleNamespace()))

        timezone_lookup.assert_called_once_with("Australia/Sydney")
        receipt = bot.cos.km.get_receipt(1)
        self.assertEqual(receipt["date"], sydney_day.isoformat())
        self.assertEqual(receipt["total_amount"], 6.50)
        self.assertEqual(receipt["category"], "dining_coffee")
        reply = message.reply_text.await_args
        self.assertIsNone(reply.kwargs.get("parse_mode"))
        self.assertTrue(reply.args[0].startswith("✍️ #1 · coffe · "))
        self.assertNotIn("coffe 6.50", reply.args[0])
        self.assertEqual(reply.args[0].splitlines()[-1], "🐾 Womby: +10 XP · Treat unlocked: [FICTIONAL DEMO] Tea break")
        self.assertEqual(bot._get_companion_state()["total_xp"], 10)

    def test_exact_upload_and_possible_duplicate_have_distinct_telegram_controls(self):
        """[FICTIONAL DEMO] Same bytes show the original; a soft match can be reviewed."""
        bot = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=12345,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot.conn.close)
        initial_file = b"[FICTIONAL DEMO] first receipt"
        separate_file = b"[FICTIONAL DEMO] separate receipt"
        original_drive_id = "1FictionalOriginalReceipt123"
        parsed = ParsedReceipt(
            vendor="[FICTIONAL DEMO] Zinc Shop",
            date=(datetime.date.today() - datetime.timedelta(days=1)).isoformat(),
            total_amount=23.50,
            tax_amount=0.0,
            gst_status="explicit",
            category="materials_3d_printing",
            confidence=0.95,
        )

        def extract(filename, *, file_bytes, **kwargs):
            return parsed.model_copy(update={"tax_amount": 8.0 if file_bytes == separate_file else 0.0})

        async def upload(payload, caption=None):
            progress = SimpleNamespace(edit_text=AsyncMock())
            message = SimpleNamespace(
                photo=[SimpleNamespace(file_id="fictional_id", file_unique_id="fictional_file", file_size=len(payload))],
                caption=caption,
                reply_text=AsyncMock(return_value=progress),
            )
            file = SimpleNamespace(file_size=len(payload), download_as_bytearray=AsyncMock(return_value=bytearray(payload)))
            context = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock(return_value=file)))
            await bot.handle_photo(SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=12345)), context)
            return progress.edit_text.await_args

        async def tap(data):
            query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
            await bot.handle_correction_callback(
                SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=12345)), SimpleNamespace(),
            )
            return query.edit_message_text.await_args

        with (
            patch.object(bot.cos.finance, "parse_document", side_effect=extract) as ocr,
            patch.object(bot.gdrive, "upload_receipt_bytes", side_effect=[original_drive_id, "1FictionalSecondReceipt321"]) as archive,
        ):
            first = asyncio.run(upload(initial_file))
            self.assert_compact_receipt_keyboard(first.kwargs["reply_markup"], 1, "abn")
            self.assertIn("Womby: +10 XP", first.args[0])

            for caption in (None, "[FICTIONAL DEMO] batch"):
                exact = asyncio.run(upload(initial_file, caption))
                self.assertIn("Already saved as receipt #1.", exact.args[0])
                self.assertIn("$23.50 AUD", exact.args[0])
                self.assertNotIn("Womby:", exact.args[0])
                [read_only] = [button for row in exact.kwargs["reply_markup"].inline_keyboard for button in row]
                details = asyncio.run(tap(read_only.callback_data))
                self.assertIn("Already saved as receipt #1.", details.kwargs["text"])
                buttons = [button for row in details.kwargs["reply_markup"].inline_keyboard for button in row]
                self.assertIn(f"https://drive.google.com/open?id={original_drive_id}", {b.url for b in buttons})
                self.assertFalse(any(b.callback_data and ":distinct:" in b.callback_data for b in buttons))
            ocr.assert_called_once()
            archive.assert_called_once()
            self.assertEqual(bot._get_companion_state()["total_xp"], 10)
            self.assertEqual(bot.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)

            possible = asyncio.run(upload(separate_file))
            self.assertIn("Possible duplicate of #1 · Excluded from totals", possible.args[0])
            self.assertIn("Womby: +10 XP", possible.args[0])
            buttons = [button for row in possible.kwargs["reply_markup"].inline_keyboard for button in row]
            distinct = next(button for button in buttons if button.text == "↩️ Not a duplicate, count it")
            self.assertEqual(distinct.callback_data, "corr:2:distinct:1")

            corrected = asyncio.run(tap(distinct.callback_data))
            self.assertIn("Needs review · Not posted", corrected.kwargs["text"])
            self.assertNotIn("Womby:", corrected.kwargs["text"])
            self.assert_compact_receipt_keyboard(corrected.kwargs["reply_markup"], 2, "abn")
            remaining = bot.cos.km.get_receipt(2)["flag_reason"]
            self.assertIn("GST", remaining)
            self.assertNotIn("duplicate", remaining.lower())
            self.assertEqual(bot.conn.execute("SELECT COUNT(*) FROM journal_transactions").fetchone()[0], 1)
            self.assertEqual(bot._get_companion_state()["total_xp"], 20)

    def test_category_controls_keep_archive_private_and_preserve_legacy_corrections(self):
        bot = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=12345,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot.conn.close)
        drive_id = "1FictionalArchivedReceipt987"
        receipt_id = bot.cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Project_Zinc* Supplies",
                "date": "2026-11-03",
                "total_amount": 23.50,
                "currency": "AUD",
                "category": "materials_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "recorded",
            },
            gdrive_file_id=drive_id,
            gdrive_status="uploaded",
        )
        prefix = f"corr:{receipt_id}"

        async def tap(suffix, chat_id=12345):
            query = MagicMock()
            query.data = f"{prefix}:{suffix}"
            query.answer = AsyncMock()
            query.edit_message_reply_markup = AsyncMock()
            query.edit_message_text = AsyncMock()
            update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=chat_id))
            await bot.handle_correction_callback(update, SimpleNamespace())
            return query

        with patch.object(bot.cos, "correct_receipt", wraps=bot.cos.correct_receipt) as correct:
            denied = asyncio.run(tap("menu:controls", chat_id=54321))
            denied.edit_message_reply_markup.assert_not_called()
            denied.edit_message_text.assert_not_called()

            controls = asyncio.run(tap("menu:controls"))
            expanded = controls.edit_message_reply_markup.await_args.kwargs["reply_markup"]
            control_buttons = [button for row in expanded.inline_keyboard for button in row]
            control_actions = {button.callback_data for button in control_buttons}
            self.assertIn(f"{prefix}:pct:80", control_actions)
            self.assertIn(f"{prefix}:act:printing_3d", control_actions)
            self.assertIn(f"{prefix}:menu:cats", control_actions)
            self.assertIn(f"{prefix}:menu:main", control_actions)
            self.assertIn(f"https://drive.google.com/open?id={drive_id}", {b.url for b in control_buttons})

            unverified_row = {**bot.cos.km.get_receipt(receipt_id), "gdrive_status": "failed"}
            with patch.object(bot.cos.km, "get_receipt", return_value=unverified_row):
                unverified = asyncio.run(tap("menu:controls"))
            unverified_keyboard = unverified.edit_message_reply_markup.await_args.kwargs["reply_markup"]
            self.assertFalse(any(button.url for row in unverified_keyboard.inline_keyboard for button in row))

            cats = asyncio.run(tap("menu:cats"))
            categories = cats.edit_message_reply_markup.await_args.kwargs["reply_markup"]
            category_actions = {button.callback_data for row in categories.inline_keyboard for button in row}
            self.assertIn(f"{prefix}:cat:groceries", category_actions)
            self.assertIn(f"{prefix}:menu:controls", category_actions)

            back = asyncio.run(tap("menu:main"))
            back_edit = back.edit_message_text if back.edit_message_text.await_count else back.edit_message_reply_markup
            self.assert_compact_receipt_keyboard(back_edit.await_args.kwargs["reply_markup"], receipt_id, "abn")
            correct.assert_not_called()

            legacy = asyncio.run(tap("pct:80"))
            correct.assert_called_once_with(receipt_id=receipt_id, business_pct=80.0)
            mutation = legacy.edit_message_text.await_args
            self.assertIsNone(mutation.kwargs.get("parse_mode"))
            self.assert_compact_receipt_keyboard(mutation.kwargs["reply_markup"], receipt_id, "abn")
            self.assertEqual(bot.cos.km.get_receipt(receipt_id)["business_pct"], 80.0)

    def test_media_intake_downloads_bytes_and_forwards_the_original_caption(self):
        bot = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=12345,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot.conn.close)
        raw_bytes = b"[FICTIONAL DEMO] Telegram attachment bytes"
        drive_id = "1FictionalArchivedReceipt987"
        uploads = (
            (None, "[FICTIONAL DEMO] For Project Zinc", False, True),
            (None, "[FICTIONAL DEMO] BaTcH for Project Zinc", True, False),
            (r"..\outside\Invoice.PDF", "[FICTIONAL DEMO] Paid With a Voucher", False, True),
            (r"../outside/Export.CSV", "[FICTIONAL DEMO] November Accounts", True, False),
            (r"../outside/Blurry.JPG", "[FICTIONAL DEMO] Date is unclear", False, False),
        )
        for filename, caption, batch, saved in uploads:
            with self.subTest(filename=filename, caption=caption):
                media = SimpleNamespace(
                    file_name=filename, file_unique_id="fictional_file", file_id="fictional_id",
                    file_size=len(raw_bytes),
                )
                progress = SimpleNamespace(edit_text=AsyncMock())
                message = SimpleNamespace(
                    photo=[media],
                    document=media,
                    caption=caption,
                    reply_text=AsyncMock(return_value=progress),
                )
                update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=12345))
                async def download():
                    message.reply_text.assert_awaited_once()
                    return bytearray(raw_bytes)

                file = SimpleNamespace(file_size=len(raw_bytes), download_as_bytearray=AsyncMock(side_effect=download))
                context = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock(return_value=file)))
                summary = IntakeSummary(
                    receipt_id=42 if saved else 0,
                    vendor="[FICTIONAL DEMO] Zinc_* Shop",
                    date="2026-11-03",
                    total_amount=23.50,
                    currency="AUD",
                    purpose="business",
                    category="materials_3d_printing",
                    tax_label="Cost of sales",
                    claim_stream="abn",
                    summary_text="Detailed report" if saved else "Please confirm the receipt date.",
                    status="recorded" if saved else "needs_review",
                    flag_reason=None if saved else "Transaction date is missing.",
                    gdrive_status="uploaded",
                    gdrive_file_id=drive_id,
                    items_summary="Nozzle kit",
                )
                with (
                    patch.object(bot.cos, "process_file", return_value=summary) as process_single,
                    patch.object(bot.cos, "process_batch_or_statement", return_value={
                        "recorded_count": 0, "xp_earned": 0, "summary_text": "No new records",
                        "gdrive_status": "uploaded", "gdrive_file_id": drive_id,
                    }) as process_batch,
                    patch.object(bot, "_award_xp_and_emit", return_value="🐾 Womby: +10 XP") as award_xp,
                ):
                    handler = bot.handle_photo if filename is None else bot.handle_document
                    asyncio.run(handler(update, context))
                    context.bot.get_file.assert_awaited_once_with("fictional_id")
                    file.download_as_bytearray.assert_awaited_once_with()
                    message.reply_text.assert_awaited_once()
                    progress.edit_text.assert_awaited_once()
                    self.assertFalse(os.path.exists(os.path.join(self.tmp_dir.name, "receipts")))
                    expected_name = "fictional_file.jpg" if filename is None else (
                        "fictional_file_" + filename.replace("\\", "/").rsplit("/", 1)[-1]
                    )
                    reply = progress.edit_text.await_args
                    keyboard = reply.kwargs["reply_markup"]
                    if saved:
                        self.assert_compact_receipt_keyboard(keyboard, 42, "abn")
                        self.assertIn(summary.vendor, reply.args[0])
                        self.assertIn("$23.50 AUD", reply.args[0])
                        self.assertNotIn("Detailed report", reply.args[0])
                        self.assertEqual(reply.args[0].splitlines()[-1], "🐾 Womby: +10 XP")
                        self.assertIsNone(reply.kwargs.get("parse_mode"))
                        award_xp.assert_called_once()
                    else:
                        self.assertEqual([len(row) for row in keyboard.inline_keyboard], [1])
                        self.assertEqual(
                            keyboard.inline_keyboard[0][0].url, f"https://drive.google.com/open?id={drive_id}"
                        )
                        self.assertNotIn("Womby:", reply.args[0])
                        award_xp.assert_not_called()
                    if batch:
                        process_batch.assert_called_once_with(expected_name, user_note=caption, file_bytes=raw_bytes)
                        process_single.assert_not_called()
                    else:
                        process_single.assert_called_once_with(expected_name, user_note=caption, file_bytes=raw_bytes)
                        process_batch.assert_not_called()
                        if not saved:
                            self.assertEqual(reply.args[0].splitlines(), [
                                "Status: Needs review · Not posted", "Check date.", "Original saved to Drive.",
                            ])

    def test_media_result_only_retries_a_markdown_parse_rejection(self):
        send = AsyncMock(side_effect=[BadRequest("Can't parse entities: Can't find end of the entity"), None])
        keyboard = build_correction_keyboard(42, claim_stream="abn")
        asyncio.run(TelegramReceiptBot._send_intake_result(send, "[FICTIONAL DEMO] Supplier*", keyboard))
        self.assertEqual(send.await_count, 2)
        self.assertEqual(send.await_args_list[0].kwargs["parse_mode"], "Markdown")
        self.assertIsNone(send.await_args_list[1].kwargs["parse_mode"])
        self.assertIs(send.await_args_list[1].kwargs["reply_markup"], keyboard)

        for error in (NetworkError("Request timed out"), BadRequest("Message is not modified")):
            with self.subTest(error=error):
                send = AsyncMock(side_effect=error)
                with self.assertRaises(type(error)):
                    asyncio.run(TelegramReceiptBot._send_intake_result(send, "[FICTIONAL DEMO] Receipt"))
                send.assert_awaited_once()

    def test_media_intake_rejects_oversized_and_empty_uploads_before_processing(self):
        bot = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=12345,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot.conn.close)
        oversized = bytearray(b"[FICTIONAL DEMO]" * 3)
        cases = (
            (len(oversized), None, oversized, 0, 0, "too large"),
            (None, len(oversized), oversized, 1, 0, "too large"),
            (None, None, oversized, 1, 1, "too large"),
            (None, None, bytearray(), 1, 1, "arrived empty"),
        )
        for media_size, file_size, contents, fetches, downloads, expected in cases:
            with self.subTest(media_size=media_size, file_size=file_size, expected=expected):
                media = SimpleNamespace(file_id="fictional_id", file_unique_id="fictional_file", file_size=media_size)
                progress = SimpleNamespace(edit_text=AsyncMock())
                message = SimpleNamespace(photo=[media], caption=None, reply_text=AsyncMock(return_value=progress))
                update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=12345))
                file = SimpleNamespace(file_size=file_size, download_as_bytearray=AsyncMock(return_value=contents))
                context = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock(return_value=file)))
                with (
                    patch("src.interfaces.telegram_bot.MAX_TELEGRAM_RECEIPT_BYTES", 32),
                    patch.object(bot.cos, "process_file") as process,
                ):
                    asyncio.run(bot.handle_photo(update, context))
                self.assertEqual(context.bot.get_file.await_count, fetches)
                self.assertEqual(file.download_as_bytearray.await_count, downloads)
                process.assert_not_called()
                reply = progress.edit_text if fetches else message.reply_text
                self.assertIn(expected, reply.await_args.args[0])

    def test_defect_8_vendor_only_rename_preserves_qa_flags_and_field_uncertainty(self):
        """
        Regression test (8 / Turn 53 QA #1):
        Vendor-only rename must NOT clear unrelated invoice/date QA flags, must NOT set
        confidence to 100% or classification_source to 'owner_correction', and must NOT
        include the flagged/uncertain $110 receipt in confirmed myTax reports.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # 1. Record a $110.00 ABN receipt with an invoice/date QA flag and low classification confidence (0.60)
        flagged_id = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Unclear 3D Vendor",
                "date": "2026-10-20",
                "total_amount": 110.00,
                "tax_amount": 10.00,
                "currency": "AUD",
                "category": "materials_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "tax_label": "Cost of sales",
                "confidence": 0.60,
                "classification_source": "ai_suggestion",
                "uncertainty_note": "Uncertain category and missing tax invoice ABN",
            },
            flagged=True,
            flag_reason="Missing valid tax invoice; Date on invoice illegible",
        )

        # Perform a vendor-only rename
        renamed = cos.correct_receipt(
            receipt_id=flagged_id,
            vendor="[FICTIONAL DEMO] Renamed 3D Vendor",
        )

        # Vendor is updated, but unresolved invoice/date QA flags & field uncertainty are preserved
        self.assertEqual(renamed.vendor, "[FICTIONAL DEMO] Renamed 3D Vendor")
        self.assertEqual(renamed.status, "flagged")
        self.assertIn("Missing valid tax invoice", renamed.flag_reason or "")
        self.assertIn("Date on invoice illegible", renamed.flag_reason or "")
        self.assertEqual(renamed.confidence, 0.60)
        self.assertNotEqual(renamed.confidence, 1.0)
        self.assertEqual(renamed.classification_source, "ai_suggestion")
        self.assertEqual(renamed.uncertainty_note, "Uncertain category and missing tax invoice ABN")
        self.assertIsNone(cos.km.memory.lookup("[FICTIONAL DEMO] Renamed 3D Vendor", confirmed_only=True))

        # Confirmed myTax report and dashboard must NOT include the $110.00 flagged receipt
        self.assertEqual(len(self.jm.get_mytax_summary("2026-01-01", "2027-12-31")), 0)
        review_rows = self.jm.get_mytax_review_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(review_rows), 1)
        self.assertEqual(review_rows[0]["gross_amount"], 110.0)
        dash = build_dashboard_payload(self.db_path)
        self.assertEqual(dash["mytax"]["total_claimable"], 0.0)
        self.assertEqual(dash["mytax"]["pending_review_claimable"], 110.0)

        # 2. Test a receipt with a future-date QA flag ("2099-01-01"):
        # Vendor rename or category edit does NOT clear the future-date flag; only fixing the date resolves it.
        future_id = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Future Date Supplier",
                "date": "2099-01-01",
                "total_amount": 110.00,
                "tax_amount": 10.00,
                "currency": "AUD",
                "category": "materials_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "tax_label": "Cost of sales",
                "confidence": 0.90,
            }
        )
        rec_future = cos.km.get_receipt(future_id)
        self.assertEqual(rec_future["status"], "flagged")
        self.assertIn("in the future", rec_future["flag_reason"] or "")

        # Vendor-only rename keeps the future-date flag active and out of confirmed myTax
        after_vendor = cos.correct_receipt(receipt_id=future_id, vendor="[FICTIONAL DEMO] Future Supplier Renamed")
        self.assertEqual(after_vendor.status, "flagged")
        self.assertIn("in the future", after_vendor.flag_reason or "")
        self.assertLess(after_vendor.confidence, 1.0)
        self.assertEqual(len(self.jm.get_mytax_summary("2026-01-01", "2100-12-31")), 0)

        # Category correction alone ALSO keeps the future-date flag active until date is fixed
        after_cat = cos.correct_receipt(receipt_id=future_id, category="tools_equipment")
        self.assertEqual(after_cat.status, "flagged")
        self.assertIn("in the future", after_cat.flag_reason or "")
        self.assertLess(after_cat.confidence, 1.0)
        self.assertEqual(len(self.jm.get_mytax_summary("2026-01-01", "2100-12-31")), 0)

        # Fixing the date to a valid date ("2026-10-15") resolves the date flag and allows confirmed reporting
        after_date_fix = cos.correct_receipt(receipt_id=future_id, date="2026-10-15")
        self.assertEqual(after_date_fix.status, "corrected")
        self.assertIsNone(after_date_fix.flag_reason)
        confirmed_after = self.jm.get_mytax_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(confirmed_after), 1)
        self.assertEqual(confirmed_after[0]["gross_amount"], 110.0)

    def test_defect_9_invalid_gst_correction_rejected_without_corrupting_record_or_reports(self):
        """
        Regression test (9 / Turn 53 QA #2):
        Explicit GST correction of $500 (or negative GST, or GST > 10% max share) on a $110 receipt
        must be rejected with ValueError before mutating the database, journal, or confirmed myTax reports
        (never producing net -$390 and GST $500).
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # 1. Record a valid $110.00 AUD ABN receipt with explicit GST = $10.00
        valid_summary = cos.process_note("Fictional 3D Maker Supply nozzle $110.00 gst=10.00 today")
        self.assertEqual(valid_summary.status, "recorded")
        self.assertEqual(valid_summary.total_amount, 110.0)
        self.assertEqual(valid_summary.tax_amount, 10.0)

        # Verify initial confirmed myTax report: gross=$110.00, net=$100.00, gst=$10.00
        mytax_before = self.jm.get_mytax_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(mytax_before), 1)
        self.assertEqual(mytax_before[0]["gross_amount"], 110.0)
        self.assertEqual(mytax_before[0]["net_amount"], 100.0)
        self.assertEqual(mytax_before[0]["total_gst"], 10.0)

        # 2. Attempt explicit GST = $500.00 on $110.00 receipt -> must fail safely with ValueError
        with self.assertRaises(ValueError) as ctx_500:
            cos.correct_receipt(receipt_id=valid_summary.receipt_id, tax_amount=500.0)
        self.assertIn("cannot exceed receipt total", str(ctx_500.exception))

        # 3. Attempt negative GST = -$5.00 -> must fail safely with ValueError
        with self.assertRaises(ValueError) as ctx_neg:
            cos.correct_receipt(receipt_id=valid_summary.receipt_id, tax_amount=-5.0)
        self.assertIn("cannot be negative", str(ctx_neg.exception))

        # 4. Attempt GST = $25.00 on $110.00 (exceeds 10% max share ~$10.10) -> must fail safely with ValueError
        with self.assertRaises(ValueError) as ctx_25:
            cos.correct_receipt(receipt_id=valid_summary.receipt_id, tax_amount=25.0)
        self.assertIn("exceeds expected maximum 10% share", str(ctx_25.exception))

        # 5. Verify prior valid record, journal balance, and confirmed myTax report were NOT corrupted
        rec_after = cos.km.get_receipt(valid_summary.receipt_id)
        self.assertEqual(rec_after["status"], "recorded")
        self.assertEqual(rec_after["total_amount"], 110.0)
        self.assertEqual(rec_after["tax_amount"], 10.0)
        self.assertEqual(rec_after["raw_tax_amount"], 10.0)

        mytax_after = self.jm.get_mytax_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(mytax_after), 1)
        self.assertEqual(mytax_after[0]["gross_amount"], 110.0)
        self.assertEqual(mytax_after[0]["net_amount"], 100.0)
        self.assertEqual(mytax_after[0]["total_gst"], 10.0)

        tb = self.jm.get_trial_balance()
        self.assertEqual(round(sum(r["total_debit"] for r in tb), 2), 110.0)
        self.assertEqual(round(sum(r["total_credit"] for r in tb), 2), 110.0)

    def test_defect_10_payg_to_abn_recomputes_compatible_category_and_tax_label(self):
        """
        Regression test (10 / Turn 54 QA #1):
        Changing a PAYG receipt (work_home_office_other, D5 tax label) to ABN must
        recompute a compatible ABN category, activity, and ABN tax label instead of
        retaining the stale employee 'D5 Other work-related expenses' label.
        Also tests ABN -> PAYG recomputation and rejection of incompatible explicit pairs.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # 1. Record a PAYG work_home_office_other receipt ($110.00 AUD, GST=$10.00)
        payg_id = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Sydney Work Supplies",
                "date": "2026-10-20",
                "total_amount": 110.00,
                "tax_amount": 10.00,
                "currency": "AUD",
                "category": "work_home_office_other",
                "purpose": "business",
                "claim_stream": "payg",
                "business_activity": "payg_work",
                "business_pct": 100.0,
                "tax_label": "D5 Other work-related expenses",
                "confidence": 0.90,
            }
        )
        rec_initial = cos.km.get_receipt(payg_id)
        self.assertEqual(rec_initial["claim_stream"], "payg")
        self.assertEqual(rec_initial["category"], "work_home_office_other")
        self.assertEqual(rec_initial["tax_label"], "D5 Other work-related expenses")

        # 2. Change stream from PAYG to ABN without activity/pct:
        # Recomputes compatible category, removes stale employee D5, sets activity to 'none' and pct to 0.0,
        # retains needs_review status, and excludes from confirmed myTax totals until activity & pct are chosen.
        to_abn = cos.correct_receipt(receipt_id=payg_id, claim_stream="abn")
        self.assertEqual(to_abn.claim_stream, "abn")
        self.assertEqual(to_abn.business_activity, "none")
        self.assertEqual(to_abn.business_pct, 0.0)
        self.assertEqual(to_abn.category, "office_supplies")
        self.assertEqual(to_abn.tax_label, "Other business expenses")
        self.assertNotIn("D5", to_abn.tax_label or "")
        self.assertEqual(to_abn.status, "needs_review")
        self.assertIsNone(cos.km.memory.lookup(to_abn.vendor, confirmed_only=True))

        mytax_abn_unconfirmed = self.jm.get_mytax_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(mytax_abn_unconfirmed), 0)

        # 3. Explicitly selecting ABN activity and business_pct confirms the record, promotes to myTax, and learns vendor rule
        confirmed_abn = cos.correct_receipt(
            receipt_id=payg_id,
            business_activity="printing_3d",
            business_pct=100.0,
        )
        self.assertEqual(confirmed_abn.claim_stream, "abn")
        self.assertEqual(confirmed_abn.business_activity, "printing_3d")
        self.assertEqual(confirmed_abn.business_pct, 100.0)
        self.assertEqual(confirmed_abn.status, "corrected")
        self.assertIsNotNone(cos.km.memory.lookup(confirmed_abn.vendor, confirmed_only=True))

        mytax_abn = self.jm.get_mytax_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(mytax_abn), 1)
        self.assertEqual(mytax_abn[0]["claim_stream"], "abn")
        self.assertEqual(mytax_abn[0]["tax_label"], "Other business expenses")

        # 4. Change ABN receipt back to PAYG -> must recompute PAYG category & D5 tax label (NOT ABN label!)
        to_payg = cos.correct_receipt(receipt_id=payg_id, claim_stream="payg")
        self.assertEqual(to_payg.claim_stream, "payg")
        self.assertEqual(to_payg.business_activity, "payg_work")
        self.assertEqual(to_payg.category, "work_home_office_other")
        self.assertEqual(to_payg.tax_label, "D5 Other work-related expenses")

        # 4. Explicitly passing incompatible stream + category/activity/tax_label in one call must fail safely
        with self.assertRaises(ValueError):
            cos.correct_receipt(receipt_id=payg_id, claim_stream="abn", tax_label="D5 Other work-related expenses")
        with self.assertRaises(ValueError):
            cos.correct_receipt(receipt_id=payg_id, claim_stream="abn", category="work_home_office_other")
        with self.assertRaises(ValueError):
            cos.correct_receipt(receipt_id=payg_id, claim_stream="payg", business_activity="dog_walking")

    def test_defect_11_rejects_nan_infinities_out_of_range_and_unsupported_categories_before_writes(self):
        """
        Regression test (11 / Turn 54 QA #2):
        Non-finite numbers (NaN / +/-inf) for business_pct or tax_amount, out-of-range
        values, and unsupported categories must be rejected with ValueError BEFORE any DB write,
        never silently turning NaN into 100%, never accepting unsupported categories, and never
        reaching SQLite or corrupting existing records/journals/memory/reports.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # 1. Record a valid $110.00 AUD ABN receipt at 80% business_pct (claimable=$88.00, GST=$8.00)
        valid = cos.process_note("Fictional 3D Maker Supply nozzle $110.00 80% gst=10.00 today")
        rec_id = valid.receipt_id
        self.assertEqual(valid.business_pct, 80.0)
        self.assertEqual(valid.claimable_amount, 88.0)
        self.assertEqual(valid.tax_amount, 8.0)

        # 2. NaN and +/-inf business_pct must raise ValueError (never silently become 100%)
        for bad_pct in (float("nan"), float("inf"), float("-inf"), -10.0, 150.0, True):
            with self.assertRaises(ValueError):
                cos.correct_receipt(receipt_id=rec_id, business_pct=bad_pct)

        # 3. NaN and +/-inf tax_amount (GST) must raise ValueError BEFORE SQLite (no IntegrityError)
        for bad_gst in (float("nan"), float("inf"), float("-inf"), True):
            with self.assertRaises(ValueError):
                cos.correct_receipt(receipt_id=rec_id, tax_amount=bad_gst)

        # 4. Unsupported categories must raise ValueError and never be accepted or saved to Vendor Memory
        for bad_cat in ("unsupported_category_xyz", "filament_inventory", ""):
            with self.assertRaises(ValueError):
                cos.correct_receipt(receipt_id=rec_id, category=bad_cat)

        # 5. Route consistency: Telegram /correct token parser & direct record_receipt also reject invalid inputs
        for bad_tokens in (
            ["1", "pct=nan"],
            ["1", "pct=inf"],
            ["1", "pct=120"],
            ["1", "gst=nan"],
            ["1", "gst=inf"],
            ["1", "category=unsupported_cat"],
            ["1", "unsupported_shorthand"],
        ):
            with self.assertRaises(ValueError):
                parse_correction_tokens(bad_tokens)

        with self.assertRaises(ValueError):
            cos.km.record_receipt(
                data={
                    "vendor": "[FICTIONAL DEMO] Bad Record Test",
                    "date": "2026-10-20",
                    "total_amount": 110.0,
                    "tax_amount": float("nan"),
                    "currency": "AUD",
                    "category": "materials_3d_printing",
                }
            )
        with self.assertRaises(ValueError):
            cos.km.record_receipt(
                data={
                    "vendor": "[FICTIONAL DEMO] Bad Category Test",
                    "date": "2026-10-20",
                    "total_amount": 110.0,
                    "currency": "AUD",
                    "category": "unsupported_category_xyz",
                }
            )

        # 6. Verify prior valid receipt, journal, vendor memory, and myTax report remain 100% intact
        rec_after = cos.km.get_receipt(rec_id)
        self.assertEqual(rec_after["category"], "materials_3d_printing")
        self.assertEqual(rec_after["business_pct"], 80.0)
        self.assertEqual(rec_after["claimable_amount"], 88.0)
        self.assertEqual(rec_after["tax_amount"], 8.0)
        self.assertEqual(rec_after["raw_tax_amount"], 10.0)
        self.assertIsNone(cos.km.memory.lookup(rec_after["vendor"], confirmed_only=True))

        mytax_rows = self.jm.get_mytax_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(mytax_rows), 1)
        self.assertEqual(mytax_rows[0]["gross_amount"], 88.0)
        self.assertEqual(mytax_rows[0]["total_gst"], 8.0)
        self.assertEqual(mytax_rows[0]["net_amount"], 80.0)

    def test_defect_12_allocation_preservation_and_no_fabricated_defaults_on_corrections(self):
        """
        Regression test (12 / Turn 54 QA Allocation Correctness):
        1. PAYG -> ABN switch must NOT fabricate 'printing_3d' and '100%' business, must NOT
           confirm the record, and must NOT save vendor learning until user explicitly chooses activity & pct.
        2. Category-only correction on a known dog_walking receipt with custom percentage (e.g. 60%)
           must PRESERVE 'dog_walking' activity and 60% business percentage across compatible category changes
           (e.g. office_supplies, tools_equipment), never silently flipping to printing_3d or 100%.
        3. Category-only correction alone on a receipt with unconfirmed or missing activity/percentage
           must NOT confirm unrelated allocation fields, must NOT save to vendor_memory, and must NOT
           enter confirmed myTax deductions until resolved.
        4. Incompatible category change (e.g. dog_walking -> materials_3d_printing without specifying activity)
           must NOT silently flip activity to printing_3d; it must flag the conflict and stay in needs_review.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # =========================================================================
        # Case 1: PAYG -> ABN without user-selected activity/pct stays in needs_review
        # =========================================================================
        payg_id = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Sydney Work Stationery",
                "date": "2026-10-18",
                "total_amount": 100.00,
                "tax_amount": 0.0,
                "currency": "AUD",
                "category": "work_home_office_other",
                "purpose": "business",
                "claim_stream": "payg",
                "business_activity": "payg_work",
                "business_pct": 100.0,
                "tax_label": "D5 Other work-related expenses",
                "confidence": 0.90,
            }
        )

        # User switches stream to ABN, but has not yet chosen activity or percentage
        switched = cos.correct_receipt(receipt_id=payg_id, claim_stream="abn")
        self.assertEqual(switched.claim_stream, "abn")
        self.assertEqual(switched.business_activity, "none")
        self.assertEqual(switched.business_pct, 0.0)
        self.assertEqual(switched.status, "needs_review")
        self.assertLess(switched.confidence, 0.80)
        self.assertIn("Missing ABN business_activity", switched.flag_reason or "")
        self.assertIn("Missing ABN business_pct", switched.flag_reason or "")
        self.assertIsNone(cos.km.memory.lookup(switched.vendor, confirmed_only=True))

        # Must be segregated from confirmed myTax totals ($0.00 confirmed)
        mytax_conf = self.jm.get_mytax_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(mytax_conf), 0)

        # User selects activity 'dog_walking', but business_pct is still missing (0.0)
        act_only = cos.correct_receipt(receipt_id=payg_id, business_activity="dog_walking")
        self.assertEqual(act_only.business_activity, "dog_walking")
        self.assertEqual(act_only.business_pct, 0.0)
        self.assertEqual(act_only.status, "needs_review")
        self.assertIn("Missing ABN business_pct", act_only.flag_reason or "")
        self.assertIsNone(cos.km.memory.lookup(act_only.vendor, confirmed_only=True))
        self.assertEqual(len(self.jm.get_mytax_summary("2026-01-01", "2027-12-31")), 0)

        # Now user provides business_pct = 75.0% -> record is fully confirmed and learned
        resolved_abn = cos.correct_receipt(receipt_id=payg_id, business_pct=75.0)
        self.assertEqual(resolved_abn.business_activity, "dog_walking")
        self.assertEqual(resolved_abn.business_pct, 75.0)
        self.assertEqual(resolved_abn.claimable_amount, 75.0)
        self.assertEqual(resolved_abn.status, "corrected")
        self.assertEqual(resolved_abn.confidence, 1.0)
        self.assertIsNotNone(cos.km.memory.lookup(resolved_abn.vendor, confirmed_only=True))

        mytax_now = self.jm.get_mytax_summary("2026-01-01", "2027-12-31")
        self.assertEqual(len(mytax_now), 1)
        self.assertEqual(mytax_now[0]["gross_amount"], 75.0)
        self.assertEqual(mytax_now[0]["claim_stream"], "abn")

        # =========================================================================
        # Case 2: Known dog_walking receipt with custom percentage (60%) preserves
        # dog_walking activity and 60% across compatible category corrections
        # =========================================================================
        dw_id = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Centennial Park Pet & Gear",
                "date": "2026-10-22",
                "total_amount": 200.00,
                "tax_amount": 0.0,
                "currency": "AUD",
                "category": "dog_walking_supplies",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "dog_walking",
                "business_pct": 60.0,
                "tax_label": "Other business expenses",
                "confidence": 1.0,
                "classification_source": "owner_correction",
            }
        )

        # Category correction to 'office_supplies' (shared category)
        # Must preserve activity='dog_walking' and business_pct=60.0% (never flip to printing_3d or 100%)
        corr_cat1 = cos.correct_receipt(receipt_id=dw_id, category="office_supplies")
        self.assertEqual(corr_cat1.category, "office_supplies")
        self.assertEqual(corr_cat1.business_activity, "dog_walking")
        self.assertEqual(corr_cat1.business_pct, 60.0)
        self.assertEqual(corr_cat1.claimable_amount, 120.0)  # 60% of $200

        # Category correction to 'tools_equipment' (shared category)
        # Must still preserve activity='dog_walking' and business_pct=60.0%
        corr_cat2 = cos.correct_receipt(receipt_id=dw_id, category="tools_equipment")
        self.assertEqual(corr_cat2.category, "tools_equipment")
        self.assertEqual(corr_cat2.business_activity, "dog_walking")
        self.assertEqual(corr_cat2.business_pct, 60.0)
        self.assertEqual(corr_cat2.claimable_amount, 120.0)

        # =========================================================================
        # Case 3: Category correction to an incompatible activity category
        # (e.g. materials_3d_printing on a dog_walking receipt)
        # Must NOT silently flip activity to printing_3d; must flag conflict
        # =========================================================================
        incompat = cos.correct_receipt(receipt_id=dw_id, category="materials_3d_printing")
        self.assertEqual(incompat.business_activity, "none")
        self.assertEqual(incompat.status, "needs_review")
        self.assertIn("incompatible with prior activity 'dog_walking'", incompat.flag_reason or "")

        # =========================================================================
        # Case 4: Category-only correction alone on an unconfirmed/needs-review receipt
        # does not confirm unrelated allocation fields or trigger vendor learning
        # =========================================================================
        unconf_id = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Unconfirmed Merchant",
                "date": "2026-10-25",
                "total_amount": 50.00,
                "currency": "AUD",
                "category": "tools_equipment",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "none",  # unconfirmed / missing
                "business_pct": 0.0,          # unconfirmed / missing
                "confidence": 0.50,
                "classification_source": "ai_suggestion",
            }
        )

        cat_only_unconf = cos.correct_receipt(receipt_id=unconf_id, category="software_subscriptions")
        self.assertEqual(cat_only_unconf.category, "software_subscriptions")
        self.assertEqual(cat_only_unconf.status, "needs_review")
        self.assertLess(cat_only_unconf.confidence, 0.80)
        self.assertIsNone(cos.km.memory.lookup(cat_only_unconf.vendor, confirmed_only=True))

    def test_defect_13_unallocated_needs_review_warning_and_new_commands(self):
        """
        Regression test (13):
        1. An unallocated needs-review receipt (business_pct=0.0 on ABN) causes /mytax and /total
           to issue a prominent warning alert rather than falsely stating no deductions exist.
        2. /total is a direct muscle memory alias for /mytax.
        3. /pnl and /report summarize FY 2026/27 net profit, Dog Walking P&L, and 3D Printing P&L in AUD.
        4. /spending returns monthly spending breakdown (business claimable vs personal).
        5. Bank transfers, Osko, PayID, and BNPL are recognized as category 'transfer', personal, 0%.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )
        bot = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=12345,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot.conn.close)

        # 1. Unallocated needs-review expense: ABN with no activity/pct (e.g. from stream switch PAYG -> ABN)
        rec_id = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Unallocated Merchant",
                "date": "2026-10-10",
                "total_amount": 120.00,
                "currency": "AUD",
                "category": "office_supplies",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "none",
                "business_pct": 0.0,
                "status": "needs_review",
                "flag_reason": "Missing business activity and percentage",
            }
        )

        # Call /mytax
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = []

        asyncio.run(bot.mytax_command(update, context))
        update.message.reply_text.assert_called_once()
        mytax_msg = update.message.reply_text.call_args[0][0]
        self.assertNotIn("No business or PAYG tax deductions recorded yet", mytax_msg)
        self.assertIn("Unallocated Needs-Review Records (1)", mytax_msg)
        self.assertIn(f"#{rec_id}", mytax_msg)
        self.assertIn("Total Confirmed Suggested Deductions*: `$0.00 AUD`", mytax_msg)

        # Call /total (alias for /mytax)
        update_total = MagicMock()
        update_total.effective_chat.id = 12345
        update_total.message.reply_text = AsyncMock()
        asyncio.run(bot.total_command(update_total, context))
        update_total.message.reply_text.assert_called_once()
        total_msg = update_total.message.reply_text.call_args[0][0]
        self.assertEqual(total_msg, mytax_msg)

        # 2. Add confirmed revenues and expenses for Dog Walking and 3D Printing
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Dog Walking Client",
                "date": "2026-10-12",
                "total_amount": 150.00,
                "currency": "AUD",
                "category": "income_dog_walking",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "dog_walking",
                "business_pct": 100.0,
                "status": "confirmed",
            }
        )
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Leash & Treat Depot",
                "date": "2026-10-14",
                "total_amount": 50.00,
                "currency": "AUD",
                "category": "dog_walking_supplies",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "dog_walking",
                "business_pct": 100.0,
                "status": "confirmed",
            }
        )
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] 3D Print Client",
                "date": "2026-10-15",
                "total_amount": 300.00,
                "currency": "AUD",
                "category": "income_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "confirmed",
            }
        )
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] 3D Maker Depot",
                "date": "2026-10-16",
                "total_amount": 100.00,
                "currency": "AUD",
                "category": "materials_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "confirmed",
            }
        )

        # Test /pnl command
        update_pnl = MagicMock()
        update_pnl.effective_chat.id = 12345
        update_pnl.message.reply_text = AsyncMock()
        asyncio.run(bot.pnl_command(update_pnl, context))
        update_pnl.message.reply_text.assert_called_once()
        pnl_msg = update_pnl.message.reply_text.call_args[0][0]
        self.assertIn("PROFIT & LOSS SUMMARY", pnl_msg)
        self.assertIn("Dog Walking ABN Activity", pnl_msg)
        self.assertIn("Income: `$150.00 AUD`", pnl_msg)
        self.assertIn("Expenses (Claimable): `$50.00 AUD`", pnl_msg)
        self.assertIn("Net Profit: `$100.00 AUD`", pnl_msg)
        self.assertIn("3D Printing ABN Activity", pnl_msg)
        self.assertIn("Income: `$300.00 AUD`", pnl_msg)
        self.assertIn("Expenses (Claimable): `$100.00 AUD`", pnl_msg)
        self.assertIn("Net Profit: `$200.00 AUD`", pnl_msg)
        self.assertIn("Total Revenue*: `$450.00 AUD`", pnl_msg)
        self.assertIn("Total Business Expenses*: `$150.00 AUD`", pnl_msg)
        self.assertIn("Net Profit (FY 2026/27)*: `$300.00 AUD`", pnl_msg)

        # Test /spending command
        update_spending = MagicMock()
        update_spending.effective_chat.id = 12345
        update_spending.message.reply_text = AsyncMock()
        context.args = ["2026-10"]
        asyncio.run(bot.spending_command(update_spending, context))
        update_spending.message.reply_text.assert_called_once()
        spend_msg = update_spending.message.reply_text.call_args[0][0]
        self.assertIn("MONTHLY SPENDING BREAKDOWN", spend_msg)
        self.assertIn("(2026-10)", spend_msg)
        self.assertIn("Business Claimable", spend_msg)
        self.assertIn("Personal Living", spend_msg)

        # 3. Test Bank Transfer / Osko / PayID / BNPL recognition
        transfer_notes = [
            "Osko payment to savings 250.00",
            "PayID transfer to friend 45.00",
            "ATM cash withdrawal 100.00",
            "Afterpay instalment 35.50",
            "Zip pay repayment 50.00",
            "Internal transfer between accounts 500",
        ]
        for note in transfer_notes:
            parsed = cos.finance._heuristic_parse_text(note)
            self.assertEqual(parsed.category, "transfer", f"Failed for note: {note}")
            self.assertEqual(parsed.claim_stream, "personal", f"Failed for note: {note}")
            self.assertEqual(parsed.business_activity, "none", f"Failed for note: {note}")
            self.assertEqual(parsed.business_pct, 0.0, f"Failed for note: {note}")

    def test_defect_14_payment_rails_income_preservation_and_pnl_spending_segregation(self):
        """
        Regression test (14 / QA Correction Request):
        1. Payment rails (PayID / Osko / Afterpay / Zip) are NOT proof of an internal transfer.
           Actual business income and business expenses paid via these rails are preserved.
        2. Ambiguous transfers and BNPL repayments remain in 'needs_review' to prevent double-counting.
        3. /spending excludes true internal transfers from consumption spending.
        4. /pnl excludes flagged/unverified income from confirmed profit totals and displays
           pending review income separately.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )
        bot = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=12345,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot.conn.close)

        # 1. Preserve actual income and expenses paid via payment rails
        # Income via Osko
        osko_inc = cos.finance._heuristic_parse_text("Osko payment from Client Sarah $150.00 dog walking")
        self.assertEqual(osko_inc.category, "income_dog_walking")
        self.assertEqual(osko_inc.claim_stream, "abn")
        self.assertEqual(osko_inc.business_activity, "dog_walking")
        self.assertEqual(osko_inc.business_pct, 100.0)
        self.assertIn("Osko", osko_inc.payment_method)

        # Income via PayID
        payid_inc = cos.finance._heuristic_parse_text("PayID received from Mike 3D print client $200.00")
        self.assertEqual(payid_inc.category, "income_3d_printing")
        self.assertEqual(payid_inc.claim_stream, "abn")
        self.assertEqual(payid_inc.business_activity, "printing_3d")
        self.assertEqual(payid_inc.business_pct, 100.0)
        self.assertIn("PayID", payid_inc.payment_method)

        # Expense via Afterpay
        afterpay_exp = cos.finance._heuristic_parse_text("Afterpay 3D Maker nozzle kit $80.00")
        self.assertEqual(afterpay_exp.category, "materials_3d_printing")
        self.assertEqual(afterpay_exp.claim_stream, "abn")
        self.assertEqual(afterpay_exp.business_activity, "printing_3d")
        self.assertEqual(afterpay_exp.business_pct, 100.0)
        self.assertIn("Afterpay", afterpay_exp.payment_method)

        # Expense via Osko
        osko_exp = cos.finance._heuristic_parse_text("Osko payment to Pet Supplies leash $45.00")
        self.assertEqual(osko_exp.category, "dog_walking_supplies")
        self.assertEqual(osko_exp.claim_stream, "abn")
        self.assertEqual(osko_exp.business_activity, "dog_walking")
        self.assertEqual(osko_exp.business_pct, 100.0)
        self.assertIn("Osko", osko_exp.payment_method)

        # 2. Ambiguous transfers and BNPL repayments stay in 'needs_review' to prevent double counting
        # (Case 1 from QA: Afterpay repayment for printer nozzle must NOT double-count as materials expense)
        nozzle_repay = cos.finance._heuristic_parse_text("Afterpay repayment for printer nozzle $20.00")
        self.assertEqual(nozzle_repay.category, "transfer")
        self.assertEqual(nozzle_repay.claim_stream, "personal")
        self.assertEqual(nozzle_repay.business_activity, "none")
        self.assertEqual(nozzle_repay.business_pct, 0.0)
        self.assertLess(nozzle_repay.confidence, 0.80)

        # (Case 2 from QA: Transfer from own savings to own dog walking account must NOT be supplies)
        savings_tfr = cos.finance._heuristic_parse_text("Transfer from own savings to own dog walking business account $500.00")
        self.assertEqual(savings_tfr.category, "transfer")
        self.assertEqual(savings_tfr.claim_stream, "personal")
        self.assertEqual(savings_tfr.business_activity, "none")
        self.assertEqual(savings_tfr.business_pct, 0.0)

        # (Case 3 from QA: A leash for the owner's own pet must NOT be 100% business)
        own_pet_leash = cos.finance._heuristic_parse_text("A leash for the owner's own pet $30.00")
        self.assertEqual(own_pet_leash.claim_stream, "personal")
        self.assertEqual(own_pet_leash.business_activity, "none")
        self.assertEqual(own_pet_leash.business_pct, 0.0)

        repay_summary = cos.process_note("Afterpay repayment $35.50 today")
        self.assertEqual(repay_summary.status, "needs_review")
        self.assertEqual(repay_summary.category, "transfer")
        self.assertIn("prevent double-counting", repay_summary.summary_text.lower())

        ambig_summary = cos.process_note("Transfer $250.00 today")
        self.assertEqual(ambig_summary.status, "needs_review")
        self.assertEqual(ambig_summary.category, "transfer")
        self.assertIn("pending owner review", ambig_summary.summary_text.lower())

        # Explicit internal transfer has high confidence
        explicit_tfr = cos.finance._heuristic_parse_text("Internal transfer to savings $500.00")
        self.assertEqual(explicit_tfr.category, "transfer")
        self.assertGreaterEqual(explicit_tfr.confidence, 0.85)

        # 3. /spending must exclude true internal transfers from consumption
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Local Cafe",
                "date": "2026-11-05",
                "total_amount": 10.00,
                "currency": "AUD",
                "category": "dining_coffee",
                "purpose": "personal",
                "claim_stream": "personal",
                "business_activity": "none",
                "business_pct": 0.0,
                "status": "confirmed",
            }
        )
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Office Stationers",
                "date": "2026-11-06",
                "total_amount": 50.00,
                "currency": "AUD",
                "category": "office_supplies",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "confirmed",
            }
        )
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Internal Bank Transfer",
                "date": "2026-11-07",
                "total_amount": 1000.00,
                "currency": "AUD",
                "category": "transfer",
                "purpose": "personal",
                "claim_stream": "personal",
                "business_activity": "none",
                "business_pct": 0.0,
                "status": "confirmed",
            }
        )

        spending_data = self.jm.get_spending_summary("2026-11")
        # Total spent MUST be $60.00 ($10 coffee + $50 office supplies), NOT $1,345.50!
        # Excludes all transfers ($1000 internal + $35.50 Afterpay repayment + $250 transfer = $1285.50)
        self.assertEqual(spending_data["total_spent"], 60.00)
        self.assertEqual(spending_data["business_claimable"], 50.00)
        self.assertEqual(spending_data["personal_share"], 10.00)
        self.assertEqual(spending_data["transfers_excluded"], 1285.50)

        update_spend = MagicMock()
        update_spend.effective_chat.id = 12345
        update_spend.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = ["2026-11"]
        asyncio.run(bot.spending_command(update_spend, context))
        update_spend.message.reply_text.assert_called_once()
        spend_msg = update_spend.message.reply_text.call_args[0][0]
        self.assertIn("$60.00 AUD", spend_msg)
        self.assertIn("1285.50 AUD in internal transfers", spend_msg)

        # 4. /pnl must exclude flagged/unverified income from confirmed profit totals
        # Confirmed dog walking income $200 and expense $50
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Dog Walking Client A",
                "date": "2026-08-01",
                "total_amount": 200.00,
                "currency": "AUD",
                "category": "income_dog_walking",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "dog_walking",
                "business_pct": 100.0,
                "status": "confirmed",
            }
        )
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Dog Supply Store",
                "date": "2026-08-02",
                "total_amount": 50.00,
                "currency": "AUD",
                "category": "dog_walking_supplies",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "dog_walking",
                "business_pct": 100.0,
                "status": "confirmed",
            }
        )
        # Unverified / flagged dog walking income $150
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Unverified Client Transfer",
                "date": "2026-08-03",
                "total_amount": 150.00,
                "currency": "AUD",
                "category": "income_dog_walking",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "dog_walking",
                "business_pct": 100.0,
                "status": "needs_review",
                "flag_reason": "Low confidence classification",
            }
        )
        # Confirmed 3D printing income $400 and expense $100
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] 3D Printing Client B",
                "date": "2026-08-04",
                "total_amount": 400.00,
                "currency": "AUD",
                "category": "income_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "confirmed",
            }
        )
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] 3D Filament Hub",
                "date": "2026-08-05",
                "total_amount": 100.00,
                "currency": "AUD",
                "category": "materials_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "confirmed",
            }
        )
        # Flagged 3D printing income $250
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Flagged 3D Client",
                "date": "2026-08-06",
                "total_amount": 250.00,
                "currency": "AUD",
                "category": "income_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "flagged",
                "flag_reason": "Date in future or duplicate invoice",
            }
        )

        # QA Reproduction 4: receipts with error, duplicate, or pending status
        # Error status receipt (must be completely excluded from confirmed and pending income)
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Error Client",
                "date": "2026-08-07",
                "total_amount": 500.00,
                "currency": "AUD",
                "category": "income_dog_walking",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "dog_walking",
                "business_pct": 100.0,
                "status": "error",
                "flag_reason": "OCR read error",
            }
        )
        # Duplicate status receipt (must be completely excluded)
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Duplicate 3D Client",
                "date": "2026-08-08",
                "total_amount": 300.00,
                "currency": "AUD",
                "category": "income_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "duplicate",
                "flag_reason": "Duplicate transaction hash",
            }
        )
        # Pending status income $100 (unverified -> goes to pending_income, NOT confirmed profit)
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Pending Verification Client",
                "date": "2026-08-09",
                "total_amount": 100.00,
                "currency": "AUD",
                "category": "income_3d_printing",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "pending",
                "flag_reason": "Awaiting customer settlement confirmation",
            }
        )

        pnl = self.jm.get_pnl_summary("2026-08-01", "2026-08-31")
        # Confirmed income: $200 (dog) + $400 (3d) = $600 (strictly excludes $150 needs_review, $250 flagged, $100 pending, and $500 error / $300 duplicate!)
        self.assertEqual(pnl["total_income"], 600.00)
        self.assertEqual(pnl["dog_walking"]["income"], 200.00)
        self.assertEqual(pnl["printing_3d"]["income"], 400.00)
        # Total expenses: $50 + $100 = $150
        self.assertEqual(pnl["total_expenses"], 150.00)
        # Confirmed Net Profit: $600 - $150 = $450 (NOT $1,650.00!)
        self.assertEqual(pnl["net_profit"], 450.00)
        # Pending income: $150 (dog needs_review) + $250 (3d flagged) + $100 (3d pending) = $500.00 (excludes error/duplicate)
        self.assertEqual(pnl["pending_income"], 500.00)
        self.assertEqual(pnl["dog_walking"]["pending_income"], 150.00)
        self.assertEqual(pnl["printing_3d"]["pending_income"], 350.00)

        update_pnl = MagicMock()
        update_pnl.effective_chat.id = 12345
        update_pnl.message.reply_text = AsyncMock()
        context.args = []
        with patch.object(self.jm, "get_pnl_summary", return_value=pnl):
            with patch.object(bot, "jm", self.jm):
                asyncio.run(bot.pnl_command(update_pnl, context))
        update_pnl.message.reply_text.assert_called_once()
        pnl_msg = update_pnl.message.reply_text.call_args[0][0]
        self.assertIn("Net Profit (FY 2026/27)*: `$450.00 AUD`", pnl_msg)
        self.assertIn("Pending Review Income (Excluded from Profit)*: `$500.00 AUD`", pnl_msg)

    def test_defect_15_ledger_conservation_and_spending_allowlist(self):
        """
        Regression test (15 / Turn 64 QA Critical Journal & Spending Defect):
        1. Critical synthetic journal reproduction: $120 Afterpay purchase + $30 repayment + $500 internal transfer.
           - _post_journal must NOT ignore payment method and must NOT fabricate bank credit $650 or personal drawings $530!
           - $120 Afterpay purchase credits BNPL Liability (2200), not Cash in Bank (1000).
           - $30 repayment and $500 internal transfer are excluded from journal posting until linkage and account information exist.
           - Trial balance: Cash in Bank (1000) credit is strictly $0.00 (not $650); Personal Living (5900) debit is strictly $0.00 (not $530);
             BNPL Liability (2200) credit is $120.00; Materials (5100) debit is $120.00; Debits == Credits == $120.00.
        2. /spending excludes internal transfers ($500) and repayments ($30) from consumption spending.
        3. /spending applies strict confirmed-status allowlist:
           - Unconfirmed / pending review business expenses ($75) are segregated into pending_review_spending and NEVER mislabeled as personal!
           - Duplicate and error records ($999 duplicate, $450 error) are completely excluded from both confirmed and pending review totals.
        4. Telegram bot /spending command clearly alerts on pending review expenses while keeping confirmed totals conserved.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )
        bot = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=12345,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot.conn.close)

        import datetime
        curr_month = datetime.date.today().strftime("%Y-%m")
        today_str = datetime.date.today().strftime("%Y-%m-%d")

        # 1. Critical sequence reproduction:
        # Step A: $120 3D printer nozzle kit via Afterpay
        afterpay_summary = cos.process_note("Fictional 3D Supply printer nozzle kit 120.00 via Afterpay today")
        self.assertEqual(afterpay_summary.total_amount, 120.00)
        self.assertIn("afterpay", afterpay_summary.payment_method.lower())
        self.assertEqual(afterpay_summary.category, "materials_3d_printing")
        self.assertEqual(afterpay_summary.status, "recorded")

        # Step B: $30 Afterpay repayment
        repay_summary = cos.process_note("Afterpay repayment $30.00 today")
        self.assertEqual(repay_summary.total_amount, 30.00)
        self.assertEqual(repay_summary.category, "transfer")

        # Step C: $500 Internal transfer between own accounts
        transfer_summary = cos.process_note("Transfer $500.00 from own savings to own dog walking business account today")
        self.assertEqual(transfer_summary.total_amount, 500.00)
        self.assertEqual(transfer_summary.category, "transfer")

        # 2. Verify Double-Entry Journal Trial Balance Conservation
        tb = self.jm.get_trial_balance()
        tb_by_code = {r["code"]: r for r in tb}

        # Bank Account (1000) MUST NOT be credited $650! Total credit must be $0.00.
        bank_tb = tb_by_code.get("1000", {})
        self.assertEqual(bank_tb.get("total_credit", 0.0), 0.00, "Bank account was falsely credited for BNPL/transfers!")
        self.assertEqual(bank_tb.get("total_debit", 0.0), 0.00)

        # Personal Drawings (5900) MUST NOT be debited $530! Total debit must be $0.00.
        drawings_tb = tb_by_code.get("5900", {})
        self.assertEqual(drawings_tb.get("total_debit", 0.0), 0.00, "Personal drawings was falsely debited for transfers/repayments!")

        # BNPL Liability (2200) MUST be credited $120.00
        bnpl_tb = tb_by_code.get("2200", {})
        self.assertEqual(bnpl_tb.get("total_credit", 0.0), 120.00)
        self.assertEqual(bnpl_tb.get("total_debit", 0.0), 0.00)

        # Materials & Consumables (5100) MUST be debited $120.00
        materials_tb = tb_by_code.get("5100", {})
        self.assertEqual(materials_tb.get("total_debit", 0.0), 120.00)
        self.assertEqual(materials_tb.get("total_credit", 0.0), 0.00)

        # Mathematical double-entry balance check: Total Debits == Total Credits == $120.00
        tot_debits = round(sum(r["total_debit"] for r in tb), 2)
        tot_credits = round(sum(r["total_credit"] for r in tb), 2)
        self.assertEqual(tot_debits, 120.00)
        self.assertEqual(tot_credits, 120.00)
        self.assertEqual(tot_debits, tot_credits)

        # 3. Verify /spending monthly breakdown
        spend_summary = self.jm.get_spending_summary(curr_month)
        self.assertEqual(spend_summary["total_spent"], 120.00)
        self.assertEqual(spend_summary["business_claimable"], 120.00)
        self.assertEqual(spend_summary["personal_share"], 0.00, "Personal share must not fabricate $530 drawings!")
        self.assertEqual(spend_summary["transfers_excluded"], 530.00, "Transfers ($500) + repayments ($30) must equal $530 excluded!")
        self.assertEqual(spend_summary["receipt_count"], 1)

        # 4. Add an unconfirmed pending business expense ($75) and verify it is NOT labeled as personal
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Ambiguous Tool Supplies",
                "date": today_str,
                "total_amount": 75.00,
                "currency": "AUD",
                "category": "tools_equipment",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "printing_3d",
                "business_pct": 100.0,
                "status": "needs_review",
                "flag_reason": "Low confidence classification",
            }
        )

        # Ensure unconfirmed receipt was NOT posted to the journal
        tb_after_unconf = self.jm.get_trial_balance()
        tot_debits_unconf = round(sum(r["total_debit"] for r in tb_after_unconf), 2)
        tot_credits_unconf = round(sum(r["total_credit"] for r in tb_after_unconf), 2)
        self.assertEqual(tot_debits_unconf, 120.00)
        self.assertEqual(tot_credits_unconf, 120.00)

        # In /spending, confirmed totals remain conserved; pending review is segregated
        spend_after_unconf = self.jm.get_spending_summary(curr_month)
        self.assertEqual(spend_after_unconf["total_spent"], 120.00)
        self.assertEqual(spend_after_unconf["business_claimable"], 120.00)
        self.assertEqual(spend_after_unconf["personal_share"], 0.00, "Pending business expense must NEVER be labeled personal!")
        self.assertEqual(spend_after_unconf["pending_review_spending"], 75.00)
        self.assertEqual(spend_after_unconf["pending_review_count"], 1)

        # 5. Add duplicate and error records ($999 duplicate, $450 error) and verify complete exclusion
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Duplicate Nozzle Order",
                "date": today_str,
                "total_amount": 999.00,
                "currency": "AUD",
                "category": "materials_3d_printing",
                "purpose": "business",
                "status": "duplicate",
                "flag_reason": "Duplicate transaction detected",
            }
        )
        cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Corrupted Scan",
                "date": today_str,
                "total_amount": 450.00,
                "currency": "AUD",
                "category": "materials_3d_printing",
                "purpose": "business",
                "status": "error",
                "flag_reason": "OCR read error",
            }
        )

        # Journal is still strictly $120.00
        tb_after_errors = self.jm.get_trial_balance()
        self.assertEqual(round(sum(r["total_debit"] for r in tb_after_errors), 2), 120.00)
        self.assertEqual(round(sum(r["total_credit"] for r in tb_after_errors), 2), 120.00)

        # /spending excludes duplicates and errors completely
        spend_after_errors = self.jm.get_spending_summary(curr_month)
        self.assertEqual(spend_after_errors["total_spent"], 120.00)
        self.assertEqual(spend_after_errors["business_claimable"], 120.00)
        self.assertEqual(spend_after_errors["personal_share"], 0.00)
        self.assertEqual(spend_after_errors["pending_review_spending"], 75.00)
        self.assertEqual(spend_after_errors["pending_review_count"], 1)

        # 6. Verify Telegram bot /spending command output formatting
        update_spend = MagicMock()
        update_spend.effective_chat.id = 12345
        update_spend.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = [curr_month]
        with patch.object(bot, "jm", self.jm):
            asyncio.run(bot.spending_command(update_spend, context))
        update_spend.message.reply_text.assert_called_once()
        spend_msg = update_spend.message.reply_text.call_args[0][0]
        self.assertIn("MONTHLY SPENDING BREAKDOWN", spend_msg)
        self.assertIn(f"({curr_month})", spend_msg)
        self.assertIn("Total Expenses: $120.00 AUD (1 receipts)", spend_msg)
        self.assertIn("Business Claimable*: `$120.00 AUD`", spend_msg)
        self.assertIn("Personal Living*: `$0.00 AUD`", spend_msg)
        self.assertIn("Pending Review Expenses (Excluded from Confirmed)*: `$75.00 AUD` (1 receipts)", spend_msg)
        self.assertIn("530.00 AUD in internal transfers / financing repayments from consumption", spend_msg)

    def test_defect_16_dog_walking_osko_field_specific_resolution_and_card_status(self):
        """
        Regression test (16 / Turn 65 QA Structured Review Resolution & Card Status):
        1. Dog walking leashes via Osko correctly starts in review ('needs_review') with classification uncertainty.
           - Explicit user confirmation of category + ABN + dog_walking activity + 100% business clears the
             classification uncertainty, marks status 'corrected', posts double-entry journal (Debit 5400, Credit 1000),
             and learns Vendor Memory.
        2. Field-specific resolution: resolving classification uncertainty does NOT clear unrelated QA flags.
           - If an unrelated flag (e.g. date in the future) exists, confirming classification clears the classification
             reason but keeps status 'flagged' with the future-date flag, blocking journal posting and vendor learning.
        3. Transfer / repayment card status:
           - Cards for internal transfers and repayments must NOT say 'Recorded'.
           - Must clearly state 'Saved; journal pending linkage/account details' and '(unposted to trial balance)'.
        """
        cos = ChiefOfStaff(
            main_conn=self.conn,
            gdrive=GDriveAdapter(receipts_folder_id=None),
            finance_agent=FinanceAgent(api_key=""),
        )

        # 1. Part A: Dog walking leashes via Osko starts in review, then cleanly confirmed
        note = "Dog walking leashes 85.00 via Osko today"
        summary = cos.process_note(note)
        self.assertEqual(summary.total_amount, 85.00)
        self.assertIn("osko", summary.payment_method.lower())
        self.assertEqual(summary.status, "needs_review")
        self.assertIn("pet item", (summary.flag_reason or "").lower())
        self.assertIn("Needs Review", summary.summary_text)

        # In review: trial balance has zero postings
        tb1 = self.jm.get_trial_balance()
        self.assertEqual(round(sum(r["total_debit"] for r in tb1), 2), 0.00)
        self.assertEqual(round(sum(r["total_credit"] for r in tb1), 2), 0.00)

        # Explicit user confirmation of all allocation fields: category + ABN + dog_walking + 100%
        corrected = cos.correct_receipt(
            receipt_id=summary.receipt_id,
            category="dog_walking_supplies",
            claim_stream="abn",
            business_activity="dog_walking",
            business_pct=100.0,
        )
        self.assertEqual(corrected.status, "corrected")
        self.assertIsNone(corrected.flag_reason)
        self.assertEqual(corrected.confidence, 1.0)

        # Journal is posted: Debit 5400 Dog Walking Supplies $85.00, Credit 1000 Bank $85.00
        tb2 = self.jm.get_trial_balance()
        tb2_by_code = {r["code"]: r for r in tb2}
        self.assertEqual(tb2_by_code.get("5400", {}).get("total_debit", 0.0), 85.00)
        self.assertEqual(tb2_by_code.get("1000", {}).get("total_credit", 0.0), 85.00)
        self.assertEqual(round(sum(r["total_debit"] for r in tb2), 2), 85.00)
        self.assertEqual(round(sum(r["total_credit"] for r in tb2), 2), 85.00)

        # Vendor memory is learned
        learned_rule = cos.km.memory.lookup(corrected.vendor, confirmed_only=True)
        self.assertIsNotNone(learned_rule)
        self.assertEqual(learned_rule.default_category, "dog_walking_supplies")
        self.assertEqual(learned_rule.default_activity, "dog_walking")

        # 2. Part B: Unrelated QA flag (future date) still blocks journal posting when classification is resolved
        future_id = cos.km.record_receipt(
            data={
                "vendor": "[FICTIONAL DEMO] Future Leash Store",
                "date": "2029-01-01",
                "total_amount": 95.00,
                "currency": "AUD",
                "category": "dog_walking_supplies",
                "purpose": "business",
                "claim_stream": "abn",
                "business_activity": "dog_walking",
                "business_pct": 100.0,
                "status": "flagged",
                "flag_reason": "Date 2029-01-01 is in the future.; Pet item without explicit business designation — suggested for Dog Walking business; verify if for client walking or personal pet.",
            }
        )
        corrected_flagged = cos.correct_receipt(
            receipt_id=future_id,
            category="dog_walking_supplies",
            claim_stream="abn",
            business_activity="dog_walking",
            business_pct=100.0,
        )
        self.assertEqual(corrected_flagged.status, "flagged", "Unrelated future date flag must keep status 'flagged'!")
        self.assertIn("in the future", corrected_flagged.flag_reason or "")
        self.assertNotIn("Pet item", corrected_flagged.flag_reason or "", "Classification uncertainty should be resolved!")
        self.assertLess(corrected_flagged.confidence, 1.0)

        # Unrelated flag blocks journal posting: trial balance debits remain strictly $85.00 (NOT $180.00!)
        tb3 = self.jm.get_trial_balance()
        self.assertEqual(round(sum(r["total_debit"] for r in tb3), 2), 85.00)
        self.assertEqual(round(sum(r["total_credit"] for r in tb3), 2), 85.00)

        # Vendor memory is NOT learned for unverified/flagged receipt
        self.assertIsNone(cos.km.memory.lookup("[FICTIONAL DEMO] Future Leash Store", confirmed_only=True))

        # 3. Part C: Transfer / repayment card status distinguishes saved receipt from journal posting
        tfr_summary = cos.process_note("Internal transfer to savings $500.00 today")
        self.assertNotIn("Recorded #", tfr_summary.summary_text, "Transfers must not say 'Recorded'!")
        self.assertIn("Saved; journal pending linkage/account details", tfr_summary.summary_text)
        self.assertIn("(unposted to trial balance)", tfr_summary.summary_text)

        repay_summary = cos.process_note("Afterpay repayment $30.00 today")
        self.assertNotIn("Recorded #", repay_summary.summary_text, "Repayments must not say 'Recorded'!")
        self.assertIn("Saved; journal pending linkage/account details", repay_summary.summary_text)
        self.assertIn("(unposted to trial balance)", repay_summary.summary_text)

    def test_task_1_telegram_bot_fails_closed_when_owner_chat_id_missing(self):
        """
        Task 1 Security Test:
        - When TELEGRAM_OWNER_CHAT_ID is missing (None/empty), _is_authorized must return False.
        - Messages from any chat ID must be rejected.
        - run() must exit with a clear error code (SystemExit).
        """
        bot_unauth = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=None,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot_unauth.conn.close)

        mock_update = MagicMock()
        mock_update.effective_chat.id = 12345
        self.assertFalse(
            bot_unauth._is_authorized(mock_update),
            "Bot must fail closed (return False) when owner_chat_id is None!"
        )

        # Non-matching chat ID with valid owner configured
        bot_auth = TelegramReceiptBot(
            bot_token="fictional_token",
            owner_chat_id=99999,
            db_path=self.db_path,
            finance_agent=FinanceAgent(api_key=""),
            load_dotenv=False,
        )
        self.addCleanup(bot_auth.conn.close)
        self.assertFalse(bot_auth._is_authorized(mock_update))

        # Matching chat ID is authorized
        mock_update_owner = MagicMock()
        mock_update_owner.effective_chat.id = 99999
        self.assertTrue(bot_auth._is_authorized(mock_update_owner))

        # run() exits cleanly when owner_chat_id is missing
        with self.assertRaises(SystemExit) as cm:
            bot_unauth.run()
        self.assertEqual(cm.exception.code, 1)

    def test_finding_12_non_numeric_owner_chat_id_fails_closed(self):
        """
        Finding #12 Test:
        - When TELEGRAM_OWNER_CHAT_ID is a non-numeric string (e.g. from unedited .env.example),
          __init__ must fail closed with SystemExit(1) instead of raising an unhandled ValueError.
        """
        with patch.dict(os.environ, {"TELEGRAM_OWNER_CHAT_ID": "your_chat_id_here"}):
            with self.assertRaises(SystemExit) as cm:
                TelegramReceiptBot(
                    bot_token="fictional_token",
                    owner_chat_id=None,
                    db_path=self.db_path,
                    finance_agent=FinanceAgent(api_key=""),
                    load_dotenv=False,
                )
            self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
