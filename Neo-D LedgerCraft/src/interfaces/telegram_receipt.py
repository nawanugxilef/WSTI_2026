"""Render Neo-D receipt summaries for Telegram without Markdown."""

from __future__ import annotations

import math
import re
from datetime import date
from typing import TYPE_CHECKING

from src.agents.qa_agent import likely_duplicate_receipt_id
from src.storage.categories import VALID_ABN_TAX_LABELS, VALID_PAYG_TAX_LABELS

if TYPE_CHECKING:
    from src.agents.chief_of_staff import IntakeSummary


CATEGORY_LABELS = {
    "materials_3d_printing": "Printing materials",
    "shipping_freight": "Shipping & freight",
    "tools_equipment": "Tools & equipment",
    "income_3d_printing": "Printing income",
    "dog_walking_supplies": "Dog walking supplies",
    "income_dog_walking": "Dog walking income",
    "travel_parking": "Travel & parking",
    "car_fuel_maintenance": "Fuel & car maintenance",
    "software_subscriptions": "Software subscriptions",
    "office_supplies": "Office supplies",
    "work_car_travel": "Work car & travel",
    "work_uniform_clothing": "Work uniform & clothing",
    "self_education": "Self-education",
    "work_home_office_other": "Home office & other work expenses",
    "gifts_donations": "Gifts & donations",
    "tax_affairs": "Tax agent & tax affairs",
    "groceries": "Groceries",
    "dining_coffee": "Dining & coffee",
    "utilities": "Utilities",
    "health_medical": "Health & medical",
    "transport_personal": "Personal transport",
    "entertainment": "Entertainment",
    "hobby_3d_printing": "3D printing hobby",
    "transfer": "Transfer",
    "general": "General",
}
CATEGORY_ICONS = {
    "materials_3d_printing": "🖨️",
    "shipping_freight": "📦",
    "tools_equipment": "🛠️",
    "income_3d_printing": "💰",
    "dog_walking_supplies": "🐕",
    "income_dog_walking": "🦮",
    "travel_parking": "🅿️",
    "car_fuel_maintenance": "⛽",
    "software_subscriptions": "💻",
    "office_supplies": "📎",
    "work_car_travel": "🚗",
    "work_uniform_clothing": "👕",
    "self_education": "🎓",
    "work_home_office_other": "🖥️",
    "gifts_donations": "🎁",
    "tax_affairs": "🧾",
    "groceries": "🛒",
    "dining_coffee": "☕",
    "utilities": "🔌",
    "health_medical": "🏥",
    "transport_personal": "🚆",
    "entertainment": "🎬",
    "hobby_3d_printing": "🎨",
    "transfer": "🔁",
    "general": "🗂️",
}
CATEGORY_STREAM_ICONS = {"abn": "💼", "payg": "👔", "personal": "🏠"}
SOURCE_LABELS = {
    "ai_suggestion": "🐶 Corgi · AI suggestion",
    "rule_heuristic": "🧭 Rule-based",
    "vendor_memory": "🧠 Vendor memory",
    "owner_correction": "✏️ Owner correction",
}
ACTIVITY_LABELS = {"printing_3d": "3D Printing", "dog_walking": "Dog Walking"}
STREAM_LABELS = {"abn": "ABN sole trader", "payg": "PAYG employment", "personal": "Personal"}
REVIEW_STATUSES = {"flagged", "needs_review", "pending", "unverified"}
SAVED_STATUSES = {"recorded", "confirmed", "corrected"}
PAYMENT_LABELS = (
    (r"\bafterpay\b", "Afterpay"),
    (r"\bzip\s*pay\b", "Zip Pay"),
    (r"\bbnpl\b", "BNPL"),
    (r"\bpay\s*id\b", "PayID"),
    (r"\bosko\b", "Osko"),
    (r"\b(?:apple|google)\s+pay\b", "Mobile wallet"),
    (r"\bcredit\s+card\b", "Credit card"),
    (r"\b(?:debit|eftpos)\b", "Debit card"),
    (r"\b(?:card|visa|mastercard|amex)\b", "Card"),
    (r"\bcash\b", "Cash"),
    (r"\b(?:bank|transfer)\b", "Bank"),
)

_ITEM_AMOUNT = re.compile(r" \(\$-?\d+(?:\.\d{2})?\)(?=, |$)")
_MASKED_DIGITS = re.compile(r"(?:[xX*•·]\s*){2,}[- ]*\d{3,}")
_LABELLED_DIGITS = re.compile(
    r"\b(?:account|acct|card|bsb|visa|mastercard|amex)"
    r"(?:\s+(?:ending|end|number|no|in)){0,3}[\s.#:()\-]+"
    r"(?:[xX*•·]+[ -]*)?(?:\d[ .-]?){2,}\d\)?",
    re.IGNORECASE,
)
_LONG_DIGITS = re.compile(r"(?<!\d)(?:\d[ .-]?){5,}\d(?!\d)")


def _safe_preview(value: str, limit: int) -> str:
    text = "".join(char for char in value if char.isprintable() or char.isspace())
    text = " ".join(text.split())
    text = _MASKED_DIGITS.sub(" ", text)
    text = _LABELLED_DIGITS.sub(" ", text)
    text = _LONG_DIGITS.sub(" ", text)
    text = " ".join(text.split()).strip(" -·,;:")
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _date_label(value: str | None) -> str:
    try:
        return date.fromisoformat(value or "").strftime("%d/%m/%Y")
    except ValueError:
        return "Date unavailable"


def _drive_saved(summary: IntakeSummary) -> bool:
    return summary.gdrive_status == "uploaded" and bool(summary.gdrive_file_id)


def _amount_line(summary: IntakeSummary) -> str:
    total = summary.total_amount
    if summary.currency.upper() != "AUD" or total is None or not math.isfinite(total):
        return "Amount unavailable"
    fields = [f"${total:,.2f} AUD"]
    gst = summary.raw_tax_amount
    if summary.gst_status == "explicit" and gst is not None and math.isfinite(gst):
        fields.append(f"GST ${gst:,.2f}")
    method = summary.payment_method.strip()
    if method.casefold() != "bank account (1000)":
        for pattern, label in PAYMENT_LABELS:
            if re.search(pattern, method, re.IGNORECASE):
                fields.append(label)
                break
    return " · ".join(fields)


def _allocation_line(summary: IntakeSummary) -> str:
    category = CATEGORY_LABELS.get(summary.category, "Uncategorised")
    if summary.claim_stream == "abn":
        activity = ACTIVITY_LABELS.get(summary.business_activity, "ABN")
        return f"{activity} · {summary.business_pct:g}% business · {category}"
    if summary.claim_stream == "payg":
        return f"PAYG · {summary.business_pct:g}% work · {category}"
    return f"Personal · {category}"


def _review_required(summary: IntakeSummary) -> bool:
    return summary.status in REVIEW_STATUSES or bool((summary.flag_reason or "").strip())


def _review_reason(summary: IntakeSummary) -> str:
    flags = (summary.flag_reason or "").split(";")
    reason = " ".join(flag for flag in flags if not likely_duplicate_receipt_id(flag)).casefold()
    checks = ["possible duplicate"] if "duplicate" in (summary.flag_reason or "").casefold() else []
    checks.extend(label for keywords, label in (
        (("vendor",), "vendor"),
        (("date",), "date"),
        (("total amount", "positive total"), "total amount"),
        (("currency", "only receipts in aud"), "currency"),
        (("gst",), "GST"),
        (("item",), "item totals"),
        (("category", "classification", "business_activity", "business_pct", "percentage"), "classification"),
    ) if any(keyword in reason for keyword in keywords))
    return "Check " + ", ".join(checks[:3]) + "." if checks else "Check the receipt details."


def format_receipt_card(summary: IntakeSummary) -> str:
    """Return a compact receipt card or a short, truthful intake status."""
    if summary.status == "duplicate":
        if summary.exact_duplicate_of:
            return f"↩️ Already saved as receipt #{summary.exact_duplicate_of}."
        return "↩️ This receipt is already saved."

    if not summary.receipt_id:
        if summary.status == "archive_unavailable":
            return "Status: Drive unavailable · Not posted\nKeep your original and try again once Drive reconnects."
        if summary.status == "extraction_unavailable":
            lines = ["Status: Could not read receipt · Not posted", "Send a clearer photo or enter the details."]
        elif _review_required(summary):
            lines = ["Status: Needs review · Not posted", _review_reason(summary)]
        else:
            lines = ["Status: Receipt not posted"]
        if _drive_saved(summary):
            lines.append("Original saved to Drive.")
        return "\n".join(lines)

    vendor = _safe_preview(summary.vendor, 35) or "Unknown vendor"
    icon = "✍️" if summary.gdrive_status == "not_applicable" and not summary.local_path else "🧾"
    lines = [f"{icon} #{summary.receipt_id} · {vendor} · {_date_label(summary.date)}", _amount_line(summary)]
    # Typed-note items can contain the input note verbatim. Preview only uploaded receipt items.
    if _drive_saved(summary) or summary.local_path:
        description = _ITEM_AMOUNT.split(summary.items_summary, maxsplit=1)[0]
        item = _safe_preview(description, 46)
        if item and item != _safe_preview(summary.vendor, 46):
            lines.append(f"Item: {item}")
    lines.append(_allocation_line(summary))
    lines.append(f"Source: {SOURCE_LABELS.get(summary.classification_source, 'Unknown')}")

    review_required = _review_required(summary)
    if review_required:
        status = "Needs review · Not posted"
        matched = likely_duplicate_receipt_id(summary.flag_reason) if summary.status == "flagged" else None
        if matched and matched != summary.receipt_id:
            status = f"Possible duplicate of #{matched} · Excluded from totals"
        elif "duplicate" in (summary.flag_reason or "").casefold():
            status = "Possible duplicate · Needs review"
    elif summary.status in SAVED_STATUSES and any(key in summary.category.lower() for key in ("transfer", "repayment")):
        status = "Saved · Journal pending"
    else:
        status = {"recorded": "Recorded", "confirmed": "Confirmed", "corrected": "Updated"}.get(summary.status, "Check receipt")
    if _drive_saved(summary) and not review_required:
        status += " · Drive saved"
    lines.append(f"Status: {status}")
    return "\n".join(lines)


def format_receipt_details(summary: IntakeSummary) -> str:
    """Expand the same card with structured classification and QA details."""
    card = format_receipt_card(summary)
    if not summary.receipt_id or summary.status == "duplicate":
        return card

    details = []
    stream = STREAM_LABELS.get(summary.claim_stream)
    if stream:
        details.append(f"Tax stream: {stream}")
    if math.isfinite(summary.confidence) and 0 <= summary.confidence <= 1:
        details.append(f"Confidence: {summary.confidence:.0%}")
    allowed_labels = {"abn": VALID_ABN_TAX_LABELS, "payg": VALID_PAYG_TAX_LABELS}
    if summary.tax_label in allowed_labels.get(summary.claim_stream, ()):
        details.append(f"ATO suggestion: {summary.tax_label}")
    if _review_required(summary):
        details.append(_review_reason(summary))
    return card + "\n\n" + "\n".join(details) if details else card


def format_existing_receipt_card(summary: IntakeSummary, *, show_details: bool = False) -> str:
    card = format_receipt_details(summary) if show_details else format_receipt_card(summary)
    return f"🐾 Womby found it! Already saved as receipt #{summary.receipt_id}.\n\n{card}"
