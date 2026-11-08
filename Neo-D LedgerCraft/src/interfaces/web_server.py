"""
Live Web Dashboard & API Server for Neo-D LedgerCraft
Serves the clean iOS-inspired web interface and queries live data directly from SQLite (ledger.db).
Supports live owner corrections (/api/correct) with full journal, GST, and myTax recalculation,
and segregates needs-review items from confirmed claimable myTax totals.
Strictly English and AUD ($ AUD) only.
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import logging
import os
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlparse

from src.agents.chief_of_staff import ChiefOfStaff
from src.interfaces.cli import load_config, seed_accounts
from src.storage.categories import ATO_SUGGESTION_DISCLAIMER
from src.storage.db import get_connection, init_db
from src.storage.journal import JournalManager

logger = logging.getLogger("web_server")

# In-memory per-IP sliding window rate limiter for /api/intake (30 requests / 60 seconds)
_INTAKE_RATE_LIMIT: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60.0
_MAX_REQUESTS_PER_WINDOW = 30

# Absolute path resolution relative to project root
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "ledger.db")


def get_default_db_path() -> str:
    """Returns the absolute path to the SQLite ledger database."""
    if os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes"):
        return os.environ.get("LEDGER_DB_PATH", os.path.join(PROJECT_ROOT, "data", "demo.db"))
    return os.environ.get("LEDGER_DB_PATH", DEFAULT_DB_PATH)


def build_dashboard_payload(db_path: str | None = None) -> dict:
    target_db = db_path or get_default_db_path()
    conn = get_connection(target_db)
    init_db(conn)
    seed_accounts(conn)

    jm = JournalManager(conn)

    # 1. Receipts (Ordered newest first)
    cur = conn.execute(
        """
        SELECT id, vendor, date, total_amount, tax_amount, raw_tax_amount, gst_status,
               currency, category, purpose, claim_stream, business_activity, business_pct,
               claimable_amount, tax_label, payment_method, items_json,
               confidence, classification_source, gdrive_status, status, flag_reason
        FROM receipts
        ORDER BY id DESC
        """
    )
    raw_receipts = [dict(r) for r in cur.fetchall()]
    receipts_out = []
    for r in raw_receipts:
        is_dup = bool(r.get("flag_reason") and "duplicate" in r["flag_reason"].lower())
        has_flag = bool((r.get("flag_reason") or "").strip()) and not is_dup
        low_conf = float(r.get("confidence") if r.get("confidence") is not None else 0.85) < 0.80
        is_needs_review = (r.get("status") in ("flagged", "needs_review") or has_flag or low_conf) and not is_dup
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
                "confidence": round(float(r.get("confidence") or 0.85), 2),
                "classification_source": r.get("classification_source") or "ai_suggestion",
                "gdrive_status": r.get("gdrive_status") or "not_applicable",
                "status": r.get("status") or "recorded",
                "duplicate": is_dup,
                "check": is_needs_review,
                "flag_reason": r.get("flag_reason"),
            }
        )

    valid_receipts = [r for r in receipts_out if not r["duplicate"]]
    expense_receipts = [
        r for r in valid_receipts
        if not any(k in (r["category"] or "").lower() for k in ("income", "revenue", "sales"))
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
            if r["claim"] > 0 and (r["business_activity"] == "dog_walking" or "dog" in (r["category"] or "").lower())
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

    dog_walking_inc = round(
        sum(r["amount"] for r in income_receipts if not r["check"] and (r["business_activity"] == "dog_walking" or "dog" in (r["category"] or "").lower())), 2
    )
    printing_inc = round(
        sum(r["amount"] for r in income_receipts if not r["check"] and r["business_activity"] != "dog_walking" and "dog" not in (r["category"] or "").lower()), 2
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

    mytax_rows = jm.get_mytax_summary("2026-07-01", "2027-06-30")
    mytax_review_rows = jm.get_mytax_review_summary("2026-07-01", "2027-06-30")
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
        sum(b["net_balance"] for b in bank_accounts) - sum(abs(l["net_balance"]) for l in liability_accounts), 2
    )

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
        "fy": "FY2026-27",
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
        },
        "companion": companion,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/dashboard":
            payload = build_dashboard_payload()
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/export.csv":
            payload = build_dashboard_payload()
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
                        r["vendor"],
                        r["status"],
                        r["claim_stream"].upper(),
                        r["business_activity"],
                        r["category"],
                        f"{r['business_pct']:.0f}%",
                        r["gst_status"],
                        f"{r['tax_amount']:.2f}",
                        r.get("tax_label") or "",
                        f"{r['amount']:.2f}",
                        f"{r['claim']:.2f}",
                        f"{r['personal_share']:.2f}",
                        r["duplicate"],
                    ]
                )
            csv_bytes = output.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="Neo-D_LedgerCraft_FY2026-27_AUD.csv"')
            self.end_headers()
            self.wfile.write(csv_bytes)
            return

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        if path in ("/", "/web", "/web/", "/index.html", "/web/index.html"):
            file_path = os.path.join(base_dir, "web", "index.html")
            self._serve_file(file_path, "text/html; charset=utf-8")
            return

        if path in ("/docs/assembly_guide_3d.html", "/assembly_guide_3d.html"):
            file_path = os.path.join(base_dir, "docs", "assembly_guide_3d.html")
            self._serve_file(file_path, "text/html; charset=utf-8")
            return

        self.send_response(404)
        self.end_headers()

    def _check_admin_auth(self) -> bool:
        """Checks Authorization: Bearer <WEB_ADMIN_TOKEN> if WEB_ADMIN_TOKEN is configured."""
        token = os.environ.get("WEB_ADMIN_TOKEN", "").strip()
        if not token:
            return True
        auth_header = self.headers.get("Authorization", "").strip()
        expected = f"Bearer {token}"
        return hmac.compare_digest(auth_header, expected)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Protect mutation endpoints with optional admin token
        if path in ("/api/reward", "/api/correct", "/api/intake"):
            if not self._check_admin_auth():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
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

        raw_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            data = json.loads(raw_body)
        except Exception:
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
                payload = build_dashboard_payload(db_file)
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
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

                payload = build_dashboard_payload(db_file)
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
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
                # Per-IP rate limiting: when TRUST_PROXY=1, inspect X-Forwarded-For
                if os.environ.get("TRUST_PROXY", "").strip().lower() in ("1", "true", "yes"):
                    xff = self.headers.get("X-Forwarded-For", "").strip()
                    client_ip = xff.split(",")[0].strip() if xff else (self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1")
                else:
                    client_ip = self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1"

                now = time.time()
                # Clean up expired entries to prevent memory growth (Finding #34)
                expired_ips = [ip for ip, timestamps in _INTAKE_RATE_LIMIT.items() if not [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]]
                for exp_ip in expired_ips:
                    del _INTAKE_RATE_LIMIT[exp_ip]

                recent_calls = [t for t in _INTAKE_RATE_LIMIT.get(client_ip, []) if now - t < _RATE_LIMIT_WINDOW]
                if len(recent_calls) >= _MAX_REQUESTS_PER_WINDOW:
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Retry-After", "60")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Rate limit exceeded (max {_MAX_REQUESTS_PER_WINDOW} requests/min). Please try again shortly."}).encode("utf-8"))
                    return
                recent_calls.append(now)
                _INTAKE_RATE_LIMIT[client_ip] = recent_calls

                note_text = str(data.get("text", "")).strip()
                if not note_text:
                    raise ValueError("Note text cannot be empty")
                if len(note_text) > 300:
                    raise ValueError("Note text exceeds maximum length of 300 characters")

                conn = get_connection(db_file)
                init_db(conn)
                seed_accounts(conn)
                cos = ChiefOfStaff(main_conn=conn)
                summary = cos.process_note(note_text)
                conn.close()

                payload = build_dashboard_payload(db_file)
                payload["intake_summary"] = {
                    "receipt_id": summary.receipt_id,
                    "summary_text": summary.summary_text,
                    "status": summary.status,
                    "gdrive_status": summary.gdrive_status,
                }
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
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


def init_demo_database(db_file: str, reset: bool = True) -> None:
    """
    If DEMO_MODE is enabled ('1', 'true', 'yes'), resets the demo database
    (e.g. data/demo.db) and populates it with clean [FICTIONAL DEMO] records.
    """
    if os.environ.get("DEMO_MODE", "").strip().lower() not in ("1", "true", "yes"):
        return
    logger.info("DEMO_MODE active: initializing clean [FICTIONAL DEMO] database at %s", db_file)
    demo_json_path = os.path.join(PROJECT_ROOT, "config", "demo_fictional_data.json")
    if not os.path.exists(demo_json_path):
        return
    with open(demo_json_path, "r", encoding="utf-8") as f:
        demo_data = json.load(f)

    # In demo mode, if reset=True or file exists, start clean to prevent public visitor contamination
    if reset and os.path.exists(db_file):
        try:
            os.remove(db_file)
            logger.info("Demo data reset: removed %s", db_file)
        except OSError as e:
            logger.warning("Could not remove old demo db: %s", e)

    conn = get_connection(db_file)
    init_db(conn)
    seed_accounts(conn)
    cos = ChiefOfStaff(main_conn=conn)

    cur = conn.execute("SELECT COUNT(*) as c FROM receipts")
    if cur.fetchone()["c"] == 0:
        for item in demo_data.get("sample_receipts", []):
            vendor = item.get("vendor", "[FICTIONAL DEMO] Vendor")
            total = float(item.get("total_amount", 0.0))
            cat = item.get("category", "materials_3d_printing")
            note_str = f"{vendor} ${total:.2f} {cat}"
            try:
                summary = cos.process_note(note_str)
                # Pass fixture explicit tax_amount and date if present
                fix_tax = item.get("tax_amount")
                fix_date = item.get("date")
                if fix_tax is not None and summary.receipt_id:
                    with conn:
                        conn.execute(
                            "UPDATE receipts SET tax_amount = ?, raw_tax_amount = ?, date = COALESCE(?, date) WHERE id = ?",
                            (float(fix_tax), float(fix_tax), fix_date, summary.receipt_id),
                        )
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
