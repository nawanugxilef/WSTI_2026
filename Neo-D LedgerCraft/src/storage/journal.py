"""
Double-Entry Journal Engine for Neo-D LedgerCraft
Enforces the fundamental equation: Assets = Liabilities + Equity
Every journal transaction must have total debits == total credits to the cent.
Supports idempotent reposting when a receipt's category, stream, or business % is corrected,
and segregates needs-review expenses from confirmed myTax claim totals.
"""

from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.storage import businesses as biz


def _sql_activity_list() -> str:
    # Keys are preset names or custom_[a-z0-9_]+ (validated in businesses.py), so inlining is safe.
    keys = biz.known_activity_keys()
    assert all(k.replace("_", "").isalnum() for k in keys)
    return ", ".join(f"'{k}'" for k in keys)


@dataclass
class JournalLeg:
    account_code: str
    debit: float = 0.0
    credit: float = 0.0
    description: Optional[str] = None


class JournalError(Exception):
    """Raised when double-entry rules are violated."""
    pass


class JournalManager:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def delete_by_reference(self, reference: str) -> int:
        """
        Deletes any existing journal transaction(s) and their lines for a given reference
        (e.g., 'receipt:12') so recalculations never produce duplicate journal entries.
        """
        if not reference:
            return 0
        with nullcontext() if self.conn.in_transaction else self.conn:
            return self._delete_by_reference(reference)

    def _delete_by_reference(self, reference: str) -> int:
        self.conn.execute(
            "DELETE FROM journal_lines WHERE transaction_id IN "
            "(SELECT id FROM journal_transactions WHERE reference = ?)",
            (reference,),
        )
        cursor = self.conn.execute("DELETE FROM journal_transactions WHERE reference = ?", (reference,))
        return cursor.rowcount

    def post_transaction(
        self,
        tx_date: str,
        description: str,
        legs: List[JournalLeg],
        source: str = "receipt",
        reference: Optional[str] = None,
        replace_existing: bool = True,
    ) -> int:
        """
        Posts a balanced double-entry transaction.
        If replace_existing is True and reference is provided, removes any prior
        transaction with the same reference to prevent duplicate entries.
        Raises JournalError if debits and credits do not balance to within 0.001.
        """
        if not legs:
            raise JournalError("Transaction must have at least one debit and one credit leg.")

        total_debit = round(sum(leg.debit for leg in legs), 2)
        total_credit = round(sum(leg.credit for leg in legs), 2)

        if abs(total_debit - total_credit) > 0.001:
            raise JournalError(
                f"Transaction does not balance: Debits={total_debit:.2f}, Credits={total_credit:.2f} (diff={total_debit - total_credit:.2f})"
            )

        with nullcontext() if self.conn.in_transaction else self.conn:
            if replace_existing and reference:
                self._delete_by_reference(reference)
            cursor = self.conn.execute(
                """
                INSERT INTO journal_transactions (tx_date, description, reference, source)
                VALUES (?, ?, ?, ?)
                """,
                (tx_date, description, reference, source),
            )
            tx_id = cursor.lastrowid

            for leg in legs:
                if leg.debit == 0.0 and leg.credit == 0.0:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO journal_lines (transaction_id, account_code, debit, credit, description)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (tx_id, leg.account_code, round(leg.debit, 2), round(leg.credit, 2), leg.description or description),
                )

        return tx_id

    def get_trial_balance(self) -> List[Dict]:
        """Returns the trial balance of all accounts: code, name, type, total_debit, total_credit, balance."""
        query = """
            SELECT 
                a.code,
                a.name,
                a.type,
                ROUND(COALESCE(SUM(l.debit), 0.0), 2) as total_debit,
                ROUND(COALESCE(SUM(l.credit), 0.0), 2) as total_credit,
                ROUND(COALESCE(SUM(l.debit), 0.0) - COALESCE(SUM(l.credit), 0.0), 2) as net_balance
            FROM accounts a
            LEFT JOIN journal_lines l ON a.code = l.account_code
            GROUP BY a.code, a.name, a.type
            ORDER BY a.code ASC;
        """
        cursor = self.conn.execute(query)
        rows = [dict(row) for row in cursor.fetchall()]
        return rows

    def get_mytax_summary(self, start_date: str, end_date: str) -> List[Dict]:
        """
        Aggregates CONFIRMED suggested deductible business/PAYG expenses mapped by
        Australian myTax labels for a financial year, scaled by each receipt's business_pct.
        Strictly excludes needs-review/flagged items (status IN ('flagged', 'needs_review')),
        personal expenses, income rows, and duplicate receipts.
        """
        query = f"""
            SELECT 
                tax_label,
                COALESCE(claim_stream, 'abn') as claim_stream,
                'confirmed' as review_state,
                COUNT(*) as count,
                ROUND(SUM((total_amount * (COALESCE(business_pct, 100.0) / 100.0)) - tax_amount), 2) as net_amount,
                ROUND(SUM(tax_amount), 2) as total_gst,
                ROUND(SUM(total_amount * (COALESCE(business_pct, 100.0) / 100.0)), 2) as gross_amount
            FROM receipts
            WHERE purpose = 'business'
              AND status IN ('recorded', 'corrected')
              AND COALESCE(TRIM(flag_reason), '') = ''
              AND COALESCE(confidence, 0.85) >= 0.80
              AND COALESCE(tax_amount, 0.0) >= 0.0
              AND COALESCE(tax_amount, 0.0) <= ROUND((total_amount * (COALESCE(business_pct, 0.0) / 100.0)) / 11.0 + 0.10, 2)
              AND COALESCE(business_pct, 0.0) > 0
              AND (COALESCE(claim_stream, 'abn') != 'abn' OR business_activity IN ({_sql_activity_list()}))
              AND (COALESCE(claim_stream, 'abn') != 'payg' OR business_activity = 'payg_work')
              AND date >= ? AND date <= ?
              AND tax_label IS NOT NULL
              AND LOWER(category) NOT LIKE '%income%'
              AND LOWER(category) NOT LIKE '%revenue%'
              AND LOWER(category) NOT LIKE '%sales%'
              AND LOWER(COALESCE(flag_reason, '')) NOT LIKE '%duplicate%'
            GROUP BY tax_label, COALESCE(claim_stream, 'abn')
            ORDER BY gross_amount DESC;
        """
        cursor = self.conn.execute(query, (start_date, end_date))
        return [dict(row) for row in cursor.fetchall()]

    def get_mytax_review_summary(self, start_date: str, end_date: str) -> List[Dict]:
        """
        Returns segregated NEEDS-REVIEW business/PAYG items (status IN ('flagged', 'needs_review'))
        that are held out of confirmed myTax claim totals until reviewed or corrected by the owner.
        """
        query = """
            SELECT 
                tax_label,
                COALESCE(claim_stream, 'abn') as claim_stream,
                'needs_review' as review_state,
                COUNT(*) as count,
                ROUND(SUM((total_amount * (COALESCE(business_pct, 100.0) / 100.0)) - tax_amount), 2) as net_amount,
                ROUND(SUM(tax_amount), 2) as total_gst,
                ROUND(SUM(total_amount * (COALESCE(business_pct, 100.0) / 100.0)), 2) as gross_amount
            FROM receipts
            WHERE purpose = 'business'
              AND status IN ('flagged', 'needs_review')
              AND COALESCE(business_pct, 100.0) > 0
              AND date >= ? AND date <= ?
              AND tax_label IS NOT NULL
              AND LOWER(category) NOT LIKE '%income%'
              AND LOWER(category) NOT LIKE '%revenue%'
              AND LOWER(category) NOT LIKE '%sales%'
              AND LOWER(COALESCE(flag_reason, '')) NOT LIKE '%duplicate%'
            GROUP BY tax_label, COALESCE(claim_stream, 'abn')
            ORDER BY gross_amount DESC;
        """
        cursor = self.conn.execute(query, (start_date, end_date))
        return [dict(row) for row in cursor.fetchall()]

    def get_unallocated_review_summary(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Returns a summary of pending review / flagged items that cannot be evaluated for myTax
        deductions because their allocation is incomplete (e.g., 0% business share, missing ABN activity,
        or unallocated claim stream).
        Never computes or confirms fictitious deduction numbers for unallocated receipts.
        """
        query = f"""
            SELECT id, vendor, date, total_amount, category, claim_stream, business_activity, business_pct, flag_reason
            FROM receipts
            WHERE status IN ('flagged', 'needs_review')
              AND LOWER(COALESCE(flag_reason, '')) NOT LIKE '%duplicate%'
              AND LOWER(category) NOT LIKE '%income%'
              AND LOWER(category) NOT LIKE '%revenue%'
              AND LOWER(category) NOT LIKE '%sales%'
              AND date >= ? AND date <= ?
              AND (
                  COALESCE(business_pct, 0.0) <= 0
                  OR tax_label IS NULL
                  OR (COALESCE(claim_stream, 'personal') = 'abn' AND business_activity NOT IN ({_sql_activity_list()}))
              )
            ORDER BY id DESC;
        """
        cur = self.conn.execute(query, (start_date, end_date))
        rows = [dict(r) for r in cur.fetchall()]
        return {
            "count": len(rows),
            "receipt_ids": [r["id"] for r in rows],
            "items": rows,
        }

    def get_pnl_summary(self, start_date: str = "2026-07-01", end_date: str = "2027-06-30") -> Dict[str, Any]:
        """
        Computes financial year P&L (Profit & Loss) for Australian ABN business activities
        (Dog Walking and 3D Printing) directly from live ledger receipts.
        Strictly in AUD ($ AUD).
        Uses an explicit allowlist of genuinely confirmed statuses ('recorded', 'confirmed', 'corrected')
        with no unresolved QA flags. Strictly excludes error, duplicate, flagged, and pending items
        from confirmed profit totals, reporting unverified income in pending review.
        """
        cur = self.conn.execute(
            """
            SELECT id, total_amount, category, claim_stream, business_activity, business_pct, status, flag_reason, file_hash
            FROM receipts
            WHERE date >= ? AND date <= ?
              AND LOWER(COALESCE(flag_reason, '')) NOT LIKE '%duplicate%'
              AND LOWER(COALESCE(flag_reason, '')) NOT LIKE '%error%'
              AND LOWER(COALESCE(status, '')) NOT IN ('duplicate', 'error')
            """,
            (start_date, end_date),
        )
        rows = [dict(r) for r in cur.fetchall()]

        GENUINELY_CONFIRMED_STATUSES = {"recorded", "confirmed", "corrected"}
        PENDING_REVIEW_STATUSES = {"flagged", "needs_review", "pending", "unverified"}

        keys = biz.abn_activity_keys()
        buckets = {k: {"income": 0.0, "expenses": 0.0, "pending_income": 0.0} for k in keys}

        seen_income_hashes = set()

        for r in rows:
            status = (r.get("status") or "recorded").strip().lower()
            flag_reason = (r.get("flag_reason") or "").strip().lower()

            # Completely discard duplicates or error records
            if "duplicate" in flag_reason or "error" in flag_reason or status in ("duplicate", "error"):
                continue

            cat = (r.get("category") or "").lower()
            act = (r.get("business_activity") or "").lower()
            is_inc = any(k in cat for k in ("income", "revenue", "sales"))
            amt = float(r.get("total_amount") or 0.0)
            pct = float(r.get("business_pct") or 0.0)

            # Deduplicate income by file_hash if present
            if is_inc:
                f_hash = r.get("file_hash")
                if f_hash:
                    if f_hash in seen_income_hashes:
                        continue
                    seen_income_hashes.add(f_hash)

            # Strictly evaluate genuinely confirmed status
            is_confirmed = (
                status in GENUINELY_CONFIRMED_STATUSES
                and not bool(flag_reason)
                and status not in ("flagged", "needs_review", "pending", "unverified", "error", "duplicate")
            )
            is_pending = (
                status in PENDING_REVIEW_STATUSES
                or bool(flag_reason)
                or status not in GENUINELY_CONFIRMED_STATUSES
            )

            key = biz.business_for(act, cat)
            if is_inc:
                if is_confirmed:
                    buckets[key]["income"] += amt
                elif is_pending:
                    buckets[key]["pending_income"] += amt
            else:
                claim = round(amt * (pct / 100.0), 2) if (is_confirmed and pct > 0) else 0.0
                if claim > 0 and (act in keys or "dog" in cat or r.get("claim_stream") == "abn"):
                    buckets[key]["expenses"] += claim

        for bucket in buckets.values():
            for field in ("income", "expenses", "pending_income"):
                bucket[field] = round(bucket[field], 2)
            bucket["net"] = round(bucket["income"] - bucket["expenses"], 2)

        result: Dict[str, Any] = {
            "period": f"{start_date} to {end_date}",
            "currency": "AUD",
            **buckets,
            "businesses": [
                {"key": b.key, "name": b.name, "icon": b.icon, **buckets[b.key]} for b in biz.active_businesses()
            ],
            "total_income": round(sum(v["income"] for v in buckets.values()), 2),
            "total_expenses": round(sum(v["expenses"] for v in buckets.values()), 2),
            "net_profit": round(sum(v["net"] for v in buckets.values()), 2),
            "pending_income": round(sum(v["pending_income"] for v in buckets.values()), 2),
        }
        return result

    def get_spending_summary(self, month: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns a monthly spending breakdown by category, business claimable share,
        and personal living expenses for a given YYYY-MM month (strictly in AUD).
        Excludes genuine internal account transfers and loan/BNPL repayments from consumption spending.
        Applies a strict confirmed-status allowlist ('recorded', 'confirmed', 'corrected') with no
        unresolved flags. Strictly excludes duplicate and error records.
        Segregates unconfirmed / pending review expenses without misattributing them to personal living.
        """
        import datetime
        m_str = month if month and len(month) == 7 and month[4] == "-" else datetime.date.today().strftime("%Y-%m")

        cur = self.conn.execute(
            """
            SELECT id, total_amount, category, purpose, claim_stream, business_pct, status, flag_reason
            FROM receipts
            WHERE date LIKE ?
              AND LOWER(COALESCE(status, '')) NOT IN ('duplicate', 'error')
              AND LOWER(COALESCE(flag_reason, '')) NOT LIKE '%duplicate%'
              AND LOWER(COALESCE(flag_reason, '')) NOT LIKE '%error%'
              AND LOWER(category) NOT LIKE '%income%'
              AND LOWER(category) NOT LIKE '%revenue%'
              AND LOWER(category) NOT LIKE '%sales%'
              AND LOWER(category) NOT IN ('transfer', 'internal_transfer', 'bnpl_repayment')
              AND LOWER(category) NOT LIKE '%transfer%'
              AND LOWER(category) NOT LIKE '%repayment%'
            """,
            (f"{m_str}%",),
        )
        rows = [dict(r) for r in cur.fetchall()]

        GENUINELY_CONFIRMED_STATUSES = {"recorded", "confirmed", "corrected"}

        total_spent = 0.0
        business_claimable = 0.0
        personal_share = 0.0
        confirmed_count = 0
        cat_totals: Dict[str, float] = {}
        pending_review_spending = 0.0
        pending_review_count = 0

        for r in rows:
            amt = round(float(r.get("total_amount") or 0.0), 2)
            pct = float(r.get("business_pct") or 0.0)
            status = (r.get("status") or "recorded").lower().strip()
            flag_reason = (r.get("flag_reason") or "").strip()
            purpose = (r.get("purpose") or "business").lower().strip()

            # Extra safeguard against error or duplicate
            if status in ("duplicate", "error") or "duplicate" in flag_reason.lower() or "error" in flag_reason.lower():
                continue

            # Separate pending review without labeling it personal!
            is_confirmed = (status in GENUINELY_CONFIRMED_STATUSES and not flag_reason)
            if not is_confirmed:
                pending_review_spending = round(pending_review_spending + amt, 2)
                pending_review_count += 1
                continue

            if purpose == "personal" or pct <= 0.0:
                claim = 0.0
                pers = amt
            else:
                pct_clamped = max(0.0, min(100.0, pct))
                claim = round(amt * (pct_clamped / 100.0), 2)
                pers = round(amt - claim, 2)

            total_spent = round(total_spent + amt, 2)
            business_claimable = round(business_claimable + claim, 2)
            personal_share = round(personal_share + pers, 2)
            confirmed_count += 1

            c_name = r.get("category") or "general"
            cat_totals[c_name] = round(cat_totals.get(c_name, 0.0) + amt, 2)

        sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)
        by_cat = []
        for c, amt in sorted_cats:
            cat_pct = round((amt / total_spent) * 100) if total_spent > 0 else 0
            by_cat.append({"category": c, "amount": amt, "pct": cat_pct})

        # Calculate excluded internal transfers / repayments
        cur_t = self.conn.execute(
            """
            SELECT ROUND(COALESCE(SUM(total_amount), 0.0), 2) as t_total, COUNT(*) as t_count
            FROM receipts
            WHERE date LIKE ?
              AND LOWER(COALESCE(status, '')) NOT IN ('duplicate', 'error')
              AND LOWER(COALESCE(flag_reason, '')) NOT LIKE '%duplicate%'
              AND LOWER(COALESCE(flag_reason, '')) NOT LIKE '%error%'
              AND (
                  LOWER(category) IN ('transfer', 'internal_transfer', 'bnpl_repayment')
                  OR LOWER(category) LIKE '%transfer%'
                  OR LOWER(category) LIKE '%repayment%'
              )
            """,
            (f"{m_str}%",),
        )
        t_row = cur_t.fetchone()
        transfers_excluded = round(float(t_row["t_total"] or 0.0), 2) if t_row else 0.0
        transfers_count = int(t_row["t_count"] or 0) if t_row else 0

        return {
            "month": m_str,
            "currency": "AUD",
            "receipt_count": confirmed_count,
            "total_spent": round(total_spent, 2),
            "business_claimable": round(business_claimable, 2),
            "personal_share": round(personal_share, 2),
            "by_category": by_cat,
            "pending_review_spending": round(pending_review_spending, 2),
            "pending_review_count": pending_review_count,
            "transfers_excluded": transfers_excluded,
            "transfers_count": transfers_count,
        }


