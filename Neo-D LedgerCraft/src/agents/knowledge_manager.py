"""
Knowledge Manager (Code) for Neo-D LedgerCraft
Files transactions into the books, posts balanced double-entry journal entries,
and updates vendor memory so recurring vendor classifications and user corrections persist.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional
from src.storage.journal import JournalLeg, JournalManager
from src.storage.memory import VendorMemoryManager


class KnowledgeManager:
    def __init__(self, main_conn: sqlite3.Connection):
        self.main_conn = main_conn
        self.journal = JournalManager(main_conn)
        self.memory = VendorMemoryManager(main_conn)

    def record_receipt(
        self,
        data: Dict[str, Any],
        file_hash: Optional[str] = None,
        local_path: Optional[str] = None,
        gdrive_file_id: Optional[str] = None,
        flagged: bool = False,
        flag_reason: Optional[str] = None,
    ) -> int:
        """
        Saves receipt to DB, updates vendor memory, and generates a balanced
        double-entry journal transaction.
        """
        vendor = data["vendor"]
        date = data["date"]
        total = float(data["total_amount"])
        tax = float(data.get("tax_amount", 0.0) or 0.0)
        currency = data.get("currency", "AUD")
        category = data.get("category", "general")
        purpose = data.get("purpose", "business")
        tax_label = data.get("tax_label")
        notes = data.get("notes")

        # 1. Insert into receipts table
        with self.main_conn:
            cursor = self.main_conn.execute(
                """
                INSERT INTO receipts (
                    file_hash, original_filename, local_path, gdrive_file_id,
                    vendor, date, total_amount, tax_amount, currency,
                    category, purpose, tax_label, status, flag_reason, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_hash,
                    data.get("original_filename"),
                    local_path,
                    gdrive_file_id,
                    vendor,
                    date,
                    total,
                    tax,
                    currency,
                    category,
                    purpose,
                    tax_label,
                    "flagged" if flagged else "recorded",
                    flag_reason,
                    notes,
                ),
            )
            receipt_id = cursor.lastrowid

        # 2. Update vendor memory
        self.memory.record_correction(
            raw_vendor=vendor,
            category=category,
            purpose=purpose,
            tax_label=tax_label,
            canonical_name=vendor,
        )

        # 3. Post balanced double-entry journal entry
        self._post_journal_for_receipt(receipt_id, vendor, date, total, category, purpose)

        return receipt_id

    def _post_journal_for_receipt(
        self,
        receipt_id: int,
        vendor: str,
        date: str,
        total: float,
        category: str,
        purpose: str,
    ) -> None:
        """Determines the appropriate debit and credit accounts and posts transaction."""
        cat_lower = (category or "").lower()

        # Check if this is business income (3D printing sales or dog walking revenue)
        if "income" in cat_lower or "revenue" in cat_lower or "sales" in cat_lower:
            debit_account = "1000"  # Cash in Bank - Everyday
            if "dog" in cat_lower or "walk" in cat_lower:
                credit_account = "4100"  # Dog Walking Services Revenue
            else:
                credit_account = "4000"  # 3D Printing Sales
            legs = [
                JournalLeg(account_code=debit_account, debit=total, credit=0.0, description="Receipt via Bank"),
                JournalLeg(account_code=credit_account, debit=0.0, credit=total, description=f"{vendor} ({category})"),
            ]
        else:
            # Expense or asset purchase paid from Cash in Bank (1000)
            credit_account = "1000"
            if purpose == "personal":
                debit_account = "5900"  # Personal Living Expenses
            else:
                if "shipping" in cat_lower or "freight" in cat_lower:
                    debit_account = "5200"  # Shipping & Postage
                elif "software" in cat_lower or "subscription" in cat_lower:
                    debit_account = "5300"  # Software & Cloud
                elif "dog" in cat_lower:
                    debit_account = "5400"  # Dog Walking Supplies
                elif "travel" in cat_lower or "parking" in cat_lower:
                    debit_account = "5500"  # Travel & Parking
                elif "tool" in cat_lower or "equipment" in cat_lower:
                    debit_account = "1300"  # Equipment & Tools
                else:
                    debit_account = "5100"  # Materials & Consumables (includes 3D printing materials)

            legs = [
                JournalLeg(account_code=debit_account, debit=total, credit=0.0, description=f"{vendor} ({category})"),
                JournalLeg(account_code=credit_account, debit=0.0, credit=total, description="Payment via Bank"),
            ]

        self.journal.post_transaction(
            tx_date=date,
            description=f"Receipt #{receipt_id}: {vendor}",
            legs=legs,
            source="receipt",
            reference=f"receipt:{receipt_id}",
        )
