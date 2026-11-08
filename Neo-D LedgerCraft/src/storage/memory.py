"""
Vendor Memory for Neo-D LedgerCraft
Learns strictly from user-confirmed corrections.
Only owner-confirmed rules (source='owner_correction', confidence=1.0) are returned
by lookup() to auto-classify future receipts from the same vendor.
Unconfirmed AI or heuristic guesses are never promoted to confidence 1.0 rules.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.storage.categories import resolve_allocation


@dataclass
class VendorRule:
    canonical_name: str
    default_category: str
    default_purpose: str = "business"
    default_claim_stream: str = "abn"
    default_activity: str = "printing_3d"
    default_business_pct: float = 100.0
    default_tax_label: Optional[str] = None
    confidence: float = 1.0
    source: str = "owner_correction"
    correction_count: int = 1


def normalize_vendor_name(name: str) -> str:
    """Normalizes vendor names for fuzzy/pattern matching (removes punctuation, lowercases)."""
    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    return " ".join(cleaned.split())


class VendorMemoryManager:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _row_to_rule(self, row: sqlite3.Row) -> VendorRule:
        keys = set(row.keys())
        return VendorRule(
            canonical_name=row["canonical_name"],
            default_category=row["default_category"],
            default_purpose=row["default_purpose"],
            default_claim_stream=row["default_claim_stream"] if "default_claim_stream" in keys else ("abn" if row["default_purpose"] == "business" else "personal"),
            default_activity=row["default_activity"] if "default_activity" in keys else ("printing_3d" if row["default_purpose"] == "business" else "none"),
            default_business_pct=float(row["default_business_pct"]) if "default_business_pct" in keys and row["default_business_pct"] is not None else (100.0 if row["default_purpose"] == "business" else 0.0),
            default_tax_label=row["default_tax_label"],
            confidence=float(row["confidence"]),
            source=str(row["source"]) if "source" in keys and row["source"] else "owner_correction",
            correction_count=int(row["correction_count"]),
        )

    def lookup(self, raw_vendor: str, confirmed_only: bool = True) -> Optional[VendorRule]:
        """
        Looks up a vendor in memory by exact or substring match.
        When confirmed_only=True (default), only returns user-confirmed corrections
        (source='owner_correction' and confidence >= 1.0).
        """
        if not raw_vendor:
            return None
        norm = normalize_vendor_name(raw_vendor)
        if not norm:
            return None

        where_confirmed = "AND COALESCE(source, 'owner_correction') = 'owner_correction' AND confidence >= 1.0" if confirmed_only else ""

        cursor = self.conn.execute(
            f"""
            SELECT canonical_name, default_category, default_purpose, default_claim_stream,
                   default_activity, default_business_pct, default_tax_label, confidence, source, correction_count
            FROM vendor_memory
            WHERE vendor_pattern = ? {where_confirmed}
            """,
            (norm,),
        )
        row = cursor.fetchone()
        if row:
            return self._row_to_rule(row)

        cursor = self.conn.execute(
            f"""
            SELECT vendor_pattern, canonical_name, default_category, default_purpose,
                   default_claim_stream, default_activity, default_business_pct,
                   default_tax_label, confidence, source, correction_count
            FROM vendor_memory
            WHERE 1=1 {where_confirmed}
            """
        )
        for row in cursor.fetchall():
            pat = row["vendor_pattern"]
            if pat and (pat in norm or norm in pat):
                return self._row_to_rule(row)
        return None

    def record_correction(
        self,
        raw_vendor: str,
        category: str,
        purpose: str = "business",
        tax_label: Optional[str] = None,
        canonical_name: Optional[str] = None,
        claim_stream: Optional[str] = None,
        business_activity: Optional[str] = None,
        business_pct: Optional[float] = None,
        source: str = "owner_correction",
        confidence: float = 1.0,
    ) -> None:
        """
        Saves or updates an owner-confirmed correction rule for a vendor.
        Only source='owner_correction' rules are treated as confirmed (confidence=1.0).
        """
        if source != "owner_correction":
            return

        norm = normalize_vendor_name(raw_vendor)
        c_name = canonical_name or raw_vendor.strip()
        alloc = resolve_allocation(
            category=category,
            purpose=purpose,
            claim_stream=claim_stream,
            business_activity=business_activity,
            business_pct=business_pct,
            tax_label=tax_label,
        )

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO vendor_memory (
                    vendor_pattern, canonical_name, default_category, 
                    default_purpose, default_claim_stream, default_activity,
                    default_business_pct, default_tax_label, confidence, source, correction_count, last_updated
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'owner_correction', 1, CURRENT_TIMESTAMP)
                ON CONFLICT(vendor_pattern) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    default_category = excluded.default_category,
                    default_purpose = excluded.default_purpose,
                    default_claim_stream = excluded.default_claim_stream,
                    default_activity = excluded.default_activity,
                    default_business_pct = excluded.default_business_pct,
                    default_tax_label = excluded.default_tax_label,
                    confidence = 1.0,
                    source = 'owner_correction',
                    correction_count = correction_count + 1,
                    last_updated = CURRENT_TIMESTAMP
                """,
                (
                    norm,
                    c_name,
                    str(alloc["category"]),
                    str(alloc["purpose"]),
                    str(alloc["claim_stream"]),
                    str(alloc["business_activity"]),
                    float(alloc["business_pct"]),
                    alloc["tax_label"],
                    float(confidence),
                ),
            )

    def get_recent_examples(self, limit: int = 40) -> List[Dict]:
        """Returns recent owner-confirmed vendor decisions for few-shot prompting."""
        cursor = self.conn.execute(
            """
            SELECT canonical_name as vendor,
                   default_category as category,
                   default_purpose as purpose,
                   default_claim_stream as claim_stream,
                   default_activity as business_activity,
                   default_business_pct as business_pct,
                   default_tax_label as tax_label
            FROM vendor_memory
            WHERE COALESCE(source, 'owner_correction') = 'owner_correction'
            ORDER BY last_updated DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
