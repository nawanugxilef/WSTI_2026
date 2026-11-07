"""
Chief of Staff (Front Door) for Neo-D LedgerCraft
Coordinates the intake pipeline: receives documents and notes, triggers Finance parsing,
orchestrates the QA review loop, uploads to Google Drive, and calls Knowledge Manager.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.agents.finance_agent import FinanceAgent, ParsedReceipt
from src.agents.knowledge_manager import KnowledgeManager
from src.agents.qa_agent import QAAgent, QACheckResult
from src.storage.gdrive import GDriveAdapter


@dataclass
class IntakeSummary:
    receipt_id: int
    vendor: str
    date: str
    total_amount: float
    currency: str
    purpose: str
    category: str
    tax_label: Optional[str]
    status: str # recorded or flagged
    flag_reason: Optional[str]
    gdrive_file_id: Optional[str]
    summary_text: str


class ChiefOfStaff:
    def __init__(
        self,
        main_conn: sqlite3.Connection,
        gdrive: Optional[GDriveAdapter] = None,
        finance_agent: Optional[FinanceAgent] = None,
        qa_agent: Optional[QAAgent] = None,
    ):
        self.main_conn = main_conn
        self.gdrive = gdrive or GDriveAdapter()
        self.finance = finance_agent or FinanceAgent()
        self.qa = qa_agent or QAAgent()
        self.km = KnowledgeManager(main_conn)

    def process_note(self, text: str) -> IntakeSummary:
        """Processes a casual typed note without a file."""
        # 1. Fetch recent vendor examples for few-shot context
        examples = self.km.memory.get_recent_examples(limit=10)

        # 2. Build-Review-Revise loop
        retry_count = 0
        qa_notes = []
        parsed = None
        qa_res = None

        while retry_count <= self.qa.max_retries:
            parsed = self.finance.parse_note(text, qa_notes=qa_notes, vendor_examples=examples)
            parsed_dict = parsed.model_dump()
            qa_res = self.qa.inspect_receipt(parsed_dict, retry_count=retry_count)

            if qa_res.passed or qa_res.flagged:
                break

            # Need revision
            qa_notes = qa_res.issues
            retry_count += 1

        # 3. Record via Knowledge Manager
        receipt_id = self.km.record_receipt(
            data=parsed.model_dump(),
            file_hash=None,
            local_path=None,
            gdrive_file_id=None,
            flagged=qa_res.flagged,
            flag_reason=qa_res.flag_reason,
        )

        status_icon = "⚠️" if qa_res.flagged else "✅"
        currency_sym = "$" if parsed.currency == "AUD" else f"{parsed.currency} "
        summary = (
            f"{status_icon} Recorded #{receipt_id}: {parsed.vendor} — {currency_sym}{parsed.total_amount:.2f} "
            f"({parsed.purpose}, {parsed.category})"
        )
        if qa_res.flagged:
            summary += f"\n  (Flagged: {qa_res.flag_reason})"

        return IntakeSummary(
            receipt_id=receipt_id,
            vendor=parsed.vendor,
            date=parsed.date,
            total_amount=parsed.total_amount,
            currency=parsed.currency,
            purpose=parsed.purpose,
            category=parsed.category,
            tax_label=parsed.tax_label,
            status="flagged" if qa_res.flagged else "recorded",
            flag_reason=qa_res.flag_reason,
            gdrive_file_id=None,
            summary_text=summary,
        )

    def process_file(self, file_path: str) -> IntakeSummary:
        """Processes an image or PDF receipt document."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        # Compute file hash
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        # Check existing duplicates in database
        cursor = self.main_conn.execute("SELECT id, file_hash, vendor, total_amount, date FROM receipts")
        existing_receipts = [dict(r) for r in cursor.fetchall()]

        examples = self.km.memory.get_recent_examples(limit=10)

        retry_count = 0
        qa_notes = []
        parsed = None
        qa_res = None

        while retry_count <= self.qa.max_retries:
            parsed = self.finance.parse_document(file_path, qa_notes=qa_notes, vendor_examples=examples)
            parsed_dict = parsed.model_dump()
            parsed_dict["file_hash"] = file_hash
            parsed_dict["original_filename"] = os.path.basename(file_path)

            qa_res = self.qa.inspect_receipt(
                parsed_dict, existing_receipts=existing_receipts, retry_count=retry_count
            )

            if qa_res.passed or qa_res.flagged:
                break

            qa_notes = qa_res.issues
            retry_count += 1

        # Upload to Google Drive
        gdrive_id = None
        try:
            gdrive_id = self.gdrive.upload_receipt(file_path)
        except Exception as e:
            print(f"[ChiefOfStaff] Google Drive upload skipped/failed: {e}")

        # Record via Knowledge Manager
        receipt_id = self.km.record_receipt(
            data=parsed.model_dump(),
            file_hash=file_hash,
            local_path=file_path,
            gdrive_file_id=gdrive_id,
            flagged=qa_res.flagged,
            flag_reason=qa_res.flag_reason,
        )

        status_icon = "⚠️" if qa_res.flagged else "🧾"
        currency_sym = "$" if parsed.currency == "AUD" else f"{parsed.currency} "
        summary = (
            f"{status_icon} Recorded #{receipt_id}: {parsed.vendor} — {currency_sym}{parsed.total_amount:.2f} "
            f"({parsed.purpose}, {parsed.category})"
        )
        if gdrive_id:
            summary += f"\n  (Backed up to Google Drive ID: {gdrive_id})"
        if qa_res.flagged:
            summary += f"\n  (Flagged: {qa_res.flag_reason})"

        return IntakeSummary(
            receipt_id=receipt_id,
            vendor=parsed.vendor,
            date=parsed.date,
            total_amount=parsed.total_amount,
            currency=parsed.currency,
            purpose=parsed.purpose,
            category=parsed.category,
            tax_label=parsed.tax_label,
            status="flagged" if qa_res.flagged else "recorded",
            flag_reason=qa_res.flag_reason,
            gdrive_file_id=gdrive_id,
            summary_text=summary,
        )

    def process_batch_or_statement(self, file_path: str) -> Dict[str, Any]:
        """
        Handles multi-receipt photos/PDFs OR Bank Statement reconciliation (CSV/PDF/Image):
        - Extracts 1..N transactions in a single Finance AI model call.
        - Reconciles against existing SQLite receipts (skips already-logged receipts so there are no duplicates).
        - Runs QA check and records any missed ('forgotten') transactions via Knowledge Manager.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        examples = self.km.memory.get_recent_examples(limit=15)
        batch = self.finance.parse_batch_or_statement(file_path, vendor_examples=examples)

        if len(batch.receipts) == 1 and batch.document_type == "single_receipt" and not file_path.lower().endswith(".csv"):
            single_res = self.process_file(file_path)
            return {
                "document_type": "single_receipt",
                "recorded_count": 1,
                "matched_count": 0,
                "xp_earned": 10,
                "summary_text": single_res.summary_text,
            }

        cursor = self.main_conn.execute("SELECT id, vendor, total_amount, date FROM receipts")
        existing = [dict(r) for r in cursor.fetchall()]

        recorded_lines: List[str] = []
        matched_lines: List[str] = []
        recorded_count = 0
        matched_count = 0

        for item in batch.receipts:
            already_logged = False
            for ex in existing:
                if abs(float(ex["total_amount"]) - float(item.total_amount)) < 0.05:
                    if ex["date"] == item.date or ex["vendor"].lower()[:6] in item.vendor.lower():
                        already_logged = True
                        matched_count += 1
                        matched_lines.append(
                            f"  • ✅ *Matched #{ex['id']}*: {item.vendor} (${item.total_amount:.2f} AUD on {item.date}) — _Already logged, skipped duplicate_"
                        )
                        break

            if already_logged:
                continue

            item_dict = item.model_dump()
            qa_res = self.qa.inspect_receipt(item_dict, existing_receipts=existing, retry_count=0)
            rec_id = self.km.record_receipt(
                data=item_dict,
                file_hash=None,
                local_path=file_path,
                gdrive_file_id=None,
                flagged=qa_res.flagged,
                flag_reason=qa_res.flag_reason,
            )
            existing.append({"id": rec_id, "vendor": item.vendor, "total_amount": item.total_amount, "date": item.date})
            recorded_count += 1
            icon = "⚠️" if qa_res.flagged else "🔄"
            recorded_lines.append(
                f"  • {icon} *Synced #{rec_id}*: {item.vendor} — *${item.total_amount:.2f} AUD* ({item.purpose}, `{item.category}`)"
            )

        doc_title = (
            "🏦 *BANK STATEMENT AI AUTO-SYNC COMPLETE*"
            if batch.document_type == "bank_statement" or file_path.lower().endswith(".csv")
            else f"📸 *MULTI-RECEIPT BATCH CAPTURE ({len(batch.receipts)} items)*"
        )
        summary_parts = [
            doc_title,
            f"• *New / Missed Receipts Added*: `{recorded_count}`",
            f"• *Already Logged (Deduplicated)*: `{matched_count}`\n",
        ]
        if recorded_lines:
            summary_parts.append("*Newly Recorded Transactions:*")
            summary_parts.extend(recorded_lines)
        if matched_lines:
            summary_parts.append("\n*Already Reconciled (Skipped Duplicates):*")
            summary_parts.extend(matched_lines[:8])

        xp_earned = max(10, recorded_count * 10)
        return {
            "document_type": batch.document_type,
            "recorded_count": recorded_count,
            "matched_count": matched_count,
            "xp_earned": xp_earned,
            "summary_text": "\n".join(summary_parts),
        }
