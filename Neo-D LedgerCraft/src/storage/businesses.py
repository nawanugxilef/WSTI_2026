"""
Business profile for Neo-D LedgerCraft: which ABN businesses this sole trader runs.

Set once with /setup in Telegram and saved next to the ledger database as
business_profile.json. With no profile the owner's two default businesses
(3D Printing and Dog Walking) are used, so existing data and tests keep working.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MAX_BUSINESSES = 3


@dataclass(frozen=True)
class Business:
    key: str
    name: str
    icon: str
    hints: str
    supplies_category: str
    income_category: str
    revenue_account: str


def _preset(key: str, name: str, icon: str, hints: str, revenue_account: str) -> Business:
    return Business(key, name, icon, hints, f"supplies_{key}", f"income_{key}", revenue_account)


# Built-in businesses keep their original category keys and revenue accounts.
PRESETS: Dict[str, Business] = {
    "printing_3d": Business(
        "printing_3d", "3D Printing", "🖨️",
        "3D printing filament, PLA, resin, nozzles, build plates, print orders",
        "materials_3d_printing", "income_3d_printing", "4000",
    ),
    "dog_walking": Business(
        "dog_walking", "Dog Walking", "🐕",
        "leashes, harnesses, waste bags, dog treats, pet minding, client walks",
        "dog_walking_supplies", "income_dog_walking", "4100",
    ),
    "cleaning": _preset("cleaning", "Cleaning", "🧹", "cleaning products, chemicals, mops, cloths, gloves, vacuum bags", "4200"),
    "trades": _preset("trades", "Trades", "🔨", "timber, fixings, screws, hardware, plumbing or electrical parts, tool hire, safety gear", "4300"),
    "photography": _preset("photography", "Photography", "📷", "camera gear, lenses, memory cards, prints, photo editing, studio hire", "4400"),
    "hair_beauty": _preset("hair_beauty", "Hair & Beauty", "💇", "hair products, colour, salon supplies, nail and beauty products", "4500"),
    "rideshare": _preset("rideshare", "Rideshare & Delivery", "🚗", "rideshare or food delivery driving, phone mount, insulated delivery bag", "4600"),
    "online_shop": _preset("online_shop", "Online Shop", "🛍️", "stock for resale, packaging, mailers, labels, marketplace fees", "4700"),
    "consulting": _preset("consulting", "Consulting", "💼", "client projects, freelance services, professional memberships, invoices to clients", "4800"),
}
DEFAULT_KEYS: Tuple[str, ...] = ("printing_3d", "dog_walking")
CUSTOM_REVENUE_ACCOUNT = "4850"

_profile_path: Optional[Path] = None
_cache: Tuple[Optional[float], Optional[dict]] = (None, None)


def configure(db_path: str | os.PathLike | None) -> None:
    """Store the profile next to this ledger database (call once at startup)."""
    global _profile_path, _cache
    _profile_path = Path(db_path).expanduser().resolve().with_name("business_profile.json") if db_path else None
    _cache = (None, None)


def _load() -> dict:
    global _cache
    if _profile_path is None or not _profile_path.exists():
        return {}
    mtime = _profile_path.stat().st_mtime
    if _cache[0] != mtime:
        try:
            _cache = (mtime, json.loads(_profile_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _cache = (mtime, {})
    return _cache[1] or {}


def _custom(entry: dict) -> Optional[Business]:
    key = str(entry.get("key", ""))
    name = re.sub(r"[*_`\[\]]", "", str(entry.get("name", ""))).strip()  # keep Telegram Markdown safe
    if not re.fullmatch(r"custom_[a-z0-9_]{1,30}", key) or not name:
        return None
    return Business(key, name[:40], "⭐", name[:40], f"supplies_{key}", f"income_{key}", CUSTOM_REVENUE_ACCOUNT)


def custom_key(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "business"
    return f"custom_{slug}"


def active_businesses() -> List[Business]:
    prof = _load()
    out: List[Business] = []
    for entry in prof.get("businesses") or []:
        if isinstance(entry, str) and entry in PRESETS:
            out.append(PRESETS[entry])
        elif isinstance(entry, dict):
            b = _custom(entry)
            if b:
                out.append(b)
    return out[:MAX_BUSINESSES] or [PRESETS[k] for k in DEFAULT_KEYS]


def has_payg() -> bool:
    return bool(_load().get("has_payg", True))


def is_configured() -> bool:
    return bool(_load().get("businesses"))


def save_profile(businesses: List[object], has_payg_job: bool) -> None:
    """businesses: preset keys and/or {"key": "custom_x", "name": "..."} dicts."""
    if _profile_path is None:
        raise RuntimeError("Business profile path is not configured.")
    clean = [b for b in businesses if (isinstance(b, str) and b in PRESETS) or (isinstance(b, dict) and _custom(b))]
    if not clean:
        raise ValueError("Choose at least one business.")
    data = {"businesses": clean[:MAX_BUSINESSES], "has_payg": bool(has_payg_job)}
    tmp = _profile_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _profile_path)


def abn_activity_keys() -> Tuple[str, ...]:
    return tuple(b.key for b in active_businesses())


def known_activity_keys() -> Tuple[str, ...]:
    """Active businesses plus every preset, so older receipts stay valid after a /setup change."""
    keys = list(abn_activity_keys())
    keys += [k for k in PRESETS if k not in keys]
    return tuple(keys)


def get_business(key: Optional[str]) -> Optional[Business]:
    k = (key or "").strip().lower()
    for b in active_businesses():
        if b.key == k:
            return b
    return PRESETS.get(k)


def exclusive_category_map() -> Dict[str, str]:
    """Category -> business for categories that belong to exactly one business."""
    out: Dict[str, str] = {}
    for b in list(PRESETS.values()) + active_businesses():
        out[b.supplies_category] = b.key
        out[b.income_category] = b.key
    return out


def business_for(activity: Optional[str], category: Optional[str]) -> str:
    """Which active business a receipt belongs to, for P&L grouping."""
    keys = abn_activity_keys()
    act = (activity or "").strip().lower()
    if act in keys:
        return act
    owner = exclusive_category_map().get((category or "").strip().lower())
    if owner in keys:
        return owner
    if "dog" in (category or "").lower() and "dog_walking" in keys:
        return "dog_walking"
    return keys[0]
