"""
Chief of Staff (Front Door) for Neo-D LedgerCraft
Coordinates the intake pipeline: receives documents and notes, triggers Finance parsing,
applies user-confirmed Vendor Memory rules, orchestrates the QA review loop, uploads to Google Drive
(reporting honest upload failure status when unavailable), and calls Knowledge Manager.
Never invents financial records when OCR is offline/unavailable.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.agents.finance_agent import (
    ExtractionUnavailableError,
    FinanceAgent,
    ParsedReceipt,
)
from src.agents.knowledge_manager import KnowledgeManager
from src.agents.qa_agent import QAAgent
from src.storage.categories import (
    ATO_SUGGESTION_DISCLAIMER,
    format_activity_badge,
    format_stream_badge,
)
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
    status: str  # recorded, corrected, flagged, needs_review, or extraction_unavailable
    flag_reason: Optional[str]
    gdrive_file_id: Optional[str]
    summary_text: str
    tax_amount: float = 0.0
    raw_tax_amount: Optional[float] = None
    gst_status: str = "unknown"
    claimable_amount: float = 0.0
    personal_share: float = 0.0
    claim_stream: str = "abn"
    business_activity: str = "printing_3d"
    business_pct: float = 100.0
    payment_method: str = "Bank Account (1000)"
    items_summary: str = ""
    confidence: float = 0.85
    classification_source: str = "ai_suggestion"
    uncertainty_note: Optional[str] = None
    gdrive_status: str = "not_applicable"
    gdrive_error: Optional[str] = None


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

    def _apply_vendor_memory_if_known(self, parsed: ParsedReceipt) -> ParsedReceipt:
        """
        If the vendor has an existing USER-CONFIRMED rule in vendor_memory
        (source='owner_correction', confidence=1.0), applies the remembered category,
        stream, activity, business %, and tax label while preserving the receipt's own
        explicit/unknown GST.
        """
        if not parsed.vendor or "general merchant" in parsed.vendor.lower():
            return parsed

        rule = self.km.memory.lookup(parsed.vendor, confirmed_only=True)
        if not rule:
            return parsed

        orig_tax = parsed.tax_amount
        orig_gst_status = parsed.gst_status

        parsed.vendor = rule.canonical_name or parsed.vendor
        parsed.category = rule.default_category
        parsed.purpose = rule.default_purpose
        parsed.claim_stream = rule.default_claim_stream
        parsed.business_activity = rule.default_activity
        parsed.business_pct = rule.default_business_pct
        parsed.tax_label = rule.default_tax_label
        parsed.confidence = 1.0
        parsed.classification_source = "vendor_memory"
        parsed.uncertainty_note = None
        parsed.tax_amount = orig_tax
        parsed.gst_status = orig_gst_status
        return self.finance._normalize_parsed_receipt(parsed, "vendor_memory")

    @staticmethod
    def _format_items_summary(items_json: Optional[str], vendor: str, total: float) -> str:
        if items_json:
            try:
                items = json.loads(items_json)
                if isinstance(items, list) and items:
                    parts = []
                    for it in items[:3]:
                        desc = str(it.get("description", "")).strip()
                        amt = float(it.get("amount", 0.0) or 0.0)
                        if desc:
                            parts.append(f"{desc} (${amt:.2f})")
                    if parts:
                        return ", ".join(parts)
            except Exception:
                pass
        return f"{vendor} (${total:.2f})"

    def _build_summary_from_receipt_row(
        self,
        rec: Dict[str, Any],
        uncertainty_note: Optional[str] = None,
        gdrive_error: Optional[str] = None,
        is_correction: bool = False,
    ) -> IntakeSummary:
        receipt_id = int(rec["id"])
        vendor = str(rec["vendor"])
        date = str(rec["date"])
        total = round(float(rec["total_amount"]), 2)
        tax = round(float(rec.get("tax_amount") or 0.0), 2)
        raw_tax = rec.get("raw_tax_amount")
        raw_tax_val: Optional[float] = round(float(raw_tax), 2) if raw_tax is not None else None
        gst_status = str(rec.get("gst_status") or ("explicit" if raw_tax_val is not None else "unknown"))
        currency = str(rec.get("currency") or "AUD")
        purpose = str(rec.get("purpose") or "business")
        category = str(rec.get("category") or "general")
        claim_stream = str(rec.get("claim_stream") or ("abn" if purpose == "business" else "personal"))
        business_activity = str(rec.get("business_activity") or ("printing_3d" if purpose == "business" else "none"))
        business_pct = round(float(rec.get("business_pct") if rec.get("business_pct") is not None else (100.0 if purpose == "business" else 0.0)), 2)
        claimable_amount = round(float(rec.get("claimable_amount") if rec.get("claimable_amount") is not None else (total * (business_pct / 100.0) if purpose == "business" else 0.0)), 2)
        personal_share = round(total - claimable_amount, 2)
        tax_label = rec.get("tax_label")
        payment_method = str(rec.get("payment_method") or "Bank Account (1000)")
        items_summary = self._format_items_summary(rec.get("items_json"), vendor, total)
        confidence = float(rec.get("confidence") if rec.get("confidence") is not None else 0.85)
        classification_source = str(rec.get("classification_source") or "ai_suggestion")
        uncertainty_note = uncertainty_note if uncertainty_note is not None else rec.get("uncertainty_note")
        status = str(rec.get("status") or "recorded")
        flag_reason = rec.get("flag_reason")
        gdrive_id = rec.get("gdrive_file_id")
        gdrive_status = str(rec.get("gdrive_status") or "not_applicable")

        is_transfer_or_repayment = (
            category.lower() in ("transfer", "internal_transfer", "bnpl_repayment")
            or "transfer" in category.lower()
            or "repayment" in category.lower()
        )

        needs_review = status in ("flagged", "needs_review") or bool((flag_reason or "").strip()) or confidence < 0.80
        if is_transfer_or_repayment and is_correction and not needs_review:
            header_icon = "✏️"
            header_verb = "Updated (Saved; journal pending linkage/account details)"
        elif is_transfer_or_repayment and not needs_review:
            header_icon = "💾"
            header_verb = "Saved; journal pending linkage/account details"
        elif is_correction and not needs_review:
            header_icon = "✏️"
            header_verb = "Updated"
        elif needs_review:
            header_icon = "⚠️"
            header_verb = "Needs Review"
        elif rec.get("local_path"):
            header_icon = "🧾"
            header_verb = "Recorded"
        else:
            header_icon = "✅"
            header_verb = "Recorded"

        currency_sym = "$" if currency == "AUD" else f"{currency} "
        stream_badge = format_stream_badge(claim_stream)
        act_badge = format_activity_badge(business_activity)

        if classification_source == "owner_correction" and not needs_review and confidence >= 1.0:
            source_line = "✏️ *Owner Verified Correction* (`100%` confidence — saved to Vendor Memory)"
        elif classification_source == "owner_correction":
            conf_pct = int(round(confidence * 100))
            source_line = f"✏️ *Category Corrected by Owner* (`{conf_pct}%` overall confidence — unresolved QA flags remain)"
        elif classification_source == "vendor_memory" and not needs_review:
            source_line = "🧠 *Learned Vendor Rule* (`100%` confidence — auto-applied from Owner Memory)"
        else:
            conf_pct = int(round(confidence * 100))
            source_line = f"🤖 *AI Suggestion* (`{conf_pct}%` confidence — unconfirmed; tap buttons below or use `/correct {receipt_id}`)"

        lines = [
            f"{header_icon} {header_verb} #{receipt_id}: {vendor} — {currency_sym}{total:.2f} ({purpose}, {category})",
            f"• *Date*: `{date}` | *Payment*: `{payment_method}`",
            f"• *Items*: {items_summary}",
        ]

        # Conditional details: Personal entries do not show irrelevant business % or ABN activity allocations
        if claim_stream == "personal" or purpose == "personal":
            lines.append(f"• *Stream*: {stream_badge} | *Category*: `{category}` _(Personal / Non-deductible)_")
        else:
            if claim_stream == "abn":
                lines.append(f"• *Stream*: {stream_badge} | *Activity*: {act_badge} | *Category*: `{category}`")
            else:
                lines.append(f"• *Stream*: {stream_badge} | *Category*: `{category}`")

            if gst_status == "explicit":
                gst_text = f"GST: `${tax:.2f} AUD` (explicit)"
            else:
                gst_text = "GST: `Unknown` ($0.00 claimed — verify tax invoice)"

            lines.append(
                f"• *Business %*: `{business_pct:.0f}%` → Claimable: `${claimable_amount:.2f} AUD` ({gst_text}) | Personal: `${personal_share:.2f} AUD`"
            )
            if tax_label:
                lines.append(f"• *Suggested ATO myTax*: `{tax_label}` _({ATO_SUGGESTION_DISCLAIMER})_")

        lines.append(f"• *Source*: {source_line}")

        if uncertainty_note and classification_source not in ("owner_correction", "vendor_memory"):
            lines.append(f"• ⚠️ *Uncertainty Note*: {uncertainty_note}")

        if needs_review:
            review_msg = flag_reason or "Held in Needs Review — excluded from confirmed myTax claim totals until verified."
            lines.append(f"• 🚨 *Review Status*: `{status}` ({review_msg}) — _Segregated from confirmed myTax totals_")

        if is_transfer_or_repayment:
            lines.append("• ℹ️ *Journal Status*: Saved; journal pending linkage/account details (unposted to trial balance)")

        # Honest Google Drive status reporting
        if gdrive_status == "uploaded" and gdrive_id:
            lines.append(f"• ☁️ *Google Drive*: Backed up (`ID: {gdrive_id}`)")
        elif gdrive_status in ("failed", "not_configured"):
            reason = gdrive_error or self.gdrive.last_error or "Google Drive upload unavailable"
            lines.append(f"• ⚠️ *Google Drive Backup Failed*: `{reason}` — stored locally only")
        else:
            lines.append("• 📝 *Storage*: Saved in local SQLite ledger (typed note — no file attachment)")

        return IntakeSummary(
            receipt_id=receipt_id,
            vendor=vendor,
            date=date,
            total_amount=total,
            tax_amount=tax,
            raw_tax_amount=raw_tax_val,
            gst_status=gst_status,
            claimable_amount=claimable_amount,
            personal_share=personal_share,
            currency=currency,
            purpose=purpose,
            claim_stream=claim_stream,
            business_activity=business_activity,
            business_pct=business_pct,
            category=category,
            tax_label=tax_label,
            payment_method=payment_method,
            items_summary=items_summary,
            confidence=confidence,
            classification_source=classification_source,
            uncertainty_note=uncertainty_note,
            status=status,
            flag_reason=flag_reason,
            gdrive_file_id=gdrive_id,
            gdrive_status=gdrive_status,
            gdrive_error=gdrive_error,
            summary_text="\n".join(lines),
        )

    def process_note(self, text: str) -> IntakeSummary:
        """Processes a casual typed note without a file."""
        examples = self.km.memory.get_recent_examples(limit=10)

        retry_count = 0
        qa_notes = []
        parsed = None
        qa_res = None

        while retry_count <= self.qa.max_retries:
            parsed = self.finance.parse_note(text, qa_notes=qa_notes, vendor_examples=examples)
            parsed = self._apply_vendor_memory_if_known(parsed)
            parsed_dict = parsed.model_dump()
            qa_res = self.qa.inspect_receipt(parsed_dict, retry_count=retry_count)

            if qa_res.passed or qa_res.flagged:
                break

            qa_notes = qa_res.issues
            retry_count += 1

        receipt_id = self.km.record_receipt(
            data=parsed.model_dump(),
            file_hash=None,
            local_path=None,
            gdrive_file_id=None,
            gdrive_status="not_applicable",
            flagged=qa_res.flagged,
            flag_reason=qa_res.flag_reason,
        )

        rec = self.km.get_receipt(receipt_id)
        return self._build_summary_from_receipt_row(
            rec=rec,
            uncertainty_note=parsed.uncertainty_note,
            gdrive_error=None,
        )

    def process_file(self, file_path: str) -> IntakeSummary:
        """
        Processes an image or PDF receipt document.
        Never invents amounts if multimodal OCR is offline or fails; instead reports
        extraction unavailable and requests manual details without posting to the books.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, "rb") as f:
            file_bytes = f.read()
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        # Upload to Google Drive first and capture honest status
        gdrive_id = None
        gdrive_status = "failed"
        gdrive_error = None
        try:
            gdrive_id = self.gdrive.upload_receipt(file_path)
            if gdrive_id:
                gdrive_status = "uploaded"
            else:
                gdrive_status = getattr(self.gdrive, "last_status", "failed") or "failed"
                gdrive_error = getattr(self.gdrive, "last_error", None) or "Google Drive upload returned no verified file ID"
        except Exception as e:
            gdrive_status = "failed"
            gdrive_error = str(e)

        cursor = self.main_conn.execute("SELECT id, file_hash, vendor, total_amount, date FROM receipts")
        existing_receipts = [dict(r) for r in cursor.fetchall()]

        examples = self.km.memory.get_recent_examples(limit=10)

        retry_count = 0
        qa_notes = []
        parsed = None
        qa_res = None

        try:
            while retry_count <= self.qa.max_retries:
                parsed = self.finance.parse_document(file_path, qa_notes=qa_notes, vendor_examples=examples)
                parsed = self._apply_vendor_memory_if_known(parsed)
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
        except ExtractionUnavailableError as err:
            drive_line = (
                f"• ☁️ *Google Drive*: Backed up (`ID: {gdrive_id}`)"
                if (gdrive_status == "uploaded" and gdrive_id)
                else f"• ⚠️ *Google Drive Backup Failed*: `{gdrive_error or 'Unavailable'}` — stored locally only"
            )
            msg = "\n".join(
                [
                    f"⚠️ *Receipt OCR Extraction Unavailable*: `{os.path.basename(file_path)}`",
                    f"• *Reason*: {err}",
                    "• *Books Integrity*: **No financial record or journal entry was posted** (zero invented amounts).",
                    "• *Action Required*: Reply with a typed note (e.g. `Vendor Name $Amount category`) to record exact details manually.",
                    drive_line,
                ]
            )
            return IntakeSummary(
                receipt_id=0,
                vendor="Extraction Unavailable",
                date=datetime.date.today().isoformat(),
                total_amount=0.0,
                currency="AUD",
                purpose="personal",
                category="unparsed",
                tax_label=None,
                status="extraction_unavailable",
                flag_reason=str(err),
                gdrive_file_id=gdrive_id,
                gdrive_status=gdrive_status,
                gdrive_error=gdrive_error,
                summary_text=msg,
            )

        data_dict = parsed.model_dump()
        data_dict["original_filename"] = os.path.basename(file_path)

        receipt_id = self.km.record_receipt(
            data=data_dict,
            file_hash=file_hash,
            local_path=file_path,
            gdrive_file_id=gdrive_id,
            gdrive_status=gdrive_status,
            flagged=qa_res.flagged,
            flag_reason=qa_res.flag_reason,
        )

        rec = self.km.get_receipt(receipt_id)
        return self._build_summary_from_receipt_row(
            rec=rec,
            uncertainty_note=parsed.uncertainty_note,
            gdrive_error=gdrive_error,
        )

    def correct_receipt(
        self,
        receipt_id: int,
        category: Optional[str] = None,
        purpose: Optional[str] = None,
        claim_stream: Optional[str] = None,
        business_activity: Optional[str] = None,
        business_pct: Optional[float] = None,
        tax_label: Optional[str] = None,
        vendor: Optional[str] = None,
        tax_amount: Optional[float] = None,
        payment_method: Optional[str] = None,
        date: Optional[str] = None,
        remember_vendor: bool = True,
    ) -> IntakeSummary:
        """
        Applies an owner correction to an existing receipt, recalculating all tax,
        claimable amounts, and double-entry journal legs without duplicates,
        and returns an updated IntakeSummary.
        """
        updated_rec = self.km.correct_receipt(
            receipt_id=receipt_id,
            category=category,
            purpose=purpose,
            claim_stream=claim_stream,
            business_activity=business_activity,
            business_pct=business_pct,
            tax_label=tax_label,
            vendor=vendor,
            tax_amount=tax_amount,
            payment_method=payment_method,
            date=date,
            remember_vendor=remember_vendor,
        )
        return self._build_summary_from_receipt_row(
            rec=updated_rec,
            uncertainty_note=updated_rec.get("uncertainty_note"),
            gdrive_error=self.gdrive.last_error if updated_rec.get("gdrive_status") in ("failed", "not_configured") else None,
            is_correction=True,
        )

    def process_batch_or_statement(self, file_path: str) -> Dict[str, Any]:
        """
        Handles multi-receipt photos/PDFs OR Bank Statement reconciliation (CSV/PDF/Image).
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        examples = self.km.memory.get_recent_examples(limit=15)
        batch = self.finance.parse_batch_or_statement(file_path, vendor_examples=examples)

        if len(batch.receipts) == 1 and batch.document_type == "single_receipt" and not file_path.lower().endswith(".csv"):
            single_res = self.process_file(file_path)
            return {
                "document_type": "single_receipt",
                "recorded_count": 1 if single_res.status != "extraction_unavailable" else 0,
                "matched_count": 0,
                "xp_earned": 10 if single_res.status != "extraction_unavailable" else 0,
                "summary_text": single_res.summary_text,
                "receipt_ids": [single_res.receipt_id] if single_res.receipt_id else [],
            }

        cursor = self.main_conn.execute("SELECT id, vendor, total_amount, date FROM receipts")
        existing = [dict(r) for r in cursor.fetchall()]

        recorded_lines: List[str] = []
        matched_lines: List[str] = []
        recorded_ids: List[int] = []
        recorded_count = 0
        matched_count = 0

        for item in batch.receipts:
            item = self._apply_vendor_memory_if_known(item)
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
                gdrive_status="not_applicable",
                flagged=qa_res.flagged,
                flag_reason=qa_res.flag_reason,
            )
            existing.append({"id": rec_id, "vendor": item.vendor, "total_amount": item.total_amount, "date": item.date})
            recorded_ids.append(rec_id)
            recorded_count += 1
            icon = "⚠️" if qa_res.flagged else "🔄"
            recorded_lines.append(
                f"  • {icon} *Synced #{rec_id}*: {item.vendor} — *${item.total_amount:.2f} AUD* "
                f"({item.claim_stream.upper()}, `{item.category}`, `{item.business_pct:.0f}%`)"
            )

        doc_title = (
            "🏦 *BANK STATEMENT AI AUTO-SYNC COMPLETE*"
            if batch.document_type == "bank_statement" or file_path.lower().endswith(".csv")
            else f"📸 *MULTI-RECEIPT BATCH CAPTURE ({len(batch.receipts)} items)*"
        )
        summary_parts = [
            doc_title,
            f"• *New / Missed Receipts Added*: `{recorded_count}`",
            f"• *Already Logged (Deduplicated)*: `{matched_count}`",
            f"• _Note: AI classifications and ATO myTax labels are suggestions only — use `/correct <id>` to adjust._\n",
        ]
        if recorded_lines:
            summary_parts.append("*Newly Recorded Transactions:*")
            summary_parts.extend(recorded_lines)
        if matched_lines:
            summary_parts.append("\n*Already Reconciled (Skipped Duplicates):*")
            summary_parts.extend(matched_lines[:8])

        xp_earned = max(10, recorded_count * 10) if recorded_count > 0 else 0
        return {
            "document_type": batch.document_type,
            "recorded_count": recorded_count,
            "matched_count": matched_count,
            "xp_earned": xp_earned,
            "summary_text": "\n".join(summary_parts),
            "receipt_ids": recorded_ids,
        }
