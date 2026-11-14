"""
Category & Australian ATO myTax Allocation Helper for Neo-D LedgerCraft
Maps receipt categories to tax streams (ABN / PAYG / Personal), business activities
(dog_walking / printing_3d / payg_work / none), default business-use percentages,
and suggested Australian ATO myTax labels.
All ATO myTax labels are suggestions only and do not guarantee tax deductibility.
"""

from __future__ import annotations

import math
import re
import os
from dataclasses import dataclass
from typing import Dict, List, Optional
import yaml

from src.storage import businesses as biz


ATO_SUGGESTION_DISCLAIMER = "Suggested ATO myTax label only — not guaranteed deductibility; verify with ATO/tax agent."

VALID_STREAMS = ("abn", "payg", "personal")


def valid_activities() -> tuple:
    return biz.known_activity_keys() + ("payg_work", "none")

# Business-exclusive categories (a business's own supplies and income) come from
# biz.exclusive_category_map(). Shared ABN categories (office, tools, software, shipping,
# car, travel, repairs) fit every business and never overwrite a receipt's known activity.

VALID_ABN_TAX_LABELS = {
    # Field names from the ATO myTax 2026 "Net income or loss from business" instructions.
    "Purchases and other costs",
    "Depreciation expenses",
    "Motor vehicle expenses",
    "Repairs and maintenance",
    "All other expenses",
    "Other business income",
}

VALID_PAYG_TAX_LABELS = {
    # Question names from the ATO Individual tax return 2026 instructions (D1-D5, D9, D10).
    "D1 Work-related car expenses",
    "D2 Work-related travel expenses",
    "D3 Work clothing, laundry and dry-cleaning expenses",
    "D4 Work-related self-education expenses",
    "D5 Other work-related expenses",
    "D9 Gifts or donations",
    "D10 Cost of managing tax affairs",
}


def require_finite_float(val: object, field_name: str) -> float:
    """Validates that `val` is a finite numeric float (rejects None, booleans, NaN, and +/-inf)."""
    if isinstance(val, bool) or val is None:
        raise ValueError(f"Invalid {field_name}: expected a finite number, got {val!r}.")
    try:
        num = float(val)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: expected a finite number, got {val!r}.") from exc
    if not math.isfinite(num):
        raise ValueError(f"Invalid {field_name}: must be a finite number (got {val!r}).")
    return num


def is_tax_label_compatible(stream: str, tax_label: Optional[str]) -> bool:
    """Checks whether an ATO myTax label is compatible with the given tax stream."""
    s = (stream or "").strip().lower()
    if s == "personal":
        return tax_label is None
    if not tax_label:
        return False
    lbl = str(tax_label).strip()
    lbl_up = lbl.upper()
    is_payg_d_label = re.match(r"D\d+ ", lbl_up) is not None
    if s == "payg":
        return lbl in VALID_PAYG_TAX_LABELS
    if s == "abn":
        return lbl in VALID_ABN_TAX_LABELS and not is_payg_d_label
    return False


def is_category_activity_compatible(
    category: str,
    activity: str,
    catalog: Optional[Dict[str, "CategorySpec"]] = None,
) -> bool:
    """
    Checks whether `activity` is compatible with `category`.
    Shared ABN categories (office_supplies, tools_equipment, software_subscriptions,
    shipping_freight, car_fuel_maintenance, travel_parking) are compatible with both
    'dog_walking' and 'printing_3d'.
    """
    cat_map = catalog or load_category_catalog()
    cat_key = (category or "").strip().lower()
    act = (activity or "").strip().lower()
    spec = cat_map.get(cat_key)
    if spec is None:
        return False
    if spec.stream == "personal":
        return act == "none"
    if spec.stream == "payg":
        return act == "payg_work"
    if spec.stream == "abn":
        if act not in biz.known_activity_keys():
            return False
        exclusive_act = biz.exclusive_category_map().get(cat_key)
        if exclusive_act is not None:
            return act == exclusive_act
        return True
    return False


def recompute_compatible_category(
    current_cat: str,
    target_stream: str,
    target_activity: str,
    catalog: Dict[str, "CategorySpec"],
) -> str:
    """
    Recomputes a stream- and activity-compatible category when a receipt's stream or
    business_activity is changed without an explicit new category.
    Never fabricates a 3D-printing-exclusive category when target_activity is unknown ('none').
    """
    cat_key = (current_cat or "general").strip().lower()
    stream = (target_stream or "personal").strip().lower()
    act = (target_activity or "none").strip().lower()
    spec = catalog.get(cat_key)

    if spec and spec.stream == stream:
        owner = biz.exclusive_category_map().get(cat_key)
        target = biz.get_business(act)
        if stream == "abn" and owner and target and owner != act:
            is_income = cat_key == biz.get_business(owner).income_category
            return target.income_category if is_income else target.supplies_category
        return cat_key

    if stream == "abn":
        if cat_key in ("work_car_travel", "transport_personal"):
            return "car_fuel_maintenance"
        if cat_key == "work_travel":
            return "travel_parking"
        if cat_key in ("work_home_office_other", "self_education", "work_uniform_clothing", "gifts_donations", "tax_affairs"):
            return "dog_walking_supplies" if act == "dog_walking" else "office_supplies"
        if cat_key == "hobby_3d_printing":
            return "materials_3d_printing" if act == "printing_3d" else "office_supplies"
        target = biz.get_business(act)
        return target.supplies_category if target else "office_supplies"

    if stream == "payg":
        if cat_key in ("car_fuel_maintenance", "travel_parking", "transport_personal"):
            return "work_car_travel"
        return "work_home_office_other"

    # stream == "personal"
    if cat_key in ("materials_3d_printing", "tools_equipment", "shipping_freight", "repairs_maintenance"):
        return "hobby_3d_printing"
    if cat_key in ("car_fuel_maintenance", "travel_parking", "work_car_travel", "work_travel"):
        return "transport_personal"
    return cat_key if (spec and spec.stream == "personal") else "general"


@dataclass
class CategorySpec:
    key: str
    stream: str  # abn, payg, personal
    activity: str  # dog_walking, printing_3d, payg_work, none
    default_business_pct: float
    tax_label: Optional[str]
    description: str

    @property
    def purpose(self) -> str:
        return "business" if self.stream in ("abn", "payg") else "personal"


def _default_categories_path() -> str:
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    return os.path.join(base_dir, "config", "categories.yaml")


_catalog_cache: Dict[tuple, Dict[str, CategorySpec]] = {}


def load_category_catalog(yaml_path: Optional[str] = None) -> Dict[str, CategorySpec]:
    path = yaml_path or _default_categories_path()
    key = (path, os.path.getmtime(path) if os.path.exists(path) else None, biz.abn_activity_keys())
    if key not in _catalog_cache:
        _catalog_cache.clear()
        _catalog_cache[key] = _build_catalog(path)
    return dict(_catalog_cache[key])


def _build_catalog(path: str) -> Dict[str, CategorySpec]:
    catalog: Dict[str, CategorySpec] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    for group_name, default_stream in (
        ("business_categories", "abn"),
        ("payg_categories", "payg"),
        ("personal_categories", "personal"),
    ):
        entries = raw.get(group_name) or {}
        for key, meta in entries.items():
            meta = meta or {}
            stream = str(meta.get("stream", default_stream)).lower()
            if stream not in VALID_STREAMS:
                stream = default_stream
            default_act = (
                "printing_3d"
                if stream == "abn"
                else ("payg_work" if stream == "payg" else "none")
            )
            activity = str(meta.get("activity", default_act)).lower()
            if activity not in valid_activities():
                activity = default_act
            default_pct = float(
                meta.get(
                    "default_business_pct",
                    100.0 if stream in ("abn", "payg") else 0.0,
                )
            )
            tax_label = meta.get("tax_label") if stream in ("abn", "payg") else None
            catalog[key] = CategorySpec(
                key=key,
                stream=stream,
                activity=activity,
                default_business_pct=default_pct,
                tax_label=tax_label,
                description=str(meta.get("description", key)),
            )

    # Hide categories of built-in businesses the owner does not run, add the owner's own.
    active = biz.abn_activity_keys()
    for cat_key, owner in biz.exclusive_category_map().items():
        if owner not in active:
            catalog.pop(cat_key, None)
    for b in biz.active_businesses():
        if b.supplies_category not in catalog:
            catalog[b.supplies_category] = CategorySpec(
                b.supplies_category, "abn", b.key, 100.0, "Purchases and other costs",
                f"{b.name} supplies and materials: {b.hints}",
            )
        if b.income_category not in catalog:
            catalog[b.income_category] = CategorySpec(
                b.income_category, "abn", b.key, 100.0, "Other business income", f"{b.name} income from clients",
            )
    return catalog


def resolve_allocation(
    category: str,
    purpose: Optional[str] = None,
    claim_stream: Optional[str] = None,
    business_activity: Optional[str] = None,
    business_pct: Optional[float] = None,
    tax_label: Optional[str] = None,
    catalog: Optional[Dict[str, CategorySpec]] = None,
    strict_category: bool = True,
    allow_incomplete: bool = True,
) -> Dict[str, object]:
    """
    Normalizes and resolves consistent (purpose, claim_stream, business_activity,
    business_pct, tax_label) for a receipt or owner correction.
    Validates finite numbers, category existence, and stream/tax-label compatibility.
    When `allow_incomplete=True` (default), missing or incompatible `business_activity`
    or `business_pct` on ABN/PAYG streams are preserved as `'none'` / `0.0` (rather than
    fabricating `'printing_3d'` or `100%`) so the caller can hold the record in `needs_review`.
    """
    cat_map = catalog or load_category_catalog()
    if category is None or not str(category).strip():
        if strict_category:
            raise ValueError("Category cannot be empty.")
        cat_key = "general"
    else:
        cat_key = str(category).strip().lower()

    spec = cat_map.get(cat_key)
    if spec is None:
        if strict_category:
            raise ValueError(
                f"Unsupported category '{category}'. Valid categories: {', '.join(sorted(cat_map.keys()))}"
            )
        cat_key = "general"
        spec = cat_map.get("general")

    # 1. Determine stream (abn / payg / personal)
    explicit_stream = (claim_stream or "").strip().lower()
    if explicit_stream == "business":
        explicit_stream = "abn"
    if explicit_stream and explicit_stream not in VALID_STREAMS:
        raise ValueError(f"Unsupported claim_stream '{claim_stream}'. Expected one of {VALID_STREAMS}.")

    stream = explicit_stream
    if stream not in VALID_STREAMS:
        if purpose and str(purpose).strip().lower() == "personal":
            stream = "personal"
        elif purpose and str(purpose).strip().lower() == "payg":
            stream = "payg"
        elif spec:
            stream = spec.stream
        elif purpose and str(purpose).strip().lower() == "business":
            stream = "abn"
        else:
            stream = "personal"

    # 2. Determine business_pct (0..100) — reject non-finite (NaN / inf) or out-of-range values
    if business_pct is not None:
        pct_num = require_finite_float(business_pct, "business_pct")
        if pct_num < 0.0 or pct_num > 100.0:
            raise ValueError(f"Business percentage must be between 0 and 100 (got {pct_num}).")
        if stream == "personal":
            pct = 0.0
        else:
            pct = pct_num
            if pct == 0.0 and not allow_incomplete and not explicit_stream:
                stream = "personal"
    elif stream == "personal":
        pct = 0.0
    elif allow_incomplete:
        pct = 0.0
    elif spec and spec.stream == stream and spec.stream in ("abn", "payg"):
        pct = spec.default_business_pct
    else:
        pct = 100.0

    # 3. Determine activity (dog_walking / printing_3d / payg_work / none)
    act = (business_activity or "").strip().lower()
    if act and act not in valid_activities():
        raise ValueError(f"Unsupported business_activity '{business_activity}'. Expected one of {valid_activities()}.")

    if stream == "personal":
        act = "none"
    elif stream == "payg":
        if act and act != "payg_work":
            act = "none" if allow_incomplete else "payg_work"
        else:
            act = "payg_work"
    elif act in biz.known_activity_keys():
        if spec and spec.stream == "abn" and not is_category_activity_compatible(cat_key, act, cat_map):
            if allow_incomplete:
                act = "none"
    else:
        if allow_incomplete:
            act = "none"
        elif spec and spec.stream == "abn" and spec.activity in biz.known_activity_keys():
            act = spec.activity
        else:
            act = biz.abn_activity_keys()[0]

    # Reconcile category with stream if incompatible
    if spec and spec.stream != stream:
        cat_key = recompute_compatible_category(cat_key, stream, act, cat_map)
        spec = cat_map.get(cat_key)

    # 4. Determine suggested Australian ATO myTax label (strictly compatible with stream)
    if stream == "personal":
        resolved_tax_label = None
    elif tax_label and is_tax_label_compatible(stream, str(tax_label)):
        resolved_tax_label = str(tax_label).strip()
    elif spec and spec.stream == stream and spec.tax_label and is_tax_label_compatible(stream, spec.tax_label):
        resolved_tax_label = spec.tax_label
    elif stream == "payg":
        resolved_tax_label = "D5 Other work-related expenses"
    elif "income" in cat_key or "revenue" in cat_key or "sales" in cat_key:
        resolved_tax_label = "Other business income"
    elif act == "printing_3d" and ("material" in cat_key or "shipping" in cat_key or "3d" in cat_key):
        resolved_tax_label = "Purchases and other costs"
    else:
        resolved_tax_label = "All other expenses"

    resolved_purpose = "business" if stream in ("abn", "payg") else "personal"

    return {
        "category": cat_key,
        "purpose": resolved_purpose,
        "claim_stream": stream,
        "business_activity": act,
        "business_pct": round(pct, 2),
        "tax_label": resolved_tax_label,
    }


def format_stream_badge(stream: str) -> str:
    s = (stream or "").lower()
    if s == "abn":
        return "💼 ABN Sole Trader"
    if s == "payg":
        return "👔 PAYG Employee"
    return "🏠 Personal"


def format_activity_badge(activity: str) -> str:
    a = (activity or "").lower()
    b = biz.get_business(a)
    if b:
        return f"{b.icon} {b.name}"
    if a == "payg_work":
        return "👔 PAYG Employment"
    return "🏠 Personal / None"


def list_selectable_categories() -> List[CategorySpec]:
    cat_map = load_category_catalog()
    return list(cat_map.values())
