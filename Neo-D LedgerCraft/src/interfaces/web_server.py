"""
Live Web Dashboard & API Server for Neo-D LedgerCraft
Serves the clean iOS-inspired web interface and queries live data directly from SQLite (ledger.db).
Supports live owner corrections (/api/correct) with full journal, GST, and myTax recalculation,
and segregates needs-review items from confirmed claimable myTax totals.
Strictly English and AUD ($ AUD) only.
"""

from __future__ import annotations

import base64
import csv
import hmac
import io
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.agents.chief_of_staff import ChiefOfStaff
from src.agents.finance_agent import FinanceAgent
from src.agents.receipt_progress import read_receipt_progress
from src.interfaces.cli import load_config, seed_accounts
from src.interfaces.dashboard_auth import DASHBOARD_ORIGIN, clear_session, get_session, issue_session
from src.storage.categories import ATO_SUGGESTION_DISCLAIMER
from src.storage.db import get_connection, init_db
from src.storage.gdrive import drive_file_url
from src.storage.journal import JournalManager

logger = logging.getLogger("web_server")

# Thread-safe in-memory rate limiter for /api/intake
# Per-IP: max 30 requests / 60 seconds. Global: max 300 requests / 3600 seconds.
_INTAKE_LOCK = threading.Lock()
_INTAKE_RATE_LIMIT: dict[str, list[float]] = defaultdict(list)
_GLOBAL_INTAKE_HISTORY: list[float] = []
_RATE_LIMIT_WINDOW = 60.0
_MAX_REQUESTS_PER_WINDOW = 30
_GLOBAL_WINDOW = 3600.0
_MAX_GLOBAL_REQUESTS = 300

# File upload security constraints & allowed extensions
MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024  # 10 MB pre-read body limit
MAX_DECODED_FILE_SIZE = 5 * 1024 * 1024   # 5 MB decoded file limit
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".webp"}


def validate_uploaded_file(file_b64: str, filename: str) -> tuple[bytes, str]:
    """
    Validates uploaded file payload:
    - Decodes base64 strictly (validate=True).
    - Enforces non-empty decoded bytes and maximum size limit.
    - Validates extension against allowlist (.jpg, .jpeg, .png, .pdf, .webp).
    - Verifies file signatures / magic bytes.
    - Sanitizes filename to prevent path traversal or unsafe characters.
    Returns (raw_bytes, safe_filename).
    """
    if not isinstance(file_b64, str) or not file_b64.strip():
        raise ValueError("Uploaded file data cannot be empty")

    try:
        raw_bytes = base64.b64decode(file_b64.strip(), validate=True)
    except Exception:
        raise ValueError("Invalid base64 encoding for file_data")

    if len(raw_bytes) == 0:
        raise ValueError("Uploaded file cannot be empty (0 bytes)")

    if len(raw_bytes) > MAX_DECODED_FILE_SIZE:
        raise ValueError("File exceeds maximum size of 5 MB")

    clean_name = os.path.basename(str(filename or "receipt.jpg").strip().replace("\\", "/"))
    safe_name = "".join(c for c in clean_name if c.isalnum() or c in "._- ")
    if not safe_name:
        safe_name = "receipt.jpg"

    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: '{ext}'. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    # Magic byte signature verification
    if ext in (".jpg", ".jpeg"):
        if not raw_bytes.startswith(b"\xff\xd8\xff"):
            raise ValueError("File content does not match JPEG signature")
    elif ext == ".png":
        if not raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("File content does not match PNG signature")
    elif ext == ".pdf":
        if not raw_bytes.startswith(b"%PDF-"):
            raise ValueError("File content does not match PDF signature")
    elif ext == ".webp":
        if len(raw_bytes) < 12 or not (raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WEBP"):
            raise ValueError("File content does not match WebP signature")

    return raw_bytes, safe_name


# Absolute path resolution relative to project root
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "ledger.db")
OFFICE_STATIC_DIR = os.path.join(os.path.dirname(os.path.realpath(PROJECT_ROOT)), "static")
DASHBOARD_ASSETS = {
    "/dashboard.css": "text/css; charset=utf-8",
    "/dashboard.js": "text/javascript; charset=utf-8",
    "/dashboard.webmanifest": "application/manifest+json",
    "/dashboard-icon-192.png": "image/png",
    "/dashboard-icon-512.png": "image/png",
    "/dashboard-apple-touch-icon.png": "image/png",
    "/dashboard-sw.js": "text/javascript; charset=utf-8",
}


def get_default_db_path() -> str:
    """Returns the absolute path to the SQLite ledger database."""
    if os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes"):
        return os.environ.get("LEDGER_DB_PATH", os.path.join(PROJECT_ROOT, "data", "demo.db"))
    return os.environ.get("LEDGER_DB_PATH", DEFAULT_DB_PATH)


def _sydney_today() -> date:
    try:
        return datetime.now(ZoneInfo("Australia/Sydney")).date()
    except ZoneInfoNotFoundError:
        return date.today()


def _financial_year(day: date) -> int:
    return day.year if day.month >= 7 else day.year - 1


def _parse_dashboard_filters(query: str) -> tuple[int | None, str | None]:
    params = parse_qs(query, keep_blank_values=True, max_num_fields=10)
    years = params.get("fy", [])
    months = params.get("month", [])
    if len(years) > 1 or (years and not re.fullmatch(r"[0-9]{4}", years[0])):
        raise ValueError("Choose a financial year using its four-digit starting year.")
    if len(months) > 1 or (months and not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", months[0])):
        raise ValueError("Choose a month in YYYY-MM format.")
    year = int(years[0]) if years else None
    if year is not None and not 1 <= year <= 9998:
        raise ValueError("Choose a valid financial year.")
    month = months[0] if months else None
    if month and _financial_year(date.fromisoformat(f"{month}-01")) != (year or _financial_year(_sydney_today())):
        raise ValueError("Choose a month within the selected financial year.")
    return year, month


def _safe_csv_text(value: str) -> str:
    """Keep receipt text from becoming a spreadsheet formula."""
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠")):
        return "'" + value
    return value


def _receipt_items(raw_items: str | None) -> list[dict]:
    if not raw_items:
        return []
    try:
        items = json.loads(raw_items)
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    output = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("description"), str):
            continue
        description = item["description"].strip()
        amount = item.get("amount")
        if description and type(amount) in (float, int) and math.isfinite(amount):
            output.append({"description": description, "amount": round(amount, 2)})
    return output


def build_dashboard_payload(
    db_path: str | None = None,
    fy_start_year: int | None = None,
    month: str | None = None,
    *,
    include_drive_links: bool = False,
) -> dict:
    today = _sydney_today()
    current_year = _financial_year(today)
    year = fy_start_year if fy_start_year is not None else current_year
    period_start = date(year, 7, 1).isoformat()
    period_end = date(year + 1, 6, 30).isoformat()
    available_months = [
        f"{year + 1:04d}-{number:02d}" for number in range(6, 0, -1)
    ] + [
        f"{year:04d}-{number:02d}" for number in range(12, 6, -1)
    ]
    if month is not None and month not in available_months:
        raise ValueError("Choose a month within the selected financial year.")
    fy = f"FY{year:04d}-{(year + 1) % 100:02d}"

    target_db = db_path or get_default_db_path()
    conn = get_connection(target_db)
    init_db(conn)
    seed_accounts(conn)

    jm = JournalManager(conn)
    available_fys = {current_year, year}
    for row in conn.execute("SELECT DISTINCT SUBSTR(date, 1, 7) AS month FROM receipts"):
        try:
            receipt_month = date.fromisoformat(f"{row['month']}-01")
        except ValueError:
            continue
        receipt_year = _financial_year(receipt_month)
        if 1 <= receipt_year <= 9998:
            available_fys.add(receipt_year)

    # 1. Receipts (Ordered newest first)
    cur = conn.execute(
        """
        SELECT id, vendor, date, total_amount, tax_amount, raw_tax_amount, gst_status,
               currency, category, purpose, claim_stream, business_activity, business_pct,
               claimable_amount, tax_label, payment_method, items_json,
               confidence, classification_source, gdrive_file_id, gdrive_status, status, flag_reason
        FROM receipts
        WHERE date BETWEEN ? AND ?
        ORDER BY id DESC
        """,
        (period_start, period_end),
    )
    raw_receipts = [dict(r) for r in cur.fetchall()]
    receipts_out = []
    for r in raw_receipts:
        status = (r.get("status") or "recorded").strip().lower()
        is_dup = status == "duplicate" or bool(r.get("flag_reason") and "duplicate" in r["flag_reason"].lower())
        has_flag = bool((r.get("flag_reason") or "").strip()) and not is_dup
        confidence = float(r["confidence"] if r.get("confidence") is not None else 0.85)
        is_needs_review = (status not in ("recorded", "confirmed", "corrected") or has_flag or confidence < 0.80) and not is_dup
        purpose = r.get("purpose") or "business"
        stream = r.get("claim_stream") or ("abn" if purpose == "business" else "personal")
        activity = r.get("business_activity") or ("printing_3d" if stream == "abn" else "none")
        pct = float(r.get("business_pct") if r.get("business_pct") is not None else (100.0 if purpose == "business" else 0.0))
        amount = round(float(r["total_amount"] or 0.0), 2)

        if purpose == "business" and pct > 0 and not is_dup:
            claim = round(amount * (pct / 100.0), 2)
        else:
            claim = 0.0
        personal_share = round(amount - claim, 2) if not is_dup else 0.0

        icon = "💼" if stream == "abn" else ("👔" if stream == "payg" else "🏠")
        receipts_out.append(
            {
                "id": r["id"],
                "vendor": r["vendor"],
                "date": r["date"],
                "amount": amount,
                "tax_amount": round(float(r["tax_amount"] or 0.0), 2),
                "raw_tax_amount": round(float(r["raw_tax_amount"]), 2) if r.get("raw_tax_amount") is not None else None,
                "gst_status": r.get("gst_status") or "unknown",
                "currency": "AUD",
                "scope": stream,
                "claim_stream": stream,
                "business_activity": activity,
                "business_pct": round(pct, 2),
                "icon": icon,
                "category": r["category"],
                "tax_label": r["tax_label"],
                "payment_method": r.get("payment_method") or "Bank Account (1000)",
                "claim": claim,
                "personal_share": personal_share,
                "items": _receipt_items(r.get("items_json")),
                "confidence": round(confidence, 2),
                "classification_source": r.get("classification_source") or "ai_suggestion",
                "gdrive_status": r.get("gdrive_status") or "not_applicable",
                "gdrive_url": (
                    drive_file_url(r.get("gdrive_file_id"))
                    if include_drive_links and r.get("gdrive_status") == "uploaded"
                    else None
                ),
                "status": status,
                "duplicate": is_dup,
                "check": is_needs_review,
                "flag_reason": r.get("flag_reason"),
            }
        )

    valid_receipts = [r for r in receipts_out if not r["duplicate"]]
    expense_receipts = [
        r for r in valid_receipts
        if not any(k in (r["category"] or "").lower() for k in ("income", "revenue", "sales", "transfer", "repayment"))
    ]
    confirmed_expenses = [r for r in expense_receipts if not r["check"]]
    review_expenses = [r for r in expense_receipts if r["check"]]

    income_receipts = [
        r for r in valid_receipts
        if any(k in (r["category"] or "").lower() for k in ("income", "revenue", "sales"))
    ]

    # Segregate confirmed claimable deductions from needs-review items
    total_claimable = round(sum(r["claim"] for r in confirmed_expenses), 2)
    pending_review_claimable = round(sum(r["claim"] for r in review_expenses), 2)
    business_total = total_claimable
    personal_total = round(sum(r["personal_share"] for r in confirmed_expenses), 2)
    all_spent_total = round(sum(r["amount"] for r in confirmed_expenses), 2)

    # ABN by business (Dog Walking & 3D Printing) for confirmed items
    dog_walking_exp = round(
        sum(
            r["claim"]
            for r in confirmed_expenses
            if r["claim"] > 0
            and r["claim_stream"] == "abn"
            and (r["business_activity"] == "dog_walking" or "dog" in (r["category"] or "").lower())
        ),
        2,
    )
    printing_exp = round(
        sum(
            r["claim"]
            for r in confirmed_expenses
            if r["claim"] > 0
            and r["claim_stream"] == "abn"
            and r["business_activity"] != "dog_walking"
            and "dog" not in (r["category"] or "").lower()
        ),
        2,
    )
    payg_exp = round(
        sum(r["claim"] for r in confirmed_expenses if r["claim"] > 0 and r["claim_stream"] == "payg"),
        2,
    )

    business_income = [r for r in income_receipts if not r["check"] and r["claim_stream"] == "abn"]
    dog_walking_inc = round(
        sum(
            r["amount"] for r in business_income
            if r["business_activity"] == "dog_walking" or "dog" in (r["category"] or "").lower()
        ),
        2,
    )
    printing_inc = round(
        sum(
            r["amount"] for r in business_income
            if r["business_activity"] != "dog_walking" and "dog" not in (r["category"] or "").lower()
        ),
        2,
    )

    def group_by_category(items, amount_key="amount"):
        totals = {}
        for item in items:
            val = float(item.get(amount_key, 0.0) or 0.0)
            if val <= 0:
                continue
            cat = item["category"] or "General"
            totals[cat] = round(totals.get(cat, 0.0) + val, 2)
        total_sum = sum(totals.values())
        sorted_cats = sorted(totals.items(), key=lambda x: x[1], reverse=True)
        result = []
        for cat, amt in sorted_cats:
            pct = round((amt / total_sum) * 100) if total_sum > 0 else 0
            result.append({"category": cat, "amount": amt, "pct": pct})
        return {"total": round(total_sum, 2), "items": result}

    where_money_goes = {
        "all": group_by_category(confirmed_expenses, "amount"),
        "business": group_by_category(confirmed_expenses, "claim"),
        "personal": group_by_category(confirmed_expenses, "personal_share"),
    }

    mytax_rows = jm.get_mytax_summary(period_start, period_end)
    mytax_review_rows = jm.get_mytax_review_summary(period_start, period_end)
    by_category_abn = group_by_category(confirmed_expenses, "claim")["items"]

    # 3. Books (Trial Balance & P&L)
    tb = jm.get_trial_balance()
    bank_accounts = [row for row in tb if row["type"] in ("bank", "savings")]
    liability_accounts = [row for row in tb if row["type"] in ("credit", "current_liability")]
    equity_accounts = [row for row in tb if row["type"] in ("equity", "drawings")]

    dog_net = round(dog_walking_inc - dog_walking_exp, 2)
    printing_net = round(printing_inc - printing_exp, 2)
    net_profit = round(dog_net + printing_net, 2)
    net_worth = round(
        sum(b["net_balance"] for b in bank_accounts) + sum(l["net_balance"] for l in liability_accounts), 2
    )
    if month is None:
        month = today.strftime("%Y-%m") if year == current_year else max(
            (r["date"][:7] for r in receipts_out if r["date"][:7] in available_months),
            default=available_months[-1],
        )
    spending = jm.get_spending_summary(month)

    # 4. Companion State
    comp_cur = conn.execute(
        "SELECT total_xp, reward_name, reward_target_xp, boxes_unlocked, last_anim, updated_at FROM companion_state WHERE id = 1"
    )
    comp_row = comp_cur.fetchone()
    companion = (
        dict(comp_row)
        if comp_row
        else {
            "total_xp": 0,
            "reward_name": "2 Packs of Indomie Goreng 🍜",
            "reward_target_xp": 100,
            "boxes_unlocked": 0,
            "last_anim": "idle",
        }
    )

    conn.close()

    return {
        "fy": fy,
        "fy_start_year": year,
        "available_fys": sorted(available_fys, reverse=True),
        "period": {"start": period_start, "end": period_end},
        "currency": "AUD",
        "suggestion_disclaimer": ATO_SUGGESTION_DISCLAIMER,
        "receipts": receipts_out,
        "mytax": {
            "total_claimable": total_claimable,
            "pending_review_claimable": pending_review_claimable,
            "needs_review_count": len(review_expenses),
            "receipt_count": len(confirmed_expenses),
            "personal_total": personal_total,
            "business_total": business_total,
            "payg_total": payg_exp,
            "all_spent_total": all_spent_total,
            "abn_by_business": {
                "dog_walking": dog_walking_exp,
                "printing_3d": printing_exp,
            },
            "where_money_goes": where_money_goes,
            "by_mytax_label": mytax_rows,
            "by_mytax_needs_review": mytax_review_rows,
            "by_category": by_category_abn,
        },
        "books": {
            "net_profit": net_profit,
            "net_worth": net_worth,
            "dog_walking_pl": {
                "income": dog_walking_inc,
                "expenses": dog_walking_exp,
                "net": dog_net,
            },
            "printing_3d_pl": {
                "income": printing_inc,
                "expenses": printing_exp,
                "net": printing_net,
            },
            "bank_accounts": bank_accounts,
            "liabilities": liability_accounts,
            "equity": equity_accounts,
            "trial_balance": tb,
            "available_months": available_months,
            "spending": spending,
        },
        "companion": companion,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _dashboard_session(self) -> bool:
        return get_session(get_default_db_path(), self.headers.get("Cookie", ""))

    def _dashboard_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _dashboard_exchange(self) -> None:
        if self.headers.get("Origin") != DASHBOARD_ORIGIN:
            self._dashboard_json(403, {"error": "Invalid request origin."})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._dashboard_json(415, {"error": "Expected a JSON request."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 1024:
                raise ValueError("Invalid request length")
            body = json.loads(self.rfile.read(length))
        except ValueError:
            self._dashboard_json(400, {"error": "Invalid login request."})
            return
        code = body.get("code") if isinstance(body, dict) else None
        if not isinstance(code, str) or not code:
            self._dashboard_json(400, {"error": "Missing login code."})
            return
        cookie = issue_session(get_default_db_path(), code)
        if cookie is None:
            self._dashboard_json(401, {"error": "This login link is invalid or has expired."})
            return
        self._finish_dashboard_session(cookie)

    def _finish_dashboard_session(self, cookie: str) -> None:
        self.send_response(204)
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_DELETE(self):
        if urlparse(self.path).path != "/api/dashboard/session":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Origin") != DASHBOARD_ORIGIN:
            self._dashboard_json(403, {"error": "Invalid request origin."})
            return
        try:
            cookie = clear_session(get_default_db_path(), self.headers.get("Cookie", ""))
        except sqlite3.Error:
            self._dashboard_json(503, {"error": "Could not sign out. Please try again."})
            return
        self._finish_dashboard_session(cookie)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/office/receipt-progress":
            payload = read_receipt_progress(get_default_db_path())
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/office-public":
            self._serve_file(os.path.join(OFFICE_STATIC_DIR, "index.html"), "text/html; charset=utf-8")
            return

        if path in ("/office-calendar.js", "/office-board.js"):
            self._serve_file(os.path.join(OFFICE_STATIC_DIR, path[1:]), "text/javascript; charset=utf-8")
            return

        if path in ("/api/dashboard", "/api/export.csv"):
            if not self._dashboard_session():
                self._dashboard_json(401, {"error": "Sign in to open the dashboard."})
                return
            try:
                year, month = _parse_dashboard_filters(parsed.query)
                payload = build_dashboard_payload(
                    fy_start_year=year, month=month, include_drive_links=path == "/api/dashboard"
                )
            except ValueError as error:
                self._dashboard_json(400, {"error": str(error)})
                return
            except sqlite3.Error:
                self._dashboard_json(503, {"error": "Could not load the ledger. Please try again."})
                return
            if path == "/api/dashboard":
                self._dashboard_json(200, payload)
                return
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "ID",
                    "Date",
                    "Vendor",
                    "Status",
                    "Stream",
                    "Activity",
                    "Category",
                    "Business_Pct",
                    "GST_Status",
                    "Claimable_GST_AUD",
                    "Suggested_Tax_Label",
                    "Amount_AUD",
                    "Claimable_AUD",
                    "Personal_Share_AUD",
                    "Duplicate",
                ]
            )
            for r in payload["receipts"]:
                writer.writerow(
                    [
                        r["id"],
                        r["date"],
                        _safe_csv_text(r["vendor"]),
                        _safe_csv_text(r["status"]),
                        _safe_csv_text(r["claim_stream"].upper()),
                        _safe_csv_text(r["business_activity"]),
                        _safe_csv_text(r["category"]),
                        f"{r['business_pct']:.0f}%",
                        _safe_csv_text(r["gst_status"]),
                        f"{r['tax_amount']:.2f}",
                        _safe_csv_text(r.get("tax_label") or ""),
                        f"{r['amount']:.2f}",
                        f"{r['claim']:.2f}",
                        f"{r['personal_share']:.2f}",
                        r["duplicate"],
                    ]
                )
            csv_bytes = output.getvalue().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="Neo-D_LedgerCraft_{payload["fy"]}_AUD.csv"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(csv_bytes)
            return

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        if path in ("/", "/web", "/web/", "/index.html", "/web/index.html", "/dashboard", "/dashboard/"):
            file_path = os.path.join(base_dir, "web", "index.html")
            self._serve_file(file_path, "text/html; charset=utf-8")
            return

        if path in DASHBOARD_ASSETS:
            self._serve_file(os.path.join(base_dir, "web", path[1:]), DASHBOARD_ASSETS[path])
            return

        if path in ("/docs/assembly_guide_3d.html", "/assembly_guide_3d.html"):
            file_path = os.path.join(base_dir, "docs", "assembly_guide_3d.html")
            self._serve_file(file_path, "text/html; charset=utf-8")
            return

        self.send_response(404)
        self.end_headers()

    def _check_admin_auth(self, *, required: bool = False) -> bool:
        """Check the configured bearer token, with optional legacy tokenless access."""
        token = os.environ.get("WEB_ADMIN_TOKEN", "").strip()
        if not token:
            return not required
        auth_header = self.headers.get("Authorization", "").strip()
        expected = f"Bearer {token}"
        return hmac.compare_digest(auth_header, expected)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/dashboard/session":
            self._dashboard_exchange()
            return

        dashboard_action = path in ("/api/dashboard/reward", "/api/dashboard/intake")
        if dashboard_action:
            if self.headers.get("Origin") != DASHBOARD_ORIGIN:
                self._dashboard_json(403, {"error": "Invalid request origin."})
                return
            if not self._dashboard_session():
                self._dashboard_json(401, {"error": "Sign in to use the dashboard."})
                return
            path = "/api/" + path.rsplit("/", 1)[-1]
        elif path in ("/api/reward", "/api/correct", "/api/intake"):
            origin = self.headers.get("Origin")
            has_browser_headers = origin is not None or self.headers.get("Cookie") is not None
            if not self._check_admin_auth(required=has_browser_headers):
                self._dashboard_json(401, {"error": "Unauthorized"})
                return

        cl_header = self.headers.get("Content-Length", "0")
        try:
            content_len = int(cl_header)
            if content_len < 0:
                raise ValueError("Negative Content-Length")
        except (ValueError, TypeError):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid Content-Length header"}).encode("utf-8"))
            return

        # Pre-read request body size limit: reject oversized payloads before memory allocation
        if content_len > MAX_REQUEST_BODY_SIZE:
            self.send_response(413)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Request entity too large (max 10 MB)"}).encode("utf-8"))
            return

        try:
            data = json.loads(self.rfile.read(content_len)) if content_len > 0 else {}
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
        except ValueError:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid JSON body"}).encode("utf-8"))
            return

        db_file = get_default_db_path()

        if path == "/api/correct":
            try:
                rec_id = int(data.get("receipt_id", 0))
                if rec_id <= 0:
                    raise ValueError("Missing or invalid receipt_id")

                # Sanitize text fields against HTML/script injection
                for field in ("vendor", "category", "tax_label", "claim_stream", "business_activity"):
                    val = data.get(field)
                    if isinstance(val, str) and ("<" in val or ">" in val):
                        raise ValueError(f"{field} contains invalid characters ('<' or '>')")

                pct_raw = data.get("business_pct")
                pct_val: float | None = None
                if pct_raw is not None:
                    pct_val = float(pct_raw)
                    if not (0.0 <= pct_val <= 100.0):
                        raise ValueError("business_pct must be between 0.0 and 100.0")

                tax_raw = data.get("tax_amount")
                tax_val: float | None = None
                if tax_raw is not None:
                    tax_val = float(tax_raw)
                    if tax_val < 0.0:
                        raise ValueError("tax_amount cannot be negative")

                conn = get_connection(db_file)
                init_db(conn)
                seed_accounts(conn)
                cos = ChiefOfStaff(main_conn=conn)

                if tax_val is not None:
                    existing_rec = cos.km.get_receipt(rec_id)
                    if existing_rec:
                        rec_total = float(existing_rec.get("total_amount") or 0.0)
                        if tax_val > rec_total:
                            conn.close()
                            raise ValueError(f"tax_amount (${tax_val:.2f}) cannot exceed receipt total (${rec_total:.2f})")

                cos.correct_receipt(
                    receipt_id=rec_id,
                    category=data.get("category"),
                    claim_stream=data.get("claim_stream"),
                    business_activity=data.get("business_activity"),
                    business_pct=pct_val,
                    tax_label=data.get("tax_label"),
                    vendor=data.get("vendor"),
                    tax_amount=tax_val,
                )
                conn.close()
                self._dashboard_json(200, build_dashboard_payload(db_file))
                return
            except ValueError as e:
                err_body = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err_body)
                return
            except Exception as e:
                logger.error("Error in /api/correct: %s", e)
                err_body = json.dumps({"error": "Failed to update receipt correction"}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err_body)
                return

        if path == "/api/reward":
            try:
                reward_name = str(data.get("reward_name", "")).strip()[:40]
                target_xp_raw = data.get("reward_target_xp")
                target_xp: int | None = None
                if target_xp_raw is not None:
                    try:
                        target_xp = max(10, int(target_xp_raw))
                    except (ValueError, TypeError):
                        raise ValueError("reward_target_xp must be a valid integer")

                conn = get_connection(db_file)
                init_db(conn)
                with conn:
                    if reward_name and target_xp is not None:
                        conn.execute(
                            "UPDATE companion_state SET reward_name = ?, reward_target_xp = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                            (reward_name, target_xp),
                        )
                    elif reward_name:
                        conn.execute(
                            "UPDATE companion_state SET reward_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                            (reward_name,),
                        )
                    elif target_xp is not None:
                        conn.execute(
                            "UPDATE companion_state SET reward_target_xp = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                            (target_xp,),
                        )
                conn.close()

                self._dashboard_json(200, build_dashboard_payload(db_file))
                return
            except ValueError as e:
                err_body = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err_body)
                return
            except Exception as e:
                logger.error("Error in /api/reward: %s", e)
                err_body = json.dumps({"error": "Failed to update companion reward"}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err_body)
                return

        if path == "/api/intake":
            try:
                # Per-IP rate limiting: when TRUST_PROXY=1, inspect the LAST entry in X-Forwarded-For (Finding #37)
                if os.environ.get("TRUST_PROXY", "").strip().lower() in ("1", "true", "yes"):
                    xff = self.headers.get("X-Forwarded-For", "").strip()
                    client_ip = xff.split(",")[-1].strip() if xff else (self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1")
                else:
                    client_ip = self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1"

                now = time.time()
                rate_limited_reason: str | None = None
                retry_after_sec = 60

                with _INTAKE_LOCK:
                    # Global rate limit guard (Finding #38): max 300 notes/hour across all callers
                    global _GLOBAL_INTAKE_HISTORY
                    _GLOBAL_INTAKE_HISTORY = [t for t in _GLOBAL_INTAKE_HISTORY if now - t < _GLOBAL_WINDOW]
                    if len(_GLOBAL_INTAKE_HISTORY) >= _MAX_GLOBAL_REQUESTS:
                        rate_limited_reason = "Global expense intake limit reached. Please try again later."
                        retry_after_sec = 300
                    else:
                        # Clean up expired per-IP entries to prevent memory growth (Finding #34)
                        expired_ips = [ip for ip, timestamps in _INTAKE_RATE_LIMIT.items() if not [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]]
                        for exp_ip in expired_ips:
                            del _INTAKE_RATE_LIMIT[exp_ip]

                        recent_calls = [t for t in _INTAKE_RATE_LIMIT.get(client_ip, []) if now - t < _RATE_LIMIT_WINDOW]
                        if len(recent_calls) >= _MAX_REQUESTS_PER_WINDOW:
                            rate_limited_reason = f"Rate limit exceeded (max {_MAX_REQUESTS_PER_WINDOW} requests/min). Please try again shortly."
                            retry_after_sec = 60
                        else:
                            recent_calls.append(now)
                            _INTAKE_RATE_LIMIT[client_ip] = recent_calls
                            _GLOBAL_INTAKE_HISTORY.append(now)

                # Send 429 outside of lock (Finding #40)
                if rate_limited_reason:
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Retry-After", str(retry_after_sec))
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": rate_limited_reason}).encode("utf-8"))
                    return

                note_text = str(data.get("text", "")).strip()
                file_b64 = data.get("file_data")
                filename = str(data.get("filename", "receipt.jpg")).strip()

                if not note_text and not file_b64:
                    raise ValueError("Note text or file_data must be provided")
                if note_text and len(note_text) > 300:
                    raise ValueError("Note text exceeds maximum length of 300 characters")

                if file_b64:
                    raw_bytes, safe_filename = validate_uploaded_file(file_b64, filename)

                with closing(get_connection(db_file)) as conn:
                    init_db(conn)
                    seed_accounts(conn)
                    cos = ChiefOfStaff(main_conn=conn)

                    if file_b64:
                        summary = cos.process_file(safe_filename, user_note=note_text or None, file_bytes=raw_bytes)
                    else:
                        summary = cos.process_note(note_text)

                rec_id = int(summary.receipt_id) if hasattr(summary, "receipt_id") and isinstance(summary.receipt_id, (int, float)) else 0
                sum_status = str(getattr(summary, "status", "recorded") or "recorded")
                sum_gdrive = str(getattr(summary, "gdrive_status", "not_applicable") or "not_applicable")
                intake_summary = {
                    "receipt_id": rec_id,
                    "summary_text": str(getattr(summary, "summary_text", "") or ""),
                    "status": sum_status,
                    "gdrive_status": sum_gdrive,
                    "local_path": None,
                }
                # The intake endpoint permits demo traffic when no admin token is configured.
                if sum_gdrive == "uploaded" and (dashboard_action or os.environ.get("WEB_ADMIN_TOKEN", "").strip()):
                    archive_url = drive_file_url(getattr(summary, "gdrive_file_id", None))
                    if archive_url:
                        intake_summary["gdrive_url"] = archive_url

                # Non-success responses let the dashboard keep the selected file for retry.
                if sum_status in ("archive_unavailable", "extraction_unavailable") or rec_id <= 0:
                    intake_errors = {
                        "needs_review": "Receipt needs review before it can be recorded",
                        "duplicate": "This receipt has already been uploaded",
                        "extraction_unavailable": "Extraction unavailable",
                        "archive_unavailable": "Google Drive could not save the original",
                    }
                    err_payload = {
                        **intake_summary,
                        "error": intake_errors.get(sum_status, "Receipt was not recorded"),
                        "status": sum_status if sum_status in intake_errors else "not_recorded",
                    }
                    self._dashboard_json(503 if sum_status == "archive_unavailable" else 422, err_payload)
                    return

                payload = build_dashboard_payload(db_file)
                payload["intake_summary"] = intake_summary
                self._dashboard_json(200, payload)
                return
            except ValueError as e:
                err_body = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err_body)
                return
            except Exception as e:
                logger.error("Error in /api/intake: %s", e)
                err_body = json.dumps({"error": "Failed to process expense note"}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err_body)
                return

        self.send_response(404)
        self.end_headers()

    def _serve_file(self, file_path: str, content_type: str):
        if not os.path.exists(file_path):
            self.send_response(404)
            self.end_headers()
            return
        with open(file_path, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def init_demo_database(db_file: str, reset: bool = False) -> None:
    """Seed an empty demo ledger with fictional records. Reset only on explicit request."""
    if os.environ.get("DEMO_MODE", "").strip().lower() not in ("1", "true", "yes"):
        return
    logger.info("DEMO_MODE active: checking [FICTIONAL DEMO] database at %s", db_file)
    demo_json_path = os.path.join(PROJECT_ROOT, "config", "demo_fictional_data.json")
    if not os.path.exists(demo_json_path):
        return
    with open(demo_json_path, "r", encoding="utf-8") as f:
        demo_data = json.load(f)

    # Server restarts preserve receipts that have already been captured.
    if reset and os.path.exists(db_file):
        try:
            os.remove(db_file)
            logger.info("Demo data reset: removed %s", db_file)
        except OSError as e:
            logger.warning("Could not remove old demo db: %s", e)

    conn = get_connection(db_file)
    init_db(conn)
    seed_accounts(conn)
    cos = ChiefOfStaff(main_conn=conn, finance_agent=FinanceAgent(api_key=""))

    cur = conn.execute("SELECT COUNT(*) as c FROM receipts")
    if cur.fetchone()["c"] == 0:
        for item in demo_data.get("sample_receipts", []):
            vendor = item.get("vendor", "[FICTIONAL DEMO] Vendor")
            total = float(item.get("total_amount", 0.0))
            cat = item.get("category", "materials_3d_printing")
            date = item.get("date")
            tax = item.get("tax_amount")
            note_str = f"{vendor} ${total:.2f} {cat} {date or ''}"
            if tax is not None:
                note_str += f" gst={float(tax):.2f}"
            try:
                cos.process_note(note_str)
            except Exception as e:
                logger.warning("Could not auto-seed demo receipt: %s", e)
    conn.close()


def run_server(port: int | None = None, host: str | None = None):
    load_config()
    db_file = get_default_db_path()
    init_demo_database(db_file)

    target_host = host or os.environ.get("WEB_HOST", "127.0.0.1")
    raw_port = port or os.environ.get("PORT") or os.environ.get("WEB_PORT", 8080)
    try:
        target_port = int(raw_port)
    except (ValueError, TypeError):
        target_port = 8080

    server = ThreadingHTTPServer((target_host, target_port), DashboardHandler)
    print(f"🌐 Neo-D LedgerCraft Live Web Dashboard running on http://{target_host}:{target_port}/web/")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
