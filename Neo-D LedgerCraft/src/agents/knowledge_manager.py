"""
Knowledge Manager (Code) for Neo-D LedgerCraft
Files transactions into the books, posts balanced double-entry journal entries
(including mixed-use business/personal percentage splits), handles owner corrections
with full recalculation and zero duplicate entries, preserves explicit vs unknown GST,
and updates Vendor Memory strictly from user-confirmed corrections.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from typing import Any, Dict, List, Optional

from src.agents.qa_agent import QAAgent, likely_duplicate_receipt_id
from src.storage.categories import (
    VALID_ACTIVITIES,
    VALID_STREAMS,
    is_category_activity_compatible,
    is_tax_label_compatible,
    load_category_catalog,
    recompute_compatible_category,
    require_finite_float,
    resolve_allocation,
)
from src.storage.journal import JournalLeg, JournalManager
from src.storage.memory import VendorMemoryManager


class KnowledgeManager:
    def __init__(self, main_conn: sqlite3.Connection):
        self.main_conn = main_conn
        self.journal = JournalManager(main_conn)
        self.memory = VendorMemoryManager(main_conn)
        self.catalog = load_category_catalog()
        self.qa = QAAgent()

    def _ensure_account_exists(self, code: str, name: str, acc_type: str = "expense") -> str:
        cur = self.main_conn.execute("SELECT code FROM accounts WHERE code = ?", (code,))
        if cur.fetchone():
            return code
        with nullcontext() if self.main_conn.in_transaction else self.main_conn:
            self.main_conn.execute(
                "INSERT OR IGNORE INTO accounts (code, name, type, currency) VALUES (?, ?, ?, 'AUD')",
                (code, name, acc_type),
            )
        return code

    @staticmethod
    def _is_income_category(category: str) -> bool:
        c = (category or "").lower()
        return any(k in c for k in ("income", "revenue", "sales"))

    @staticmethod
    def _low_confidence_matters(claim_stream: str, category: str) -> bool:
        # A guessed category on a personal purchase changes no tax claim, so it is recorded and the
        # owner retags it with one tap. Business claims and transfers still wait for review.
        c = (category or "").lower()
        return claim_stream != "personal" or "transfer" in c or "repayment" in c

    @staticmethod
    def _is_classification_or_allocation_clause(clause: str) -> bool:
        """
        Returns True if the clause represents classification, stream, activity,
        percentage, or payment rail interpretation uncertainty rather than a hard
        integrity/QA flag (such as future dates, duplicates, invoice flags, or invalid GST).
        """
        c_low = (clause or "").lower().strip()
        if not c_low:
            return False
        # Hard integrity/QA flags are NEVER classification clauses
        if (
            clause.startswith("Date ")
            or any(
                k in c_low
                for k in (
                    "in the future",
                    "future date",
                    "invalid date format",
                    "transaction date is missing",
                    "date must be",
                    "duplicate",
                    "currency",
                    "item lines",
                    "item sum",
                    "vendor name is missing",
                    "ocr read error",
                    "invoice number",
                )
            )
        ):
            return False
        if clause.startswith("GST amount") or "exceeds expected maximum" in c_low or "negative gst" in c_low:
            return False

        # Classification / allocation uncertainty indicators
        return any(
            k in c_low
            for k in (
                "low confidence",
                "ambiguous",
                "ai suggestion",
                "classification",
                "missing abn",
                "missing payg",
                "incompatible with prior activity",
                "incompatible with business_activity",
                "incompatible payg",
                "unconfirmed allocation",
                "pet item",
                "business designation",
                "personal pet",
                "client walking",
                "verify whether this is",
                "verify if for client",
                "verify category",
                "suggested for dog walking",
                "suggested for 3d printing",
                "payment rail",
                "financing repayment",
                "double-counting",
                "pending owner review",
                "needs owner review",
                "heuristic",
                "unverified transfer",
                "unverified client",
                "unallocated",
            )
        )

    @classmethod
    def validate_gst_amount(cls, total: float, raw_tax: Optional[float], currency: str = "AUD") -> None:
        """
        Validates that an explicit GST amount is a finite number, non-negative, does not exceed
        the receipt total, and complies with the Australian 10% GST maximum share check
        (total / 11 + $0.10 margin). Raises ValueError if invalid.
        """
        total_val = require_finite_float(total, "total_amount")
        if raw_tax is None:
            return
        tax_val = round(require_finite_float(raw_tax, "tax_amount"), 2)
        if tax_val < 0.0:
            raise ValueError(f"GST amount (${tax_val:.2f}) cannot be negative.")
        if tax_val > total_val:
            raise ValueError(f"GST amount (${tax_val:.2f}) cannot exceed receipt total (${total_val:.2f}).")
        if str(currency).upper() == "AUD" and total_val > 0:
            max_expected_gst = round(total_val / 11.0 + 0.10, 2)
            if tax_val > max_expected_gst:
                raise ValueError(
                    f"GST amount (${tax_val:.2f}) exceeds expected maximum 10% share for total ${total_val:.2f} (max ~${max_expected_gst:.2f})."
                )

    @classmethod
    def _is_gst_valid(cls, total: float, raw_tax: Optional[float], currency: str = "AUD") -> bool:
        try:
            cls.validate_gst_amount(total=total, raw_tax=raw_tax, currency=currency)
            return True
        except ValueError:
            return False

    @classmethod
    def _calculate_tax_and_claim(
        cls,
        total: float,
        raw_tax: Optional[float],
        gst_status: str,
        currency: str,
        purpose: str,
        business_pct: float,
        category: str,
    ) -> Dict[str, Any]:
        """
        Calculates claimable_amount, personal_share, claimable tax_amount, raw_tax_amount,
        and gst_status without ever fabricating GST when raw_tax is 0.0 or None (unknown),
        and never allowing NaN, negative, or >total/invalid GST into claimable tax_amount.
        """
        total_val = round(require_finite_float(total, "total_amount"), 2)
        pct_val = require_finite_float(business_pct, "business_pct")

        if raw_tax is not None:
            gross_gst: Optional[float] = round(require_finite_float(raw_tax, "tax_amount"), 2)
            resolved_gst_status = "explicit"
        else:
            gross_gst = None
            resolved_gst_status = "unknown" if gst_status != "explicit" else "unknown"

        gst_valid = cls._is_gst_valid(total=total_val, raw_tax=gross_gst, currency=currency)

        if cls._is_income_category(category):
            claim_gst = gross_gst if (gross_gst is not None and gst_valid and currency == "AUD") else 0.0
            return {
                "claimable_amount": round(total_val, 2),
                "tax_amount": claim_gst,
                "raw_tax_amount": gross_gst,
                "gst_status": resolved_gst_status,
                "personal_share": 0.0,
            }

        if purpose != "business" or pct_val <= 0.0 or currency != "AUD":
            return {
                "claimable_amount": 0.0,
                "tax_amount": 0.0,
                "raw_tax_amount": gross_gst,
                "gst_status": resolved_gst_status,
                "personal_share": round(total_val, 2),
            }

        pct_ratio = max(0.0, min(100.0, pct_val)) / 100.0
        claimable = round(total_val * pct_ratio, 2)
        personal_share = round(total_val - claimable, 2)

        if gross_gst is not None and gst_valid:
            # Explicit valid GST (including 0.00 or $2.00): scale strictly by business_pct
            claimable_gst = round(gross_gst * pct_ratio, 2)
        else:
            # Unknown or invalid GST: never invent 1/11th or claim invalid GST
            claimable_gst = 0.0

        return {
            "claimable_amount": claimable,
            "tax_amount": claimable_gst,
            "raw_tax_amount": gross_gst,
            "gst_status": resolved_gst_status,
            "personal_share": personal_share,
        }

    def record_receipt(
        self,
        data: Dict[str, Any],
        file_hash: Optional[str] = None,
        local_path: Optional[str] = None,
        gdrive_file_id: Optional[str] = None,
        gdrive_status: str = "not_applicable",
        flagged: bool = False,
        flag_reason: Optional[str] = None,
    ) -> int:
        """
        Saves receipt to DB and generates a balanced double-entry journal transaction.
        Validates finite numeric fields and supported categories before writing.
        Unconfirmed AI/heuristic classifications are NOT saved to Vendor Memory as
        confirmed rules; only owner-confirmed corrections update Vendor Memory.
        """
        vendor = str(data.get("vendor") or "").strip()
        if not vendor:
            raise ValueError("Vendor name cannot be empty.")
        date = str(data.get("date") or "").strip()
        if not date:
            raise ValueError("Transaction date cannot be empty.")

        total = round(require_finite_float(data.get("total_amount"), "total_amount"), 2)
        if total <= 0:
            raise ValueError(f"Total amount must be greater than 0 (got {total}).")

        currency = str(data.get("currency", "AUD")).upper()
        cat_raw = str(data.get("category") or "general").strip().lower()
        if cat_raw not in self.catalog:
            raise ValueError(
                f"Unsupported category '{data.get('category')}'. Valid categories: {', '.join(sorted(self.catalog.keys()))}"
            )

        if data.get("business_pct") is not None:
            pct_check = require_finite_float(data.get("business_pct"), "business_pct")
            if pct_check < 0.0 or pct_check > 100.0:
                raise ValueError(f"Business percentage must be between 0 and 100 (got {pct_check}).")

        # Validate explicit tax_amount (including 0.0) vs None (unknown) — reject NaN / inf before DB write
        raw_tax_in = data.get("raw_tax_amount") if "raw_tax_amount" in data else data.get("tax_amount")
        if raw_tax_in is not None:
            raw_tax_in = round(require_finite_float(raw_tax_in, "tax_amount"), 2)
        gst_status_in = str(data.get("gst_status") or ("explicit" if raw_tax_in is not None else "unknown"))

        if data.get("confidence") is not None:
            conf_in = require_finite_float(data.get("confidence"), "confidence")
            if conf_in < 0.0 or conf_in > 1.0:
                raise ValueError(f"Confidence must be between 0.0 and 1.0 (got {conf_in}).")
        else:
            conf_in = 0.85

        alloc = resolve_allocation(
            category=cat_raw,
            purpose=data.get("purpose"),
            claim_stream=data.get("claim_stream"),
            business_activity=data.get("business_activity"),
            business_pct=data.get("business_pct"),
            tax_label=data.get("tax_label"),
            catalog=self.catalog,
            strict_category=True,
            allow_incomplete=True,
        )
        category = str(alloc["category"])
        purpose = str(alloc["purpose"])
        claim_stream = str(alloc["claim_stream"])
        business_activity = str(alloc["business_activity"])
        business_pct = float(alloc["business_pct"])
        tax_label = alloc["tax_label"]

        # Detect missing or incompatible required allocation fields so we never fabricate defaults or confirm them
        missing_allocation_reasons: List[str] = []
        raw_act_in = str(data.get("business_activity") or "").strip().lower()
        if claim_stream == "abn":
            if raw_act_in in ("dog_walking", "printing_3d") and not is_category_activity_compatible(
                cat_raw, raw_act_in, self.catalog
            ):
                missing_allocation_reasons.append(
                    f"Category '{cat_raw}' is incompatible with business_activity '{raw_act_in}' — please confirm activity and category"
                )
            elif business_activity not in ("dog_walking", "printing_3d"):
                missing_allocation_reasons.append(
                    "Missing ABN business_activity — please select dog_walking or printing_3d"
                )
            if business_pct <= 0.0:
                missing_allocation_reasons.append(
                    "Missing ABN business_pct — please select deductible business percentage"
                )
        elif claim_stream == "payg":
            if business_activity != "payg_work":
                missing_allocation_reasons.append(
                    f"Incompatible PAYG business_activity '{raw_act_in or business_activity}'"
                )
            if business_pct <= 0.0:
                missing_allocation_reasons.append(
                    "Missing PAYG business_pct — please select deductible work percentage"
                )

        # Effective deductible percentage is 0.0 while required activity/pct allocation is incomplete
        effective_pct = business_pct if not missing_allocation_reasons else 0.0
        amounts = self._calculate_tax_and_claim(
            total=total,
            raw_tax=raw_tax_in,
            gst_status=gst_status_in,
            currency=currency,
            purpose=purpose,
            business_pct=effective_pct,
            category=category,
        )
        tax = amounts["tax_amount"]
        raw_tax_amount = amounts["raw_tax_amount"]
        gst_status = amounts["gst_status"]
        claimable_amount = amounts["claimable_amount"]

        confidence = conf_in
        classification_source = str(data.get("classification_source", "ai_suggestion") or "ai_suggestion")
        if missing_allocation_reasons:
            confidence = min(confidence, 0.50)
            if classification_source in ("owner_correction", "vendor_memory"):
                classification_source = "ai_suggestion"

        if data.get("confirmed_fields") is not None:
            confirmed_fields_list = sorted({str(x) for x in data["confirmed_fields"]})
        elif classification_source in ("owner_correction", "vendor_memory") and not missing_allocation_reasons:
            confirmed_fields_list = ["activity", "category", "pct", "stream"]
        else:
            confirmed_fields_list = []
        confirmed_fields_json = json.dumps(confirmed_fields_list)

        payment_method = str(data.get("payment_method") or "Bank Account (1000)")

        items_raw = data.get("items") or []
        items_serialized = []
        for it in items_raw:
            if isinstance(it, dict):
                items_serialized.append({"description": str(it.get("description", "")), "amount": float(it.get("amount", 0.0))})
            elif hasattr(it, "description") and hasattr(it, "amount"):
                items_serialized.append({"description": str(it.description), "amount": float(it.amount)})
        if not items_serialized:
            items_serialized = [{"description": f"{vendor} ({category})", "amount": total}]
        items_json = json.dumps(items_serialized)

        notes = data.get("notes")
        uncertainty_note = data.get("uncertainty_note")
        if missing_allocation_reasons and not uncertainty_note:
            uncertainty_note = "; ".join(missing_allocation_reasons)

        # Run deterministic QA validation on the incoming record so invalid dates/GST/currency/items are always flagged
        qa_check = self.qa.inspect_receipt(
            {
                "vendor": vendor,
                "date": date,
                "total_amount": total,
                "tax_amount": raw_tax_amount if raw_tax_amount is not None else 0.0,
                "raw_tax_amount": raw_tax_amount,
                "currency": currency,
                "items": items_raw if items_raw else None,
            },
            existing_receipts=None,
            retry_count=self.qa.max_retries,
        )

        combined_flags: List[str] = []
        if flag_reason:
            for part in str(flag_reason).split(";"):
                p = part.strip()
                if p and p not in combined_flags:
                    combined_flags.append(p)
        for issue in qa_check.issues:
            if issue not in combined_flags:
                combined_flags.append(issue)
        for alloc_issue in missing_allocation_reasons:
            if alloc_issue not in combined_flags:
                combined_flags.append(alloc_issue)

        data_status = str(data.get("status") or "").strip().lower()
        has_flag_reason = bool((flag_reason or "").strip()) or bool(str(data.get("flag_reason") or "").strip())
        is_qa_flagged = bool(flagged) or bool(qa_check.issues) or (data_status == "flagged") or has_flag_reason
        needs_classification_review = (
            (data_status in ("needs_review", "pending", "unverified"))
            or bool(missing_allocation_reasons)
            or (
                confidence < 0.80
                and self._low_confidence_matters(claim_stream, category)
                and classification_source not in ("owner_correction", "vendor_memory")
            )
        )

        if data_status in ("error", "duplicate"):
            status_val = data_status
            reason_val = "; ".join(combined_flags) if combined_flags else f"Status: {data_status}"
        elif is_qa_flagged:
            status_val = "flagged"
            reason_val = "; ".join(combined_flags) if combined_flags else "Flagged by QA validation"
            if confidence >= 1.0:
                confidence = 0.95
        elif needs_classification_review:
            status_val = data_status if data_status in ("needs_review", "pending", "unverified") else "needs_review"
            reason_val = (
                "; ".join(combined_flags)
                if combined_flags
                else (uncertainty_note or "Low confidence AI classification — needs owner review")
            )
        elif data_status == "confirmed":
            status_val = "confirmed"
            reason_val = "; ".join(combined_flags) if combined_flags else None
        else:
            status_val = "recorded"
            reason_val = "; ".join(combined_flags) if combined_flags else None

        with self.main_conn:
            cursor = self.main_conn.execute(
                """
                INSERT INTO receipts (
                    file_hash, original_filename, local_path, gdrive_file_id, gdrive_status,
                    vendor, date, total_amount, tax_amount, raw_tax_amount, gst_status, currency,
                    category, purpose, claim_stream, business_activity, business_pct,
                    claimable_amount, tax_label, payment_method, items_json,
                    confidence, classification_source, confirmed_fields, uncertainty_note, status, flag_reason, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_hash,
                    data.get("original_filename"),
                    local_path,
                    gdrive_file_id,
                    gdrive_status,
                    vendor,
                    date,
                    total,
                    tax,
                    raw_tax_amount,
                    gst_status,
                    currency,
                    category,
                    purpose,
                    claim_stream,
                    business_activity,
                    business_pct,
                    claimable_amount,
                    tax_label,
                    payment_method,
                    items_json,
                    confidence,
                    classification_source,
                    confirmed_fields_json,
                    uncertainty_note,
                    status_val,
                    reason_val,
                    notes,
                ),
            )
            receipt_id = cursor.lastrowid

        # Only record to Vendor Memory if explicitly marked as a complete owner correction and not flagged
        if (
            classification_source == "owner_correction"
            and not is_qa_flagged
            and not missing_allocation_reasons
        ):
            self.memory.record_correction(
                raw_vendor=vendor,
                category=category,
                purpose=purpose,
                tax_label=tax_label,
                canonical_name=vendor,
                claim_stream=claim_stream,
                business_activity=business_activity,
                business_pct=business_pct,
                source="owner_correction",
                confidence=1.0,
            )

        # Post balanced double-entry journal entry (idempotent by reference)
        self._post_journal_for_receipt(
            receipt_id=receipt_id,
            vendor=vendor,
            date=date,
            total=total,
            category=category,
            purpose=purpose,
            business_pct=effective_pct,
            business_activity=business_activity,
            payment_method=payment_method,
            status=status_val,
            flag_reason=reason_val,
        )

        return receipt_id

    def get_receipt(self, receipt_id: int) -> Optional[Dict[str, Any]]:
        cur = self.main_conn.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,))
        row = cur.fetchone()
        return dict(row) if row else None

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
        *,
        distinct_from: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Applies an owner correction to an existing receipt, validating all modified fields
        and revalidating the complete candidate record before mutating the database, journal,
        or Vendor Memory. Preserves unresolved validation flags and field-level uncertainty
        when a correction only touches unrelated fields (e.g., vendor-only rename).
        """
        current = self.get_receipt(receipt_id)
        if not current:
            raise ValueError(f"Receipt #{receipt_id} not found.")

        total = round(float(current["total_amount"]), 2)
        if total <= 0:
            raise ValueError(f"Receipt #{receipt_id} has invalid total amount (${total:.2f}).")

        currency = str(current.get("currency", "AUD")).upper()
        if currency != "AUD":
            raise ValueError(f"Unsupported currency '{currency}'. All transactions must be in AUD.")

        # 1. Fail-safe validation of explicit correction inputs before any mutation
        if vendor is not None:
            if not str(vendor).strip():
                raise ValueError("Vendor name cannot be empty.")
            new_vendor = str(vendor).strip()
        else:
            new_vendor = str(current["vendor"]).strip()
            if not new_vendor:
                raise ValueError("Vendor name cannot be empty.")

        if date is not None:
            new_date = str(date).strip()
            date_check = self.qa.inspect_receipt(
                {"vendor": new_vendor, "date": new_date, "total_amount": total, "tax_amount": 0.0, "currency": currency},
                retry_count=self.qa.max_retries,
            )
            date_issues = [
                i for i in date_check.issues
                if "date" in i.lower()
            ]
            if date_issues:
                raise ValueError("; ".join(date_issues))
        else:
            new_date = str(current["date"]).strip()

        if category is not None:
            cat_in = str(category).strip().lower()
            if not cat_in or cat_in not in self.catalog:
                raise ValueError(
                    f"Unsupported category '{category}'. Valid categories: {', '.join(sorted(self.catalog.keys()))}"
                )

        if purpose is not None:
            p_in = str(purpose).strip().lower()
            if p_in not in ("business", "personal", "abn", "payg"):
                raise ValueError(f"Invalid purpose '{purpose}'. Expected 'business' or 'personal'.")

        if business_pct is not None:
            pct_in = require_finite_float(business_pct, "business_pct")
            if pct_in < 0.0 or pct_in > 100.0:
                raise ValueError(f"Business percentage must be between 0 and 100 (got {pct_in}).")

        if claim_stream is not None:
            s_in = str(claim_stream).strip().lower()
            if s_in not in ("abn", "payg", "personal", "business"):
                raise ValueError(f"Invalid claim_stream '{claim_stream}'. Expected 'abn', 'payg', or 'personal'.")

        if business_activity is not None:
            a_in = str(business_activity).strip().lower()
            if a_in not in VALID_ACTIVITIES:
                raise ValueError(f"Invalid business_activity '{business_activity}'. Expected one of {VALID_ACTIVITIES}.")

        # Validate explicit cross-field compatibility if multiple conflicting arguments were passed together
        if claim_stream is not None and business_activity is not None:
            s_norm = "abn" if str(claim_stream).strip().lower() == "business" else str(claim_stream).strip().lower()
            a_norm = str(business_activity).strip().lower()
            if s_norm == "abn" and a_norm not in ("dog_walking", "printing_3d"):
                raise ValueError(f"Activity '{a_norm}' is incompatible with claim_stream 'abn'.")
            if s_norm == "payg" and a_norm != "payg_work":
                raise ValueError(f"Activity '{a_norm}' is incompatible with claim_stream 'payg'.")
            if s_norm == "personal" and a_norm != "none":
                raise ValueError(f"Activity '{a_norm}' is incompatible with claim_stream 'personal'.")

        if claim_stream is not None and category is not None:
            s_norm = "abn" if str(claim_stream).strip().lower() == "business" else str(claim_stream).strip().lower()
            c_spec = self.catalog[str(category).strip().lower()]
            if c_spec.stream != s_norm:
                raise ValueError(
                    f"Category '{c_spec.key}' (stream '{c_spec.stream}') is incompatible with claim_stream '{s_norm}'."
                )

        if category is not None and business_activity is not None:
            c_key = str(category).strip().lower()
            a_norm = str(business_activity).strip().lower()
            if not is_category_activity_compatible(c_key, a_norm, self.catalog):
                raise ValueError(
                    f"Category '{c_key}' is incompatible with business_activity '{a_norm}'."
                )

        if claim_stream is not None and business_pct is not None:
            s_norm = "abn" if str(claim_stream).strip().lower() == "business" else str(claim_stream).strip().lower()
            pct_norm = require_finite_float(business_pct, "business_pct")
            if s_norm == "personal" and pct_norm > 0.0:
                raise ValueError("Cannot set business_pct > 0 when claim_stream is 'personal'.")
            if s_norm in ("abn", "payg") and pct_norm == 0.0:
                raise ValueError(f"Cannot set business_pct = 0 when claim_stream is '{s_norm}'.")

        if tax_amount is not None:
            gst_in = round(require_finite_float(tax_amount, "tax_amount"), 2)
            self.validate_gst_amount(total=total, raw_tax=gst_in, currency=currency)
            preserved_raw_tax: Optional[float] = gst_in
            preserved_gst_status = "explicit"
        else:
            preserved_raw_tax = current.get("raw_tax_amount")
            if preserved_raw_tax is not None:
                preserved_raw_tax = round(require_finite_float(preserved_raw_tax, "raw_tax_amount"), 2)
            preserved_gst_status = str(
                current.get("gst_status") or ("explicit" if preserved_raw_tax is not None else "unknown")
            )

        new_payment = (payment_method or current.get("payment_method") or "Bank Account (1000)").strip()

        prev_stream = str(
            current.get("claim_stream") or ("abn" if current.get("purpose") == "business" else "personal")
        ).strip().lower()
        cur_act = str(current.get("business_activity") or "none").strip().lower()
        cur_pct = require_finite_float(
            current.get("business_pct") if current.get("business_pct") is not None else 0.0,
            "business_pct",
        )
        prev_source = str(current.get("classification_source") or "ai_suggestion")
        prev_conf = float(current.get("confidence") if current.get("confidence") is not None else 0.85)
        prev_status = str(current.get("status") or "recorded")
        prev_uncertainty = current.get("uncertainty_note")

        try:
            confirmed_fields = set(json.loads(current.get("confirmed_fields") or "[]"))
        except Exception:
            confirmed_fields = set()
        # Record the owner's decision about this pair alongside other reviewed fields.
        # A later category edit should not re-flag a match the owner already reviewed.
        if distinct_from is not None:
            decision = f"distinct_from:{distinct_from}"
            if decision in confirmed_fields:
                return current
            if (
                prev_status != "flagged"
                or not current.get("file_hash")
                or distinct_from != likely_duplicate_receipt_id(current.get("flag_reason"))
            ):
                raise ValueError("This receipt no longer has that possible duplicate match.")
        if prev_source in ("owner_correction", "vendor_memory") and prev_conf >= 1.0 and prev_status in ("recorded", "corrected"):
            confirmed_fields.update({"stream", "category", "activity", "pct"})
        was_classification_confirmed = (prev_source == "owner_correction" and prev_conf >= 1.0)

        stream_explicit = (claim_stream is not None) or (purpose is not None)
        cat_explicit = category is not None
        act_explicit = business_activity is not None
        pct_explicit = business_pct is not None
        tax_label_explicit = tax_label is not None

        target_cat = (category or current["category"] or "general").strip().lower()
        if target_cat not in self.catalog:
            raise ValueError(f"Unsupported category '{target_cat}'.")
        cat_spec = self.catalog.get(target_cat)

        # Determine target stream (abn / payg / personal)
        if claim_stream is not None:
            s_low = claim_stream.strip().lower()
            target_stream = "abn" if s_low == "business" else s_low
        elif purpose is not None:
            p_low = purpose.strip().lower()
            target_stream = "personal" if p_low == "personal" else ("payg" if p_low == "payg" else "abn")
        elif act_explicit and business_activity.strip().lower() in ("dog_walking", "printing_3d"):
            target_stream = "abn"
        elif act_explicit and business_activity.strip().lower() == "payg_work":
            target_stream = "payg"
        elif act_explicit and business_activity.strip().lower() == "none" and prev_stream == "personal":
            target_stream = "personal"
        elif cat_explicit and cat_spec:
            target_stream = cat_spec.stream
        elif pct_explicit and require_finite_float(business_pct, "business_pct") > 0 and prev_stream == "personal":
            target_stream = cat_spec.stream if (cat_spec and cat_spec.stream in ("abn", "payg")) else "abn"
        elif pct_explicit and require_finite_float(business_pct, "business_pct") <= 0:
            target_stream = "personal"
        else:
            target_stream = prev_stream

        stream_changed = target_stream != prev_stream
        if stream_changed:
            if not cat_explicit:
                confirmed_fields.discard("category")
            if not act_explicit:
                confirmed_fields.discard("activity")
            if not pct_explicit and target_stream == "personal":
                confirmed_fields.discard("pct")

        # Determine target business_pct:
        # Preserve known percentage across compatible category/activity/stream corrections; never fabricate 100% on stream switch!
        if target_stream == "personal":
            target_pct = 0.0
            confirmed_fields.add("pct")
        elif pct_explicit:
            target_pct = require_finite_float(business_pct, "business_pct")
            if target_pct > 0.0:
                confirmed_fields.add("pct")
            else:
                confirmed_fields.discard("pct")
        elif not stream_changed and cur_pct > 0.0:
            # Same stream: preserve existing custom or known business_pct (never overwrite with cat_spec.default_business_pct)
            target_pct = cur_pct
        elif stream_changed and "pct" in confirmed_fields and cur_pct > 0.0:
            # Stream changed, but user had explicitly selected a percentage previously: preserve user-selected percentage
            target_pct = cur_pct
        else:
            # Stream changed without a user-selected percentage (e.g. PAYG -> ABN), or prior pct was 0.0: do NOT fabricate 100%
            target_pct = 0.0
            confirmed_fields.discard("pct")

        # Determine target activity:
        # Preserve known compatible activity across category/pct corrections; never fabricate 'printing_3d'!
        incompatible_cat_act_msg: Optional[str] = None
        if target_stream == "personal":
            target_activity = "none"
            confirmed_fields.add("activity")
        elif target_stream == "payg":
            target_activity = "payg_work"
            confirmed_fields.add("activity")
        elif act_explicit:
            act_val = business_activity.strip().lower()
            if act_val in ("dog_walking", "printing_3d"):
                target_activity = act_val
                confirmed_fields.add("activity")
                confirmed_fields.add("stream")
            else:
                target_activity = "none"
                confirmed_fields.discard("activity")
        elif prev_stream == "abn" and cur_act in ("dog_walking", "printing_3d"):
            if cat_explicit and not is_category_activity_compatible(target_cat, cur_act, self.catalog):
                # Category-only correction conflicts with prior activity (e.g. dog_walking -> materials_3d_printing):
                # Do NOT silently flip activity to printing_3d; require explicit user choice.
                target_activity = "none"
                confirmed_fields.discard("activity")
                incompatible_cat_act_msg = (
                    f"Category '{target_cat}' is incompatible with prior activity '{cur_act}' — please explicitly select business_activity"
                )
            else:
                # Preserve known compatible activity (e.g. dog_walking stays dog_walking when category changes to office_supplies/tools_equipment)
                target_activity = cur_act
        else:
            # Stream switched to ABN without explicit activity, or prior activity was missing ('none'):
            # Do NOT fabricate 'printing_3d'!
            target_activity = "none"
            confirmed_fields.discard("activity")

        if stream_explicit:
            confirmed_fields.add("stream")
        if cat_explicit:
            confirmed_fields.add("category")
            confirmed_fields.add("stream")

        # Recompute compatible category whenever category was not explicitly supplied and stream/activity changed
        if not cat_explicit:
            target_cat = recompute_compatible_category(
                current_cat=target_cat,
                target_stream=target_stream,
                target_activity=target_activity,
                catalog=self.catalog,
            )
            cat_spec = self.catalog.get(target_cat)

        # Determine tax label — validate explicit tax_label and never retain stale incompatible labels (e.g. PAYG D5 on ABN)
        if tax_label_explicit:
            tl_clean = str(tax_label).strip()
            if not is_tax_label_compatible(target_stream, tl_clean):
                raise ValueError(f"ATO myTax label '{tl_clean}' is incompatible with claim_stream '{target_stream}'.")
            target_tax_label: Optional[str] = tl_clean
        elif target_stream == "personal":
            target_tax_label = None
        elif cat_spec and cat_spec.stream == target_stream and cat_spec.tax_label and is_tax_label_compatible(target_stream, cat_spec.tax_label):
            target_tax_label = cat_spec.tax_label
        elif current.get("tax_label") and is_tax_label_compatible(target_stream, current.get("tax_label")):
            target_tax_label = str(current.get("tax_label"))
        elif target_stream == "payg":
            target_tax_label = "D5 Other work-related expenses"
        else:
            target_tax_label = "Other business expenses"

        alloc = resolve_allocation(
            category=target_cat,
            purpose="business" if target_stream in ("abn", "payg") else "personal",
            claim_stream=target_stream,
            business_activity=target_activity,
            business_pct=target_pct,
            tax_label=target_tax_label,
            catalog=self.catalog,
            strict_category=True,
            allow_incomplete=True,
        )

        final_cat = str(alloc["category"])
        final_purpose = str(alloc["purpose"])
        final_stream = str(alloc["claim_stream"])
        final_activity = str(alloc["business_activity"])
        final_pct = float(alloc["business_pct"])
        final_tax_label = alloc["tax_label"]

        # Check for missing or incompatible required allocation fields
        missing_allocation_reasons: List[str] = []
        if incompatible_cat_act_msg:
            missing_allocation_reasons.append(incompatible_cat_act_msg)
        if final_stream == "abn":
            if final_activity not in ("dog_walking", "printing_3d") and not incompatible_cat_act_msg:
                missing_allocation_reasons.append(
                    "Missing ABN business_activity — please select dog_walking or printing_3d"
                )
            if final_pct <= 0.0:
                missing_allocation_reasons.append(
                    "Missing ABN business_pct — please select deductible business percentage"
                )
        elif final_stream == "payg":
            if final_pct <= 0.0:
                missing_allocation_reasons.append(
                    "Missing PAYG business_pct — please select deductible work percentage"
                )

        # Effective deductible percentage is 0.0 while required activity/pct allocation is incomplete
        effective_pct = final_pct if not missing_allocation_reasons else 0.0
        amounts = self._calculate_tax_and_claim(
            total=total,
            raw_tax=preserved_raw_tax,
            gst_status=preserved_gst_status,
            currency=currency,
            purpose=final_purpose,
            business_pct=effective_pct,
            category=final_cat,
        )
        new_tax = amounts["tax_amount"]
        new_raw_tax = amounts["raw_tax_amount"]
        new_gst_status = amounts["gst_status"]
        new_claimable = amounts["claimable_amount"]

        # 2. Field-level provenance & uncertainty preservation:
        # User confirmation of vendor/date/gst/payment is NOT confirmation of category/stream/activity/pct,
        # and a category-only (or partial allocation) correction must NOT confirm unselected activity/pct fields.
        classification_fields_touched = any(
            val is not None
            for val in (category, purpose, claim_stream, business_activity, business_pct, tax_label)
        )
        is_category_only_correction = (
            cat_explicit and not act_explicit and not pct_explicit and not stream_explicit
        )

        from src.storage.categories import EXCLUSIVE_ABN_ACTIVITY_BY_CATEGORY

        if final_stream == "abn":
            act_confirmed = (
                ("activity" in confirmed_fields)
                or act_explicit
                or (final_cat in EXCLUSIVE_ABN_ACTIVITY_BY_CATEGORY)
                or (
                    prev_stream == "abn"
                    and prev_conf >= 0.80
                    and cur_act in ("dog_walking", "printing_3d")
                    and not stream_changed
                    and not is_category_only_correction
                )
            )
            pct_confirmed = (
                ("pct" in confirmed_fields)
                or pct_explicit
                or (
                    prev_stream == "abn"
                    and prev_conf >= 0.80
                    and cur_pct > 0.0
                    and not stream_changed
                    and not is_category_only_correction
                )
            )
            if act_explicit:
                confirmed_fields.add("activity")
            if pct_explicit:
                confirmed_fields.add("pct")
            all_allocation_confirmed = (
                not missing_allocation_reasons
                and act_confirmed
                and pct_confirmed
                and (classification_fields_touched or was_classification_confirmed)
            )
        elif final_stream == "payg":
            pct_confirmed = (
                ("pct" in confirmed_fields)
                or pct_explicit
                or (
                    prev_stream == "payg"
                    and prev_conf >= 0.80
                    and cur_pct > 0.0
                    and not stream_changed
                    and not is_category_only_correction
                )
            )
            if pct_explicit:
                confirmed_fields.add("pct")
            all_allocation_confirmed = (
                not missing_allocation_reasons
                and pct_confirmed
                and (classification_fields_touched or was_classification_confirmed)
            )
        else:  # final_stream == "personal"
            all_allocation_confirmed = (
                not missing_allocation_reasons
                and (classification_fields_touched or was_classification_confirmed)
            )

        if missing_allocation_reasons:
            candidate_source = "ai_suggestion"
            candidate_conf = min(prev_conf, 0.50)
            candidate_uncertainty = "; ".join(missing_allocation_reasons)
        elif classification_fields_touched:
            if all_allocation_confirmed and not is_category_only_correction:
                candidate_source = "owner_correction"
                candidate_conf = 1.0
                candidate_uncertainty = None
            elif is_category_only_correction:
                if was_classification_confirmed and ("activity" in confirmed_fields and "pct" in confirmed_fields):
                    candidate_source = "owner_correction"
                    candidate_conf = 1.0
                    candidate_uncertainty = None
                else:
                    candidate_source = prev_source if prev_source in ("owner_correction", "vendor_memory") else "ai_suggestion"
                    candidate_conf = min(prev_conf, 0.90) if prev_conf >= 0.80 else prev_conf
                    candidate_uncertainty = prev_uncertainty
            else:
                candidate_source = prev_source if prev_source != "owner_correction" else "ai_suggestion"
                candidate_conf = min(prev_conf, 0.75)
                candidate_uncertainty = prev_uncertainty
        else:
            candidate_source = prev_source
            candidate_conf = prev_conf
            candidate_uncertainty = prev_uncertainty

        # 3. Revalidate the complete candidate record and reconcile unresolved QA/validation flags
        items_for_qa = None
        if current.get("items_json"):
            try:
                parsed_items = json.loads(current["items_json"])
                if isinstance(parsed_items, list) and parsed_items:
                    items_for_qa = parsed_items
            except Exception:
                items_for_qa = None

        other_receipts = None
        old_flag_reason = current.get("flag_reason") or ""
        file_hash = current.get("file_hash")
        if file_hash or "duplicate" in old_flag_reason.lower():
            cur_others = self.main_conn.execute(
                "SELECT id, file_hash, vendor, total_amount, date, status, flag_reason FROM receipts WHERE id != ?",
                (receipt_id,),
            )
            other_receipts = []
            for row in cur_others:
                # A later upload held as a possible copy must not put the original on hold.
                if (
                    row["id"] > receipt_id
                    and row["status"] == "flagged"
                    and row["file_hash"] != file_hash
                    and any(
                        likely_duplicate_receipt_id(clause) == receipt_id
                        for clause in (row["flag_reason"] or "").split(";")
                    )
                ):
                    continue
                other_receipts.append(dict(row))

        qa_res = self.qa.inspect_receipt(
            {
                "vendor": new_vendor,
                "date": new_date,
                "total_amount": total,
                "tax_amount": new_raw_tax if new_raw_tax is not None else 0.0,
                "raw_tax_amount": new_raw_tax,
                "currency": currency,
                "items": items_for_qa,
                "file_hash": file_hash,
            },
            existing_receipts=other_receipts,
            retry_count=self.qa.max_retries,
        )

        if distinct_from is not None:
            matched_ids = {likely_duplicate_receipt_id(issue) for issue in qa_res.issues}
            has_identical_file = any(
                receipt.get("file_hash") == current["file_hash"] for receipt in (other_receipts or ())
            )
            if distinct_from not in matched_ids or has_identical_file:
                raise ValueError("This receipt no longer has that possible duplicate match.")
            confirmed_fields.add(decision)
        confirmed_fields_json = json.dumps(sorted(confirmed_fields))

        old_clauses = [c.strip() for c in old_flag_reason.split(";") if c.strip()]
        preserved_clauses: List[str] = []
        for clause in old_clauses:
            c_low = clause.lower()
            if self._is_classification_or_allocation_clause(clause):
                # An allocation/classification uncertainty clause is resolved IF AND ONLY IF:
                # 1. The user explicitly touched allocation fields (category, stream, activity, pct, purpose),
                # 2. All required allocation fields are confirmed (valid activity & positive % for ABN/PAYG),
                # 3. No allocation requirements remain missing, AND
                # 4. It was not a category-only edit on an unconfirmed receipt.
                can_resolve_allocation = (
                    classification_fields_touched
                    and all_allocation_confirmed
                    and not missing_allocation_reasons
                    and not is_category_only_correction
                ) or (
                    is_category_only_correction
                    and was_classification_confirmed
                    and ("activity" in confirmed_fields and "pct" in confirmed_fields)
                )
                if not can_resolve_allocation:
                    preserved_clauses.append(clause)
                continue

            if "vendor name is missing" in c_low or (c_low.startswith("vendor") and "missing" in c_low):
                if vendor is None or not str(new_vendor).strip() or any("vendor" in i.lower() for i in qa_res.issues):
                    preserved_clauses.append(clause)
                continue

            if clause.startswith("Date ") or "in the future" in c_low or "invalid date" in c_low or "date is missing" in c_low or "date must be" in c_low:
                if date is None or any("date" in i.lower() for i in qa_res.issues):
                    preserved_clauses.append(clause)
                continue

            if clause.startswith("GST amount") or "gst" in c_low:
                if tax_amount is None or any("gst" in i.lower() for i in qa_res.issues):
                    preserved_clauses.append(clause)
                continue

            if "likely duplicate" in c_low or "duplicate" in c_low:
                if (vendor is None and date is None) or any("duplicate" in i.lower() for i in qa_res.issues):
                    preserved_clauses.append(clause)
                continue

            # Any other validation/QA flag (e.g. invoice flag, date/invoice flag, file_hash duplicate, item sum mismatch)
            # is NOT resolved by vendor/category/pct/stream/gst edits and must be preserved.
            preserved_clauses.append(clause)

        remaining_issues: List[str] = []
        for c in preserved_clauses + qa_res.issues + missing_allocation_reasons:
            match_id = likely_duplicate_receipt_id(c)
            if match_id is not None and f"distinct_from:{match_id}" in confirmed_fields:
                continue
            if c and c not in remaining_issues:
                remaining_issues.append(c)

        remaining_qa_flags = [
            c for c in remaining_issues
            if not self._is_classification_or_allocation_clause(c)
        ]
        remaining_review_flags = [
            c for c in remaining_issues
            if self._is_classification_or_allocation_clause(c)
        ]

        needs_classification_review = (
            bool(remaining_review_flags)
            or bool(missing_allocation_reasons)
            or (
                candidate_conf < 0.80
                and self._low_confidence_matters(final_stream, final_cat)
                and candidate_source not in ("owner_correction", "vendor_memory")
            )
        )

        if remaining_qa_flags:
            new_status = "flagged"
            new_flag_reason = "; ".join(remaining_issues)
            # Record with unresolved QA/invoice/date flags cannot have 100% confidence
            final_conf = min(candidate_conf, 0.95) if candidate_conf >= 1.0 else candidate_conf
        elif needs_classification_review:
            new_status = "needs_review"
            new_flag_reason = (
                "; ".join(remaining_issues)
                if remaining_issues
                else (candidate_uncertainty or "Low confidence AI classification — needs owner review")
            )
            final_conf = min(candidate_conf, 0.75) if candidate_conf >= 0.80 and candidate_source not in ("owner_correction", "vendor_memory") else candidate_conf
        else:
            new_status = "corrected"
            new_flag_reason = None
            final_conf = 1.0 if candidate_source in ("owner_correction", "vendor_memory") else candidate_conf

        with self.main_conn:
            cursor = self.main_conn.execute(
                """
                UPDATE receipts
                SET vendor = ?,
                    date = ?,
                    category = ?,
                    purpose = ?,
                    claim_stream = ?,
                    business_activity = ?,
                    business_pct = ?,
                    claimable_amount = ?,
                    tax_amount = ?,
                    raw_tax_amount = ?,
                    gst_status = ?,
                    tax_label = ?,
                    payment_method = ?,
                    confidence = ?,
                    classification_source = ?,
                    confirmed_fields = ?,
                    uncertainty_note = ?,
                    status = ?,
                    flag_reason = ?
                WHERE id = ? AND confirmed_fields IS ? AND status IS ? AND flag_reason IS ?
                """,
                (
                    new_vendor,
                    new_date,
                    final_cat,
                    final_purpose,
                    final_stream,
                    final_activity,
                    final_pct,
                    new_claimable,
                    new_tax,
                    new_raw_tax,
                    new_gst_status,
                    final_tax_label,
                    new_payment,
                    final_conf,
                    candidate_source,
                    confirmed_fields_json,
                    candidate_uncertainty,
                    new_status,
                    new_flag_reason,
                    receipt_id,
                    current.get("confirmed_fields"),
                    current.get("status"),
                    current.get("flag_reason"),
                ),
            )
            if not cursor.rowcount:
                raise ValueError("This receipt changed. Reopen it before making another correction.")
            # The receipt and its journal entry commit together.
            self._post_journal_for_receipt(
                receipt_id=receipt_id,
                vendor=new_vendor,
                date=new_date,
                total=total,
                category=final_cat,
                purpose=final_purpose,
                business_pct=effective_pct,
                business_activity=final_activity,
                payment_method=new_payment,
                status=new_status,
                flag_reason=new_flag_reason,
            )

        # Save to Vendor Memory ONLY when all required allocation fields are owner-confirmed and no QA flags remain
        if remember_vendor and all_allocation_confirmed and not remaining_qa_flags and not missing_allocation_reasons:
            self.memory.record_correction(
                raw_vendor=new_vendor,
                category=final_cat,
                purpose=final_purpose,
                tax_label=final_tax_label,
                canonical_name=new_vendor,
                claim_stream=final_stream,
                business_activity=final_activity,
                business_pct=final_pct,
                source="owner_correction",
                confidence=1.0,
            )

        updated = self.get_receipt(receipt_id)
        updated["personal_share"] = amounts["personal_share"]
        return updated

    def _post_journal_for_receipt(
        self,
        receipt_id: int,
        vendor: str,
        date: str,
        total: float,
        category: str,
        purpose: str,
        business_pct: float = 100.0,
        business_activity: str = "printing_3d",
        payment_method: str = "bank",
        status: str = "recorded",
        flag_reason: Optional[str] = None,
    ) -> None:
        """
        Determines the appropriate debit and credit accounts and posts a balanced transaction.
        Supports mixed-use business/personal percentage splits and replaces any prior
        journal transaction for `receipt:<id>` so duplicates can never occur.

        Conservative posting guards:
        1. Only genuinely confirmed transactions ('recorded', 'confirmed', 'corrected') without
           unresolved QA flags or review holds may post to the double-entry journal.
        2. Repayments and internal transfers are excluded from posting until linkage
           and dedicated account information exist to avoid fabricating accounting entries.
        3. BNPL / financing purchases credit liability (2200) instead of immediate bank cash (1000).
        """
        reference = f"receipt:{receipt_id}"

        # Guard 1: Status allowlist & unresolved flags
        GENUINELY_CONFIRMED_STATUSES = {"recorded", "confirmed", "corrected"}
        status_clean = (status or "").lower().strip()
        has_unresolved_flags = bool(flag_reason and str(flag_reason).strip())

        if status_clean not in GENUINELY_CONFIRMED_STATUSES or has_unresolved_flags:
            self.journal.delete_by_reference(reference)
            return

        # Guard 2: Exclude repayments and internal transfers
        cat_lower = (category or "").lower().strip()
        if (
            cat_lower in ("transfer", "internal_transfer", "bnpl_repayment")
            or "transfer" in cat_lower
            or "repayment" in cat_lower
        ):
            self.journal.delete_by_reference(reference)
            return

        act_lower = (business_activity or "").lower().strip()
        self._ensure_account_exists("1000", "Cash in Bank - Everyday", "bank")
        self._ensure_account_exists("5900", "Personal Living Expenses", "personal_drawings")

        if self._is_income_category(cat_lower):
            debit_account = "1000"
            if "dog" in cat_lower or "walk" in cat_lower or act_lower == "dog_walking":
                credit_account = self._ensure_account_exists("4100", "Dog Walking Services Revenue", "business_revenue")
            else:
                credit_account = self._ensure_account_exists("4000", "3D Printing Sales", "business_revenue")
            legs = [
                JournalLeg(account_code=debit_account, debit=total, credit=0.0, description="Receipt via Bank"),
                JournalLeg(account_code=credit_account, debit=0.0, credit=total, description=f"{vendor} ({category})"),
            ]
        else:
            pm_lower = (payment_method or "").lower().strip()
            if any(k in pm_lower for k in ("afterpay", "bnpl", "pay in 4", "zip")):
                credit_account = self._ensure_account_exists("2200", "BNPL / Financing Liability", "liability")
                payment_desc = f"Financed via {payment_method or 'BNPL'}"
            elif any(k in pm_lower for k in ("credit card", "credit")):
                credit_account = self._ensure_account_exists("2000", "Credit Card Liability", "liability")
                payment_desc = f"Payment via {payment_method or 'Credit Card'}"
            else:
                credit_account = "1000"
                payment_desc = "Payment via Bank"

            if purpose == "personal" or business_pct <= 0.0:
                legs = [
                    JournalLeg(account_code="5900", debit=total, credit=0.0, description=f"{vendor} ({category})"),
                    JournalLeg(account_code=credit_account, debit=0.0, credit=total, description=payment_desc),
                ]
            else:
                if "shipping" in cat_lower or "freight" in cat_lower:
                    exp_account = self._ensure_account_exists("5200", "Shipping & Postage", "expense")
                elif "software" in cat_lower or "subscription" in cat_lower:
                    exp_account = self._ensure_account_exists("5300", "Software & Cloud Services", "expense")
                elif "dog" in cat_lower or (act_lower == "dog_walking" and "car" not in cat_lower and "travel" not in cat_lower):
                    exp_account = self._ensure_account_exists("5400", "Dog Walking Supplies", "expense")
                elif "travel" in cat_lower or "parking" in cat_lower or "car" in cat_lower or "fuel" in cat_lower:
                    exp_account = self._ensure_account_exists("5500", "Travel & Parking", "expense")
                elif "tool" in cat_lower or "equipment" in cat_lower:
                    exp_account = self._ensure_account_exists("1300", "Equipment & Tools", "fixed_asset")
                else:
                    exp_account = self._ensure_account_exists("5100", "Materials & Consumables", "expense")

                pct_clamped = max(0.0, min(100.0, float(business_pct)))
                bus_share = round(total * (pct_clamped / 100.0), 2)
                pers_share = round(total - bus_share, 2)

                legs = []
                if bus_share > 0.0:
                    legs.append(
                        JournalLeg(
                            account_code=exp_account,
                            debit=bus_share,
                            credit=0.0,
                            description=f"{vendor} ({category} — {pct_clamped:.0f}% business)",
                        )
                    )
                if pers_share > 0.0:
                    legs.append(
                        JournalLeg(
                            account_code="5900",
                            debit=pers_share,
                            credit=0.0,
                            description=f"{vendor} ({100.0 - pct_clamped:.0f}% personal share)",
                        )
                    )
                legs.append(
                    JournalLeg(
                        account_code=credit_account,
                        debit=0.0,
                        credit=total,
                        description=payment_desc,
                    )
                )

        self.journal.post_transaction(
            tx_date=date,
            description=f"Receipt #{receipt_id}: {vendor}",
            legs=legs,
            source="receipt",
            reference=reference,
            replace_existing=True,
        )
