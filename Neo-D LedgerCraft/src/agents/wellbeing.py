"""Gentle same-day check-ins. Neo notices a pattern and asks how the owner is; it never scolds."""

import datetime
import re
import sqlite3
from typing import Optional

COFFEE_PATTERN = re.compile(
    r"\b(coffee|kopi|caf[eé]|latte|cappuccino|flat white|long black|espresso|mocha|macchiato|cold brew)\b",
    re.IGNORECASE,
)
COFFEE_CHECK_IN_AT = 3
COFFEE_CHECK_IN = (
    "☕ Neo: That's your third coffee today. Everything okay?\n"
    "If it's a long day, a glass of water or a short walk might help too. 💛"
)


def coffee_check_in(conn: sqlite3.Connection, receipt_id: int, today: datetime.date) -> Optional[str]:
    """Return the check-in when this receipt is today's third coffee.

    Only receipts dated today count, so catching up on old receipts never triggers it.
    It fires once, on the third, so the owner is not asked again with every cup.
    """
    rows = conn.execute(
        "SELECT id, vendor, items_json FROM receipts WHERE date = ? AND status != 'duplicate' ORDER BY id",
        (today.isoformat(),),
    ).fetchall()
    coffee_ids = [row[0] for row in rows if COFFEE_PATTERN.search(f"{row[1]} {row[2] or ''}")]
    if len(coffee_ids) == COFFEE_CHECK_IN_AT and coffee_ids[-1] == receipt_id:
        return COFFEE_CHECK_IN
    return None
