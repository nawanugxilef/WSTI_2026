"""Email sources: the fictional demo inbox, or the owner's Gmail (read-only)."""

from __future__ import annotations

import base64
import datetime
import email.utils
import html
import json
import re
from pathlib import Path
from typing import Iterator, List

from src.email_scout.scout import Email

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
# Narrow the search so Gemini only reads likely receipts.
DEFAULT_QUERY = (
    'newer_than:30d (category:purchases OR subject:(receipt OR invoice OR "tax invoice" '
    'OR "order confirmation" OR "your order" OR "payment received"))'
)
DEMO_INBOX = Path(__file__).resolve().parents[2] / "demo" / "sample_emails.json"


def demo_inbox(path: Path = DEMO_INBOX) -> List[Email]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        Email(r["id"], r["from"], r["subject"], datetime.date.fromisoformat(r["date"]), r["body"])
        for r in rows
    ]


def _plain_text(payload: dict) -> str:
    """Prefer text/plain parts; fall back to tag-stripped HTML."""
    plain, rich = [], []

    def walk(part: dict) -> None:
        data = part.get("body", {}).get("data")
        mime = part.get("mimeType", "")
        if data:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            (plain if mime == "text/plain" else rich if mime == "text/html" else []).append(text)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    if plain:
        return "\n".join(plain)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", "\n".join(rich))
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def gmail_inbox(token_file: str, query: str = DEFAULT_QUERY, limit: int = 25) -> Iterator[Email]:
    """Yield recent likely-receipt emails. The token must be a gmail.readonly authorized-user file."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(token_file, scopes=[GMAIL_READONLY])
    session = AuthorizedSession(creds)
    listing = session.get(GMAIL_API, params={"q": query, "maxResults": limit}, timeout=30)
    listing.raise_for_status()
    for ref in listing.json().get("messages", []):
        msg = session.get(f"{GMAIL_API}/{ref['id']}", params={"format": "full"}, timeout=30)
        msg.raise_for_status()
        data = msg.json()
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}
        try:
            sent = email.utils.parsedate_to_datetime(headers.get("date", "")).date()
        except (TypeError, ValueError):
            sent = datetime.datetime.fromtimestamp(int(data.get("internalDate", 0)) / 1000).date()
        yield Email(
            message_id=f"gmail:{data['id']}",
            sender=headers.get("from", ""),
            subject=headers.get("subject", ""),
            date=sent,
            body=_plain_text(data.get("payload", {})),
        )
