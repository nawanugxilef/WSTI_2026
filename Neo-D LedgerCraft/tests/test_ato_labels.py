import sqlite3

from src.agents.knowledge_manager import KnowledgeManager
from src.storage.categories import VALID_ABN_TAX_LABELS, VALID_PAYG_TAX_LABELS, load_category_catalog
from src.storage.db import init_db


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_every_category_uses_an_official_mytax_label():
    allowed = {"abn": VALID_ABN_TAX_LABELS, "payg": VALID_PAYG_TAX_LABELS}
    for key, spec in load_category_catalog().items():
        if spec.stream == "personal":
            assert not spec.tax_label, key
        else:
            assert spec.tax_label in allowed[spec.stream], key


def test_ai_written_label_is_ignored_in_favour_of_the_table():
    conn = _conn()
    km = KnowledgeManager(conn)
    rid = km.record_receipt({
        "vendor": "[FICTIONAL DEMO] Paper Gum", "date": "2026-11-10", "total_amount": 32.0,
        "category": "materials_3d_printing", "claim_stream": "abn", "business_activity": "printing_3d",
        "business_pct": 100, "confidence": 0.95, "classification_source": "ai_suggestion",
        "tax_label": "All other expenses",
    })
    row = conn.execute("SELECT tax_label FROM receipts WHERE id = ?", (rid,)).fetchone()
    assert row["tax_label"] == "Purchases and other costs"


def test_old_labels_are_renamed_on_startup():
    conn = _conn()
    conn.execute("UPDATE companion_state SET total_xp = 0")
    conn.execute(
        "INSERT INTO receipts (vendor, date, total_amount, category, tax_label) VALUES (?, ?, ?, ?, ?)",
        ("[FICTIONAL DEMO] Old", "2026-10-01", 10.0, "office_supplies", "Other business expenses"),
    )
    init_db(conn)
    assert conn.execute("SELECT tax_label FROM receipts").fetchone()["tax_label"] == "All other expenses"


def test_donation_and_tax_agent_get_d9_d10_labels():
    conn = _conn()
    km = KnowledgeManager(conn)
    for cat, label in (("gifts_donations", "D9 Gifts or donations"), ("tax_affairs", "D10 Cost of managing tax affairs")):
        rid = km.record_receipt({
            "vendor": "[FICTIONAL DEMO] Helper", "date": "2026-11-10", "total_amount": 50.0,
            "category": cat, "claim_stream": "payg", "business_activity": "payg_work",
            "business_pct": 100, "confidence": 0.95, "classification_source": "ai_suggestion",
        })
        assert conn.execute("SELECT tax_label FROM receipts WHERE id = ?", (rid,)).fetchone()["tax_label"] == label


def test_rule_based_note_recognises_donation_and_tax_agent():
    from src.agents.finance_agent import FinanceAgent
    parse = FinanceAgent._heuristic_parse_text
    assert parse(FinanceAgent.__new__(FinanceAgent), "Red Cross donation $50").category == "gifts_donations"
    assert parse(FinanceAgent.__new__(FinanceAgent), "Tax agent fee $220").category == "tax_affairs"
