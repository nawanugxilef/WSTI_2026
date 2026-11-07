"""
Vendor Memory for Neo-D LedgerCraft
Learns from owner corrections.
When the owner corrects a vendor's category or purpose, future receipts from that
vendor use the learned rule with confidence 1.0.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class VendorRule:
    canonical_name: str
    default_category: str
    default_purpose: str = "business"
    default_tax_label: Optional[str] = None
    confidence: float = 1.0
    correction_count: int = 1


def normalize_vendor_name(name: str) -> str:
    """Normalizes vendor names for fuzzy/pattern matching (removes punctuation, lowercases)."""
    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    return " ".join(cleaned.split())


class VendorMemoryManager:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def lookup(self, raw_vendor: str) -> Optional[VendorRule]:
        """Looks up a vendor in memory by exact or substring match."""
        norm = normalize_vendor_name(raw_vendor)
        cursor = self.conn.execute(
            """
            SELECT canonical_name, default_category, default_purpose, default_tax_label, confidence, correction_count
            FROM vendor_memory
            WHERE vendor_pattern = ?
            """,
            (norm,),
        )
        row = cursor.fetchone()
        if row:
            return VendorRule(
                canonical_name=row["canonical_name"],
                default_category=row["default_category"],
                default_purpose=row["default_purpose"],
                default_tax_label=row["default_tax_label"],
                confidence=row["confidence"],
                correction_count=row["correction_count"],
            )

        # Check for contained pattern
        cursor = self.conn.execute(
            """
            SELECT vendor_pattern, canonical_name, default_category, default_purpose, default_tax_label, confidence, correction_count
            FROM vendor_memory
            """
        )
        for row in cursor.fetchall():
            pat = row["vendor_pattern"]
            if pat in norm or norm in pat:
                return VendorRule(
                    canonical_name=row["canonical_name"],
                    default_category=row["default_category"],
                    default_purpose=row["default_purpose"],
                    default_tax_label=row["default_tax_label"],
                    confidence=row["confidence"],
                    correction_count=row["correction_count"],
                )
        return None

    def record_correction(
        self,
        raw_vendor: str,
        category: str,
        purpose: str = "business",
        tax_label: Optional[str] = None,
        canonical_name: Optional[str] = None,
    ) -> None:
        """Saves or updates a correction rule for a vendor."""
        norm = normalize_vendor_name(raw_vendor)
        c_name = canonical_name or raw_vendor.strip().title()

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO vendor_memory (
                    vendor_pattern, canonical_name, default_category, 
                    default_purpose, default_tax_label, confidence, correction_count, last_updated
                )
                VALUES (?, ?, ?, ?, ?, 1.0, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(vendor_pattern) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    default_category = excluded.default_category,
                    default_purpose = excluded.default_purpose,
                    default_tax_label = excluded.default_tax_label,
                    confidence = 1.0,
                    correction_count = correction_count + 1,
                    last_updated = CURRENT_TIMESTAMP
                """,
                (norm, c_name, category, purpose, tax_label),
            )

    def get_recent_examples(self, limit: int = 40) -> List[Dict]:
        """Returns recent categorized vendor decisions for few-shot prompting."""
        cursor = self.conn.execute(
            """
            SELECT canonical_name as vendor, default_category as category, default_purpose as purpose, default_tax_label as tax_label
            FROM vendor_memory
            ORDER BY last_updated DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
