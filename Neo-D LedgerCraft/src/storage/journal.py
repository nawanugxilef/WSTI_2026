"""
Double-Entry Journal Engine for Neo-D LedgerCraft
Enforces the fundamental equation: Assets = Liabilities + Equity
Every journal transaction must have total debits == total credits to the cent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


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

    def post_transaction(
        self,
        tx_date: str,
        description: str,
        legs: List[JournalLeg],
        source: str = "receipt",
        reference: Optional[str] = None,
    ) -> int:
        """
        Posts a balanced double-entry transaction.
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

        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO journal_transactions (tx_date, description, reference, source)
                VALUES (?, ?, ?, ?)
                """,
                (tx_date, description, reference, source),
            )
            tx_id = cursor.lastrowid

            for leg in legs:
                self.conn.execute(
                    """
                    INSERT INTO journal_lines (transaction_id, account_code, debit, credit, description)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (tx_id, leg.account_code, leg.debit, leg.credit, leg.description or description),
                )

        return tx_id

    def get_trial_balance(self) -> List[Dict]:
        """Returns the trial balance of all accounts: code, name, type, total_debit, total_credit, balance."""
        query = """
            SELECT 
                a.code,
                a.name,
                a.type,
                COALESCE(SUM(l.debit), 0.0) as total_debit,
                COALESCE(SUM(l.credit), 0.0) as total_credit,
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
        Aggregates deductible business expenses mapped by Australian myTax labels for a financial year.
        """
        query = """
            SELECT 
                tax_label,
                COUNT(*) as count,
                ROUND(SUM(total_amount - tax_amount), 2) as net_amount,
                ROUND(SUM(tax_amount), 2) as total_gst,
                ROUND(SUM(total_amount), 2) as gross_amount
            FROM receipts
            WHERE purpose = 'business'
              AND date >= ? AND date <= ?
              AND tax_label IS NOT NULL
            GROUP BY tax_label
            ORDER BY gross_amount DESC;
        """
        cursor = self.conn.execute(query, (start_date, end_date))
        return [dict(row) for row in cursor.fetchall()]
