import sqlite3

import pytest

from src.agents.chief_of_staff import ChiefOfStaff
from src.agents.finance_agent import ParsedReceipt
from src.storage.db import init_db
from src.storage.journal import JournalManager


@pytest.mark.parametrize("category,label,account", [
    ("rent_expenses", "Rent expenses", "Rent"),
    ("contractors", "Contractor, sub-contractor and commission expenses", "Contractors & Commissions"),
    ("advertising_marketing", "All other expenses", "Advertising & Marketing"),
    ("phone_internet", "All other expenses", "Phone & Internet"),
    ("insurance", "All other expenses", "Insurance"),
    ("bank_fees", "All other expenses", "Bank & Payment Fees"),
    ("professional_fees", "All other expenses", "Accounting & Professional Fees"),
])
def test_shared_abn_category_gets_its_ato_label_and_own_account(category, label, account):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    cos = ChiefOfStaff(main_conn=conn)
    receipt_id = cos.km.record_receipt(
        data=ParsedReceipt(
            vendor="[FICTIONAL DEMO] Sample", date="2026-08-12", total_amount=110.0, tax_amount=10.0,
            gst_status="explicit", category=category, purpose="business", claim_stream="abn",
            business_activity="dog_walking", business_pct=100,
        ).model_dump(),
        file_hash=None, local_path=None, gdrive_file_id=None, gdrive_status="not_applicable",
    )
    row = cos.km.get_receipt(receipt_id)
    assert (row["category"], row["tax_label"], row["business_activity"]) == (category, label, "dog_walking")
    balance = JournalManager(conn).get_trial_balance()
    assert sum(r["total_debit"] for r in balance) == sum(r["total_credit"] for r in balance) > 0
    assert any(r["name"] == account and r["total_debit"] > 0 for r in balance)
    conn.close()
