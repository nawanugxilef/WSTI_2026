from dataclasses import replace

from src.agents.chief_of_staff import IntakeSummary
from src.interfaces.telegram_receipt import format_receipt_card, format_receipt_details


DEMO_RECEIPT = IntakeSummary(
    receipt_id=42,
    vendor="[FICTIONAL DEMO] _Cafe*",
    date="2026-11-10",
    total_amount=6.50,
    currency="AUD",
    purpose="personal",
    category="dining_coffee",
    tax_label=None,
    status="recorded",
    flag_reason=None,
    gdrive_file_id="fictional_receipt_identifier",
    summary_text="[FICTIONAL DEMO] Verbose Markdown and raw notes must stay out of the card.",
    tax_amount=0.0,
    raw_tax_amount=0.60,
    gst_status="explicit",
    claim_stream="personal",
    business_activity="none",
    business_pct=0.0,
    payment_method="VISA Card ending 1234 (2000)",
    items_summary="[FICTIONAL DEMO] Flat white (large) ($6.50), [FICTIONAL DEMO] Other item ($0.00)",
    gdrive_status="uploaded",
)


def test_saved_personal_photo_is_compact_and_uses_gross_gst():
    card = format_receipt_card(DEMO_RECEIPT)
    assert card.splitlines() == [
        "🧾 #42 · [FICTIONAL DEMO] _Cafe* · 10/11/2026",
        "$6.50 AUD · GST $0.60 · Card",
        "Item: [FICTIONAL DEMO] Flat white (large)",
        "Personal · Dining & coffee",
        "Source: AI suggestion",
        "Status: Recorded · Drive saved",
    ]
    assert "fictional_receipt_identifier" not in card


def test_rule_based_note_omits_unverified_gst_and_raw_item_text():
    summary = replace(
        DEMO_RECEIPT,
        raw_tax_amount=None,
        gst_status="unknown",
        classification_source="rule_heuristic",
        gdrive_file_id=None,
        gdrive_status="not_applicable",
        payment_method="Bank Account (1000)",
        items_summary="[FICTIONAL DEMO] Raw typed note ($6.50)",
    )
    card = format_receipt_card(summary)
    assert "GST" not in card
    assert card.splitlines()[1] == "$6.50 AUD"
    assert "Raw typed note" not in card
    assert "Source: Rule-based" in card


def test_review_card_and_details_do_not_expose_payment_numbers_or_freeform_reasons():
    summary = replace(
        DEMO_RECEIPT,
        vendor="[FICTIONAL DEMO] Cafe acct #12-345",
        status="flagged",
        flag_reason="Invalid date; GST mismatch; [FICTIONAL DEMO] private review text",
        uncertainty_note="[FICTIONAL DEMO] Private model explanation",
        items_summary="[FICTIONAL DEMO] Latte ****5678 9999-0000-1111-2222 ($6.50)",
        raw_tax_amount=0.0,
    )
    card = format_receipt_card(summary)
    details = format_receipt_details(summary)
    assert "GST $0.00" in card
    assert "Status: Needs review · Not posted" in card
    assert "Check date, GST." in details
    for sensitive in ("12-345", "1234", "2000", "5678", "9999", "private review", "Private model", "Recorded"):
        assert sensitive not in details


def test_abn_details_reuse_card_and_explain_allocation():
    summary = replace(
        DEMO_RECEIPT,
        purpose="business",
        category="materials_3d_printing",
        claim_stream="abn",
        business_activity="printing_3d",
        business_pct=85.5,
        tax_label="Cost of sales",
        classification_source="vendor_memory",
        confidence=1.0,
    )
    card = format_receipt_card(summary)
    details = format_receipt_details(summary)
    assert "3D Printing · 85.5% business · Printing materials" in card
    assert "Source: Vendor memory" in card
    assert details.startswith(card + "\n\n")
    assert "Tax stream: ABN sole trader" in details
    assert "Confidence: 100%" in details
    assert "ATO suggestion: Cost of sales" in details


def test_duplicate_only_shows_existing_receipt_id():
    summary = replace(
        DEMO_RECEIPT,
        receipt_id=0,
        status="duplicate",
        flag_reason="This exact file is already saved as receipt #42.",
    )
    assert format_receipt_card(summary) == "Already saved as receipt #42."


def test_possible_duplicate_stays_visible_on_compact_card():
    summary = replace(DEMO_RECEIPT, status="flagged", flag_reason="Likely duplicate of receipt #12.")
    assert format_receipt_card(summary).splitlines()[-1] == "Status: Possible duplicate · Needs review"


def test_unposted_status_reports_missing_details_or_drive_failure():
    summary = replace(DEMO_RECEIPT, receipt_id=0, status="needs_review", flag_reason="Missing transaction date.")
    assert format_receipt_card(summary).splitlines() == [
        "Status: Needs review · Not posted",
        "Check date.",
        "Original saved to Drive.",
    ]
    failed = replace(summary, status="archive_unavailable", gdrive_file_id=None, gdrive_status="failed")
    assert format_receipt_card(failed).splitlines()[0] == "Status: Drive unavailable · Not posted"


def test_saved_transfer_does_not_claim_journal_posting():
    card = format_receipt_card(replace(DEMO_RECEIPT, category="transfer"))
    assert "Status: Saved · Journal pending · Drive saved" in card
