"""
Live Web Dashboard & API Server for Neo-D LedgerCraft
Serves the clean iOS-inspired web interface and queries live data directly from SQLite (ledger.db).
Strictly English and AUD only.
"""

from __future__ import annotations

import csv
import io
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from src.interfaces.cli import load_config, seed_accounts
from src.storage.db import get_connection, init_db
from src.storage.journal import JournalManager


def build_dashboard_payload(db_path: str = "./data/ledger.db") -> dict:
    conn = get_connection(db_path)
    init_db(conn)
    seed_accounts(conn)

    jm = JournalManager(conn)

    # 1. Receipts (Ordered newest first)
    cur = conn.execute(
        """
        SELECT id, vendor, date, total_amount, tax_amount, currency, category, purpose, tax_label, status, flag_reason
        FROM receipts
        ORDER BY id DESC
        """
    )
    raw_receipts = [dict(r) for r in cur.fetchall()]
    receipts_out = []
    for r in raw_receipts:
        is_dup = bool(r.get("flag_reason") and "duplicate" in r["flag_reason"].lower())
        is_flagged = r.get("status") == "flagged" and not is_dup
        scope = "abn" if r.get("purpose") == "business" else "personal"
        icon = "💼" if scope == "abn" else "🏠"
        claim = r["total_amount"] if (scope == "abn" and not is_dup) else 0.0
        receipts_out.append(
            {
                "id": r["id"],
                "vendor": r["vendor"],
                "date": r["date"],
                "amount": round(float(r["total_amount"] or 0.0), 2),
                "tax_amount": round(float(r["tax_amount"] or 0.0), 2),
                "currency": "AUD",
                "scope": scope,
                "icon": icon,
                "category": r["category"],
                "tax_label": r["tax_label"],
                "claim": round(float(claim), 2),
                "duplicate": is_dup,
                "check": is_flagged,
                "flag_reason": r.get("flag_reason"),
            }
        )

    valid_receipts = [r for r in receipts_out if not r["duplicate"]]
    expense_receipts = [
        r for r in valid_receipts
        if not any(k in (r["category"] or "").lower() for k in ("income", "revenue", "sales"))
    ]
    income_receipts = [
        r for r in valid_receipts
        if any(k in (r["category"] or "").lower() for k in ("income", "revenue", "sales"))
    ]

    total_claimable = round(sum(r["claim"] for r in expense_receipts if r["scope"] == "abn"), 2)
    personal_total = round(sum(r["amount"] for r in expense_receipts if r["scope"] == "personal"), 2)
    business_total = round(sum(r["amount"] for r in expense_receipts if r["scope"] == "abn"), 2)
    all_spent_total = round(personal_total + business_total, 2)

    # ABN by business (Dog Walking & 3D Printing)
    dog_walking_exp = round(
        sum(r["amount"] for r in expense_receipts if r["scope"] == "abn" and "dog" in (r["category"] or "").lower()), 2
    )
    printing_exp = round(business_total - dog_walking_exp, 2)

    dog_walking_inc = round(
        sum(r["amount"] for r in income_receipts if "dog" in (r["category"] or "").lower()), 2
    )
    printing_inc = round(
        sum(r["amount"] for r in income_receipts if "dog" not in (r["category"] or "").lower()), 2
    )

    def group_by_category(items):
        totals = {}
        for item in items:
            cat = item["category"] or "General"
            totals[cat] = round(totals.get(cat, 0.0) + item["amount"], 2)
        total_sum = sum(totals.values())
        sorted_cats = sorted(totals.items(), key=lambda x: x[1], reverse=True)
        result = []
        for cat, amt in sorted_cats:
            pct = round((amt / total_sum) * 100) if total_sum > 0 else 0
            result.append({"category": cat, "amount": amt, "pct": pct})
        return {"total": round(total_sum, 2), "items": result}

    where_money_goes = {
        "all": group_by_category(expense_receipts),
        "business": group_by_category([r for r in expense_receipts if r["scope"] == "abn"]),
        "personal": group_by_category([r for r in expense_receipts if r["scope"] == "personal"]),
    }

    mytax_rows = jm.get_mytax_summary("2026-07-01", "2027-06-30")
    by_category_abn = group_by_category([r for r in expense_receipts if r["scope"] == "abn"])["items"]

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
        "receipts": receipts_out,
        "mytax": {
            "total_claimable": total_claimable,
            "receipt_count": len(valid_receipts),
            "personal_total": personal_total,
            "business_total": business_total,
            "all_spent_total": all_spent_total,
            "abn_by_business": {
                "dog_walking": dog_walking_exp,
                "printing_3d": printing_exp,
            },
            "where_money_goes": where_money_goes,
            "by_mytax_label": mytax_rows,
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
            writer.writerow(["ID", "Date", "Vendor", "Scope", "Category", "Tax_Label", "Amount_AUD", "Claimable_AUD", "Duplicate"])
            for r in payload["receipts"]:
                writer.writerow([
                    r["id"],
                    r["date"],
                    r["vendor"],
                    r["scope"].upper(),
                    r["category"],
                    r.get("tax_label") or "",
                    f"{r['amount']:.2f}",
                    f"{r['claim']:.2f}",
                    r["duplicate"],
                ])
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

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/reward":
            content_len = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            try:
                data = json.loads(raw_body)
            except Exception:
                data = {}

            reward_name = str(data.get("reward_name", "")).strip()
            target_xp = data.get("reward_target_xp")

            conn = get_connection("./data/ledger.db")
            init_db(conn)
            with conn:
                if reward_name and target_xp is not None:
                    t_val = max(10, int(target_xp))
                    conn.execute(
                        "UPDATE companion_state SET reward_name = ?, reward_target_xp = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                        (reward_name, t_val),
                    )
                elif reward_name:
                    conn.execute(
                        "UPDATE companion_state SET reward_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                        (reward_name,),
                    )
                elif target_xp is not None:
                    t_val = max(10, int(target_xp))
                    conn.execute(
                        "UPDATE companion_state SET reward_target_xp = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                        (t_val,),
                    )
            conn.close()

            payload = build_dashboard_payload()
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
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


def run_server(port: int = 8080):
    load_config()
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"🌐 Neo-D LedgerCraft Live Web Dashboard running on http://localhost:{port}/web/")
    server.serve_forever()


if __name__ == "__main__":
    run_server(8080)
