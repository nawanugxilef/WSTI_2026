import json
import sqlite3
from contextlib import closing
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.agents.chief_of_staff import ChiefOfStaff
from src.agents.finance_agent import FinanceAgent
from src.interfaces.cli import seed_accounts
from src.storage.db import init_db
from src.storage.journal import JournalManager


CAPTURE_DATE = date(2026, 11, 11)


@pytest.mark.parametrize("description", ["coffee", "coffe"])
def test_short_manual_expense_is_recorded_with_the_telegram_capture_date(description):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        init_db(conn)
        seed_accounts(conn)
        cos = ChiefOfStaff(main_conn=conn, finance_agent=FinanceAgent(api_key=""))

        # [FICTIONAL DEMO] Match the terse owner input exactly.
        result = cos.process_note(f"{description} 5.5", capture_date=CAPTURE_DATE)

        assert result.status == "recorded"
        assert result.receipt_id > 0
        assert result.date == CAPTURE_DATE.isoformat()
        assert result.total_amount == 5.50
        assert result.category == "dining_coffee"
        assert result.claim_stream == "personal"
        trial_balance = JournalManager(conn).get_trial_balance()
        assert sum(row["total_debit"] for row in trial_balance) == 5.50
        assert sum(row["total_credit"] for row in trial_balance) == 5.50


@pytest.mark.parametrize("text", [
    "[FICTIONAL DEMO] coffee",
    "[FICTIONAL DEMO] coffee 2",
    "[FICTIONAL DEMO] coffee 5.5 and 6.5",
    "[FICTIONAL DEMO] coffee last Friday 5.5",
    "[FICTIONAL DEMO] coffee May 5.5",
    "[FICTIONAL DEMO] coffee 5/11 5.5",
    "[FICTIONAL DEMO] coffee 31/02/2026 5.5",
])
def test_incomplete_or_ambiguous_manual_note_does_not_get_capture_date(text):
    result = FinanceAgent(api_key="").parse_note(text, capture_date=CAPTURE_DATE)
    assert result.date is None


def test_manual_note_preserves_explicit_date_and_non_telegram_default():
    finance = FinanceAgent(api_key="")
    assert finance.parse_note("[FICTIONAL DEMO] coffee 5.5").date is None
    assert finance.parse_note(
        "[FICTIONAL DEMO] coffee 5.5 05/11/2026", capture_date=CAPTURE_DATE
    ).date == "2026-11-05"
    assert finance.parse_note(
        "[FICTIONAL DEMO] coffee 5.5 yesterday", capture_date=CAPTURE_DATE
    ).date == "2026-11-10"


def test_configured_model_cannot_replace_manual_capture_date_or_amount():
    finance = FinanceAgent(api_key="")
    generate = Mock(return_value=SimpleNamespace(text=json.dumps({
        "vendor": "[FICTIONAL DEMO] coffee",
        "date": "2026-11-01",
        "total_amount": 0.25,
        "category": "dining_coffee",
    })))
    finance._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    result = finance.parse_note("[FICTIONAL DEMO] coffee 5.5", capture_date=CAPTURE_DATE)

    generate.assert_called_once()
    instruction = generate.call_args.kwargs["config"].system_instruction
    assert "verified the entry date as 2026-11-11 in Australia/Sydney" in instruction
    assert result.date == CAPTURE_DATE.isoformat()
    assert result.total_amount == 5.50
