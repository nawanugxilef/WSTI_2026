"""
QA Agent (Rule-based) for Neo-D LedgerCraft
Checks the Finance agent's output: missing fields, tax calculations, date sanity,
duplicate detection, currency anomalies, and feeds back issues for re-reading (max 2 retries).
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.storage.categories import load_category_catalog


@dataclass
class QACheckResult:
    passed: bool
    issues: List[str] = field(default_factory=list)
    flagged: bool = False
    flag_reason: Optional[str] = None


class QAAgent:
    def __init__(self, gst_rate: float = 0.10, max_retries: int = 2):
        self.gst_rate = gst_rate
        self.max_retries = max_retries
        self.catalog = load_category_catalog()

    def inspect_receipt(
        self,
        data: Dict[str, Any],
        existing_receipts: Optional[List[Dict[str, Any]]] = None,
        retry_count: int = 0,
    ) -> QACheckResult:
        """
        Runs comprehensive rule-based QA on the parsed receipt data.
        Returns QACheckResult with issues or approval.
        """
        issues = []

        # 1. Mandatory Fields & Category Check
        vendor = data.get("vendor")
        if not vendor or not str(vendor).strip():
            issues.append("Vendor name is missing.")

        if data.get("category") is not None:
            cat_str = str(data.get("category")).strip().lower()
            if not cat_str or cat_str not in self.catalog:
                issues.append(f"Unsupported category '{data.get('category')}'.")

        total_raw = data.get("total_amount")
        total: Optional[float] = None
        if total_raw is None or isinstance(total_raw, bool):
            issues.append("Total amount must be greater than 0.")
        else:
            try:
                t_val = float(total_raw)
                if not math.isfinite(t_val) or t_val <= 0:
                    issues.append("Total amount must be greater than 0.")
                else:
                    total = t_val
            except (TypeError, ValueError):
                issues.append("Total amount must be greater than 0.")

        # 2. Date Verification
        date_str = data.get("date")
        if not date_str:
            issues.append("Transaction date is missing.")
        else:
            try:
                tx_date = datetime.date.fromisoformat(str(date_str).strip())
                try:
                    today = datetime.datetime.now(ZoneInfo("Australia/Sydney")).date()
                except ZoneInfoNotFoundError:
                    today = datetime.date.today()
                if tx_date > today:
                    issues.append(f"Date {tx_date} is in the future.")

                # Check if day and month might be swapped (e.g. DD/MM vs MM/DD)
                days_ago = (today - tx_date).days
                if days_ago > 90:
                    # Try swapping day and month if valid
                    try:
                        swapped = datetime.date(tx_date.year, tx_date.day, tx_date.month)
                        swapped_days = (today - swapped).days
                        if 0 <= swapped_days < 30:
                            issues.append(
                                f"Date {date_str} may have day and month swapped (did you mean {swapped.isoformat()}?)."
                            )
                    except ValueError:
                        pass
            except ValueError:
                issues.append(f"Invalid date format: '{date_str}'. Expected YYYY-MM-DD.")

        # 3. Currency & Tax Checks (Strictly AUD only)
        currency = str(data.get("currency", "AUD")).upper()
        raw_tax_val = data.get("raw_tax_amount") if "raw_tax_amount" in data else data.get("tax_amount")
        tax: Optional[float] = 0.0
        if raw_tax_val is not None:
            if isinstance(raw_tax_val, bool):
                issues.append("GST amount must be a finite number.")
                tax = None
            else:
                try:
                    t_num = float(raw_tax_val)
                    if not math.isfinite(t_num):
                        issues.append("GST amount must be a finite number.")
                        tax = None
                    else:
                        tax = t_num
                except (TypeError, ValueError):
                    issues.append("GST amount must be a finite number.")
                    tax = None

        if currency != "AUD":
            issues.append(f"Unsupported currency '{currency}'. All transactions must be in AUD.")
        elif tax is not None:
            if tax < 0.0:
                issues.append(f"GST amount (${tax:.2f}) cannot be negative.")
            if total is not None and total > 0 and tax > total:
                issues.append(f"GST amount (${tax:.2f}) cannot exceed receipt total (${total:.2f}).")
            # Australian GST should not exceed 10% (tax <= total / 11 + margin)
            if total and total > 0:
                max_expected_gst = round(total / 11.0 + 0.10, 2)
                if tax > max_expected_gst:
                    issues.append(
                        f"GST amount (${tax:.2f}) exceeds expected maximum 10% share for total ${total:.2f} (max ~${max_expected_gst:.2f})."
                    )

        # 4. Line Items Sum Check
        items = data.get("items")
        if items and isinstance(items, list):
            items_sum = round(sum(float(i.get("amount", 0.0)) for i in items), 2)
            if total and abs(items_sum - total) > 0.05:
                issues.append(
                    f"Sum of item lines (${items_sum:.2f}) does not match receipt total (${total:.2f})."
                )

        # 5. Duplicate Check
        file_hash = data.get("file_hash")
        if existing_receipts:
            for rec in existing_receipts:
                if file_hash and rec.get("file_hash") == file_hash:
                    issues.append(f"Duplicate receipt: identical file hash already recorded (id={rec.get('id')}).")
                    break

                # Vendor + date + exact amount within 3 days
                if (
                    rec.get("vendor", "").lower() == str(vendor).lower()
                    and abs(float(rec.get("total_amount", 0.0)) - float(total or 0.0)) < 0.01
                ):
                    rec_date = rec.get("date")
                    if rec_date and date_str:
                        try:
                            d1 = datetime.date.fromisoformat(str(rec_date))
                            d2 = datetime.date.fromisoformat(str(date_str))
                            if abs((d1 - d2).days) <= 3:
                                issues.append(
                                    f"Likely duplicate: matching vendor '{vendor}' and amount ${total} within 3 days of existing receipt #{rec.get('id')}."
                                )
                        except Exception:
                            pass

        # 6. Retry vs Flagging Logic
        if not issues:
            return QACheckResult(passed=True, issues=[], flagged=False)

        # If retried max times, flag it instead of blocking
        if retry_count >= self.max_retries:
            return QACheckResult(
                passed=False,
                issues=issues,
                flagged=True,
                flag_reason="; ".join(issues),
            )

        return QACheckResult(passed=False, issues=issues, flagged=False)
