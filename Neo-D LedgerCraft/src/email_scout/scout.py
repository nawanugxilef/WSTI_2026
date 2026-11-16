"""Read receipt emails, mark ones already in the ledger as repeats, and record the rest."""

from __future__ import annotations

import datetime
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable, List, Optional

from src.agents.chief_of_staff import ChiefOfStaff

RECEIPT_WORDS = re.compile(
    r"\b(receipt|tax invoice|invoice|order confirm\w*|your order|payment received|"
    r"payment confirm\w*|purchase|subscription renew\w*|thanks for your order)\b",
    re.IGNORECASE,
)
AMOUNT = re.compile(r"\$\s?\d")
REPEAT_WINDOW_DAYS = 3
MAX_BODY_CHARS = 4000


@dataclass
class Email:
    message_id: str
    sender: str
    subject: str
    date: datetime.date
    body: str


@dataclass
class ScanResult:
    message_id: str
    subject: str
    status: str  # recorded, repeat, not_receipt, needs_review, already_scanned
    detail: str
    receipt_id: Optional[int] = None


def looks_like_receipt(email: Email) -> bool:
    text = f"{email.subject}\n{email.body}"
    return bool(RECEIPT_WORDS.search(text) and AMOUNT.search(text))


def _vendor_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").casefold())


def same_vendor(a: str, b: str) -> bool:
    ka, kb = _vendor_key(a), _vendor_key(b)
    return bool(ka and kb) and (ka in kb or kb in ka)


class EmailScout:
    def __init__(self, cos: ChiefOfStaff):
        self.cos = cos
        self.conn = cos.main_conn
        with self.conn:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS email_scout_seen (
                    message_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    receipt_id INTEGER,
                    scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )

    def find_repeat(self, vendor: str, date: str, total: float) -> Optional[int]:
        """Return the ledger receipt with the same amount and vendor within a few days."""
        rows = self.conn.execute(
            "SELECT id, vendor FROM receipts WHERE ABS(total_amount - ?) < 0.005 "
            "AND ABS(julianday(date) - julianday(?)) <= ? ORDER BY id",
            (float(total), date, REPEAT_WINDOW_DAYS),
        ).fetchall()
        for row in rows:
            if same_vendor(row["vendor"], vendor):
                return int(row["id"])
        return None

    def _remember(self, email: Email, status: str, receipt_id: Optional[int]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO email_scout_seen (message_id, status, receipt_id) VALUES (?, ?, ?)",
                (email.message_id, status, receipt_id),
            )

    def scan_one(self, email: Email) -> ScanResult:
        seen = self.conn.execute(
            "SELECT status, receipt_id FROM email_scout_seen WHERE message_id = ?", (email.message_id,)
        ).fetchone()
        if seen:
            return ScanResult(email.message_id, email.subject, "already_scanned",
                              f"Scanned before ({seen['status']}).", seen["receipt_id"])

        if not looks_like_receipt(email):
            self._remember(email, "not_receipt", None)
            return ScanResult(email.message_id, email.subject, "not_receipt", "No receipt in this email.")

        sender_name = re.sub(r"\s*<[^>]*>", "", email.sender).strip(' "') or email.sender
        text = (
            f"{sender_name} on {email.date.isoformat()}. Email receipt, subject: {email.subject}.\n"
            f"{email.body[:MAX_BODY_CHARS]}"
        )
        parsed = self.cos.finance.parse_note(
            text, vendor_examples=self.cos.km.memory.get_recent_examples(limit=10), capture_date=email.date
        )
        parsed = self.cos._apply_vendor_memory_if_known(parsed)
        missing = self.cos._missing_booking_details(parsed)
        if missing or parsed.currency.strip().upper() != "AUD":
            reason = "Only AUD receipts are recorded." if not missing else f"Missing {', '.join(missing)}."
            self._remember(email, "needs_review", None)
            return ScanResult(email.message_id, email.subject, "needs_review", reason)

        repeat_of = self.find_repeat(parsed.vendor, parsed.date, parsed.total_amount)
        if repeat_of:
            self._remember(email, "repeat", repeat_of)
            return ScanResult(
                email.message_id, email.subject, "repeat",
                f"Repeat: {parsed.vendor} ${parsed.total_amount:.2f} on {parsed.date} is already receipt #{repeat_of}.",
                repeat_of,
            )

        qa = self.cos.qa.inspect_receipt(parsed.model_dump(), retry_count=self.cos.qa.max_retries)
        receipt_id = self.cos.km.record_receipt(
            data=parsed.model_dump(),
            file_hash=None,
            local_path=None,
            gdrive_file_id=None,
            gdrive_status="not_applicable",
            flagged=qa.flagged,
            flag_reason=qa.flag_reason,
        )
        self._remember(email, "recorded", receipt_id)
        return ScanResult(
            email.message_id, email.subject, "recorded",
            f"Recorded: {parsed.vendor} ${parsed.total_amount:.2f} on {parsed.date} as receipt #{receipt_id}.",
            receipt_id,
        )

    def scan(self, emails: Iterable[Email]) -> List[ScanResult]:
        return [self.scan_one(email) for email in emails]
