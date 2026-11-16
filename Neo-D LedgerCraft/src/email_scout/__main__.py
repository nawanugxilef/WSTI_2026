"""Run Inbox Scout.

  python -m src.email_scout --demo     fictional inbox, throwaway ledger
  python -m src.email_scout --gmail    owner's Gmail (read-only) into the real ledger
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

from src.agents.chief_of_staff import ChiefOfStaff
from src.email_scout.scout import EmailScout
from src.email_scout.sources import demo_inbox, gmail_inbox
from src.interfaces.cli import load_config, seed_accounts
from src.storage.db import get_connection, init_db

ICON = {"recorded": "✅", "repeat": "🔁", "not_receipt": "·", "needs_review": "✋", "already_scanned": "↩"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Neo-D Inbox Scout")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="Scan the fictional demo inbox into a throwaway ledger")
    mode.add_argument("--gmail", action="store_true", help="Scan your Gmail (read-only) into your Neo ledger")
    parser.add_argument("--limit", type=int, default=25, help="Most emails to read from Gmail")
    parser.add_argument("--db", help="Demo only: keep the throwaway ledger at this path to scan again")
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.demo:
        db_path = args.db or os.path.join(tempfile.mkdtemp(prefix="neo-scout-"), "demo.db")
        fresh = not os.path.exists(db_path)
    else:
        db_path = cfg.get("storage", {}).get("db_path", "./data/ledger.db")
    conn = get_connection(db_path)
    init_db(conn)
    seed_accounts(conn)
    cos = ChiefOfStaff(main_conn=conn)
    scout = EmailScout(cos)

    if args.demo:
        # The owner already snapped this receipt in Telegram, so the email copy must become a repeat.
        if fresh:
            cos.process_note("[FICTIONAL DEMO] Fictional Filament Co PLA filament spools 54.00 on 2026-11-10")
        emails = demo_inbox()
    else:
        token = os.environ.get("GMAIL_TOKEN_FILE")
        if not token or not os.path.exists(token):
            print("Set GMAIL_TOKEN_FILE to your gmail.readonly token. See docs/inbox_scout.md.")
            return 2
        emails = gmail_inbox(token, limit=args.limit)

    results = scout.scan(emails)
    for r in results:
        print(f"{ICON.get(r.status, '?')} {r.subject[:60]}\n    {r.detail}")
    counts = {s: sum(r.status == s for r in results) for s in ("recorded", "repeat", "needs_review", "not_receipt", "already_scanned")}
    print(
        f"\nDone. {counts['recorded']} new, {counts['repeat']} repeat, "
        f"{counts['needs_review']} to check, {counts['not_receipt']} not receipts, {counts['already_scanned']} scanned before."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
