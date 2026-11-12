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


@pytest.mark.parametrize(("text", "amount"), [
    ("coffee 5.5", 5.50), ("coffe 5.5", 5.50), ("cofee 5,5", 5.50), ("milktea 7", 7.00),
])
def test_short_manual_expense_is_recorded_with_the_telegram_capture_date(text, amount):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        init_db(conn)
        seed_accounts(conn)
        cos = ChiefOfStaff(main_conn=conn, finance_agent=FinanceAgent(api_key=""))

        # [FICTIONAL DEMO] Match the terse owner input exactly.
        result = cos.process_note(text, capture_date=CAPTURE_DATE)

        assert result.status == "recorded"
        assert result.receipt_id > 0
        assert result.date == CAPTURE_DATE.isoformat()
        assert result.total_amount == amount
        assert result.category == "dining_coffee"
        assert result.claim_stream == "personal"
        trial_balance = JournalManager(conn).get_trial_balance()
        assert sum(row["total_debit"] for row in trial_balance) == amount
        assert sum(row["total_credit"] for row in trial_balance) == amount


@pytest.mark.parametrize(("text", "amount", "category"), [
    ("nasi campur 16", 16, "dining_coffee"),
    ("pork knuckles $20", 20, "dining_coffee"),
    ("baklava $15", 15, "dining_coffee"),
    ("cake 3", 3, "dining_coffee"),
    ("chocolate cake 3", 3, "dining_coffee"),
    ("kue lapis 3", 3, "dining_coffee"),
    ("cake from supermarket $3", 3, "groceries"),
    ("hot dog $8", 8, "dining_coffee"),
    ("milk tea 7", 7, "dining_coffee"),
    ("milk-tea 7", 7, "dining_coffee"),
    ("bubble tea 7", 7, "dining_coffee"),
    ("bubbletea 7", 7, "dining_coffee"),
    ("boba 7", 7, "dining_coffee"),
    ("boba tea 7", 7, "dining_coffee"),
    ("boba milk tea 7", 7, "dining_coffee"),
    ("pearl milk tea 7", 7, "dining_coffee"),
    ("bubble tea from supermarket $7", 7, "groceries"),
    ("baklava from supermarket $15", 15, "groceries"),
    ("dog walking client at cafe $60", 60, "income_dog_walking"),
])
def test_manual_food_note_uses_dish_and_shopping_context(text, amount, category):
    result = FinanceAgent(api_key="").parse_note(text, capture_date=CAPTURE_DATE)
    assert result.total_amount == amount and result.category == category
    assert result.date == CAPTURE_DATE.isoformat()
    assert result.confidence >= 0.8


@pytest.mark.parametrize("text", [
    "[FICTIONAL DEMO] coffee",
    "[FICTIONAL DEMO] coffee 2",
    "[FICTIONAL DEMO] coffee 5.5 and 6.5",
    "[FICTIONAL DEMO] coffee last Friday 5.5",
    "[FICTIONAL DEMO] coffee May 5.5",
    "[FICTIONAL DEMO] coffee 5/11 5.5",
    "[FICTIONAL DEMO] coffee 31/02/2026 5.5",
    "[FICTIONAL DEMO] milk tea last Friday 7",
    "[FICTIONAL DEMO] bubble tea 31/02/2026 $7",
])
def test_incomplete_or_ambiguous_manual_note_does_not_get_capture_date(text):
    result = FinanceAgent(api_key="").parse_note(text, capture_date=CAPTURE_DATE)
    assert result.date is None


@pytest.mark.parametrize("text", ["baklava USD 15.00", "milktea USD 7.00", "boba 7 SGD"])
def test_manual_foreign_price_is_not_recorded_as_aud(text):
    result = FinanceAgent(api_key="").parse_note(text, capture_date=CAPTURE_DATE)
    assert result.total_amount is None and result.date is None


@pytest.mark.parametrize("text", [
    "unknown shop 7", "boba shop 7", "milktea 7 and 8",
    "cake qty 3", "cake mix 3", "cake 3 slices", "cake 3 and 4", "cake last Friday 3",
])
def test_uncertain_whole_dollar_amount_stays_blank(text):
    result = FinanceAgent(api_key="").parse_note(text, capture_date=CAPTURE_DATE)
    assert result.total_amount is None and result.date is None


def test_manual_note_preserves_explicit_date_and_non_telegram_default():
    finance = FinanceAgent(api_key="")
    assert finance.parse_note("[FICTIONAL DEMO] coffee 5.5").date is None
    assert finance.parse_note("[FICTIONAL DEMO] milktea 7").date is None
    assert finance.parse_note(
        "[FICTIONAL DEMO] coffee 5.5 05/11/2026", capture_date=CAPTURE_DATE
    ).date == "2026-11-05"
    assert finance.parse_note(
        "[FICTIONAL DEMO] coffee 5.5 yesterday", capture_date=CAPTURE_DATE
    ).date == "2026-11-10"


def test_configured_model_cannot_replace_manual_capture_date_or_amount():
    finance = FinanceAgent(api_key="")
    generate = Mock(return_value=SimpleNamespace(text=json.dumps({
        "vendor": "[FICTIONAL DEMO] parking",
        "date": "2026-11-01",
        "total_amount": 0.25,
        "category": "travel_parking",
    })))
    finance._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    result = finance.parse_note("[FICTIONAL DEMO] parking 5.5", capture_date=CAPTURE_DATE)

    generate.assert_called_once()
    config = generate.call_args.kwargs["config"]
    assert "verified the entry date as 2026-11-11 in Australia/Sydney" in config.system_instruction
    assert config.http_options.timeout == 8_000
    assert config.http_options.retry_options.attempts == 1
    assert result.date == CAPTURE_DATE.isoformat()
    assert result.total_amount == 5.50


@pytest.mark.parametrize(("description", "vendor", "label"), [
    ("milktea", "Milk Tea", "Milk Tea"),
    ("tarosago", "Taro Sago", "Taro Sago"),
    ("tarosago", "Invented Cafe", "tarosago"),
])
def test_gemini_classifies_informal_whole_dollar_notes(description, vendor, label):
    finance = FinanceAgent(api_key="")
    generate = Mock(return_value=SimpleNamespace(text=json.dumps({
        "vendor": vendor,
        "date": "2026-11-01",
        "total_amount": 0.25,
        "tax_amount": 0.02,
        "category": "dining_coffee",
        "claim_stream": "personal",
        "business_activity": "none",
        "business_pct": 0,
    })))
    finance._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    result = finance.parse_note(f"[FICTIONAL DEMO] {description} 7", capture_date=CAPTURE_DATE)

    generate.assert_called_once()
    assert result.vendor == label
    assert result.date == CAPTURE_DATE.isoformat()
    assert result.total_amount == 7.00 and result.tax_amount is None
    assert result.category == "dining_coffee" and result.claim_stream == "personal"
    assert result.classification_source == "ai_suggestion"


@pytest.mark.parametrize(("text", "capture_date"), [
    ("tarosago 7", None),
    ("tarosago next Friday 7", CAPTURE_DATE),
    ("tarosago kemarin 7", CAPTURE_DATE),
    ("tarosago 7 31/02/2026", CAPTURE_DATE),
    ("tarosago 7 and 8", CAPTURE_DATE),
    ("tarosago 7 SGD", CAPTURE_DATE),
    ("tarosago USD 7.00", CAPTURE_DATE),
    ("tarosago Rp 7000", CAPTURE_DATE),
    ("tarosago 7 rupiah", CAPTURE_DATE),
    ("tarosago quantity 7", CAPTURE_DATE),
    ("invoice number 7", CAPTURE_DATE),
    ("tarosago -7", CAPTURE_DATE),
    ("tarosago 0", CAPTURE_DATE),
])
def test_gemini_skips_ambiguous_or_unpriced_whole_dollar_notes(text, capture_date):
    finance = FinanceAgent(api_key="")
    generate = Mock()
    finance._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    result = finance.parse_note(text, capture_date=capture_date)

    generate.assert_not_called()
    assert result.total_amount is None and result.date is None


def test_gemini_whole_dollar_shortcut_does_not_replace_explicit_relative_date():
    finance = FinanceAgent(api_key="")
    generate = Mock()
    finance._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    result = finance.parse_note("[FICTIONAL DEMO] tarosago yesterday 7", capture_date=CAPTURE_DATE)

    generate.assert_not_called()
    assert result.date == "2026-11-10" and result.total_amount is None


def test_gemini_failure_preserves_offline_whole_dollar_protection():
    finance = FinanceAgent(api_key="", fallback_models=["fixture-fallback"])
    generate = Mock(side_effect=RuntimeError("Provider unavailable"))
    finance._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    result = finance.parse_note("[FICTIONAL DEMO] tarosago 7", capture_date=CAPTURE_DATE)

    assert generate.call_count == 2
    assert result.total_amount is None and result.date is None
    assert result.category == "general" and result.classification_source == "rule_heuristic"


@pytest.mark.parametrize(("amount", "currency"), [(None, "AUD"), (7, "SGD")])
def test_gemini_can_leave_an_informal_amount_unverified(amount, currency):
    finance = FinanceAgent(api_key="")
    generate = Mock(return_value=SimpleNamespace(text=json.dumps({
        "vendor": "Taro Sago",
        "total_amount": amount,
        "currency": currency,
        "category": "dining_coffee",
        "claim_stream": "personal",
    })))
    finance._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    result = finance.parse_note("[FICTIONAL DEMO] tarosago 7", capture_date=CAPTURE_DATE)

    generate.assert_called_once()
    assert result.total_amount is None


@pytest.mark.parametrize("text", ["cofee 5,5", "coffee 5,50", "coffe $5,5"])
def test_known_short_coffee_entry_does_not_wait_for_a_configured_model(text):
    finance = FinanceAgent(api_key="")
    generate = Mock(side_effect=AssertionError("known short coffee notes must be local"))
    finance._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    # [FICTIONAL DEMO] Exercise the exact punctuation and spelling of terse Telegram input.
    result = finance.parse_note(text, capture_date=CAPTURE_DATE)

    generate.assert_not_called()
    assert result.date == CAPTURE_DATE.isoformat()
    assert result.total_amount == 5.50
    assert result.category == "dining_coffee"
    assert result.claim_stream == "personal"
    assert result.classification_source == "rule_heuristic"


@pytest.mark.parametrize(("amount", "expected"), [
    ("$1,234.50", 1234.50),
    ("1,234.50", 1234.50),
    ("$1,234", 1234.0),
    ("1,234", None),
    ("1,234,5", None),
    ("$1,234,5", None),
])
def test_manual_note_distinguishes_decimal_commas_and_thousands(amount, expected):
    result = FinanceAgent(api_key="").parse_note(f"[FICTIONAL DEMO] coffee {amount}", capture_date=CAPTURE_DATE)
    assert result.total_amount == expected
    assert result.date == (CAPTURE_DATE.isoformat() if expected is not None else None)


@pytest.mark.parametrize(("text", "vendor", "day"), [
    ("Bunnings 12 Nov 2026 hammer $45.90", "Bunnings", "2026-11-12"),
    ("Bunnings hammer $45.90 12/11/26", "Bunnings", "2026-11-12"),
    ("Kmart Nov 3 storage box 19.00", "Kmart", "2026-11-03"),
    ("Officeworks 25th December paper $9.80", "Officeworks", "2025-12-25"),
    ("[FICTIONAL DEMO] Harbour Cafe $6.50 3 November", "[FICTIONAL DEMO] Harbour Cafe", "2026-11-03"),
])
def test_rule_parser_reads_written_dates_and_shop_names(text, vendor, day):
    result = FinanceAgent(api_key="").parse_note(text, capture_date=date(2026, 11, 12))
    assert result.vendor == vendor and result.date == day


def test_month_word_next_to_a_price_is_not_a_date():
    result = FinanceAgent(api_key="").parse_note("may 5.50", capture_date=date(2026, 11, 12))
    assert result.date != "2026-05-05" and result.total_amount == 5.5
