"""
Command Line Interface for Neo-D LedgerCraft
Enables quick terminal intake, owner corrections (Category, Business %, ABN/PAYG/Personal,
Activity, Vendor), ledger reporting, Suggested Australian ATO myTax summaries, and Google Drive backups.
Strictly English and AUD ($ AUD).
"""

from __future__ import annotations

import argparse
import os
import yaml

from src.agents.chief_of_staff import ChiefOfStaff
from src.storage.categories import ATO_SUGGESTION_DISCLAIMER
from src.storage.db import get_connection, init_db
from src.storage.gdrive import GDriveAdapter
from src.storage.journal import JournalManager


def load_env():
    """Loads variables from .env or env.txt into os.environ if present and not a placeholder."""
    for fname in [".env", "env.txt"]:
        env_path = os.path.join(os.path.dirname(__file__), f"../../{fname}")
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k not in os.environ and not v.startswith("your_"):
                        os.environ[k] = v


def load_config(load_dotenv: bool = True) -> dict:
    if load_dotenv:
        load_env()
    config_path = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def seed_accounts(conn) -> None:
    """Seeds default accounts from accounts.yaml if empty."""
    cursor = conn.execute("SELECT COUNT(*) as c FROM accounts")
    if cursor.fetchone()["c"] == 0:
        acc_path = os.path.join(os.path.dirname(__file__), "../../config/accounts.yaml")
        if os.path.exists(acc_path):
            with open(acc_path, "r") as f:
                data = yaml.safe_load(f) or {}
            with conn:
                for grp, accs in data.items():
                    for code, info in accs.items():
                        conn.execute(
                            "INSERT OR IGNORE INTO accounts (code, name, type) VALUES (?, ?, ?)",
                            (str(code), info["name"], info["type"]),
                        )


def main():
    parser = argparse.ArgumentParser(description="Neo-D LedgerCraft CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. init
    subparsers.add_parser("init", help="Initialize database and chart of accounts")

    # 2. note: intake casual text note
    note_p = subparsers.add_parser("note", help="Record a typed expense note without receipt")
    note_p.add_argument("text", help="e.g. 'Fictional Demo Cafe coffee 6.50 today'")

    # 3. file: intake receipt image or PDF
    file_p = subparsers.add_parser("file", help="Ingest receipt image, invoice PDF, or bank statement CSV")
    file_p.add_argument("path", help="Path to receipt image, PDF, or CSV statement")

    # 4. correct: apply owner correction to an existing receipt
    corr_p = subparsers.add_parser("correct", help="Correct receipt Category, Business %, Stream, Activity, or Vendor")
    corr_p.add_argument("receipt_id", type=int, help="Numeric Receipt ID")
    corr_p.add_argument("--category", help="New category key (e.g. materials_3d_printing, dog_walking_supplies)")
    corr_p.add_argument("--pct", type=float, help="New Business % (0 to 100)")
    corr_p.add_argument("--stream", choices=["abn", "payg", "personal"], help="Tax stream: abn, payg, or personal")
    corr_p.add_argument("--activity", help="Business key from /setup (e.g. printing_3d, dog_walking), payg_work or none")
    corr_p.add_argument("--vendor", help="Corrected canonical vendor name")

    # 5. balance: show trial balance
    subparsers.add_parser("balance", help="Show double-entry trial balance")

    # 6. mytax: show tax report
    tax_p = subparsers.add_parser("mytax", help="Show Suggested Australian ATO myTax expense totals")
    tax_p.add_argument("--start", default="2026-07-01", help="Financial year start YYYY-MM-DD")
    tax_p.add_argument("--end", default="2027-06-30", help="Financial year end YYYY-MM-DD")

    # 7. backup: backup DB to Google Drive
    subparsers.add_parser("backup", help="Snapshot SQLite database and upload to Google Drive")

    args = parser.parse_args()

    cfg = load_config()
    db_path = cfg.get("storage", {}).get("db_path", "./data/ledger.db")

    conn = get_connection(db_path)
    init_db(conn)
    seed_accounts(conn)

    gdrive_cfg = cfg.get("google_drive", {})
    gdrive = GDriveAdapter(
        receipts_folder_id=gdrive_cfg.get("receipts_folder_id"),
        backups_folder_id=gdrive_cfg.get("backups_folder_id"),
        statements_folder_id=gdrive_cfg.get("statements_folder_id"),
    )

    cos = ChiefOfStaff(main_conn=conn, gdrive=gdrive)

    if args.command == "init":
        print(f"✅ Initialized database at:\n  - {db_path}")
        print("✅ Chart of accounts ready.")

    elif args.command == "note":
        res = cos.process_note(args.text)
        print(res.summary_text)

    elif args.command == "file":
        res = cos.process_file(args.path)
        print(res.summary_text)

    elif args.command == "correct":
        res = cos.correct_receipt(
            receipt_id=args.receipt_id,
            category=args.category,
            business_pct=args.pct,
            claim_stream=args.stream,
            business_activity=args.activity,
            vendor=args.vendor,
        )
        print(res.summary_text)

    elif args.command == "balance":
        jm = JournalManager(conn)
        rows = jm.get_trial_balance()
        print("\n⚖️ DOUBLE-ENTRY TRIAL BALANCE:")
        print(f"{'Code':<6} {'Account Name':<32} {'Debits ($)':<12} {'Credits ($)':<12} {'Net Balance ($)'}")
        print("-" * 75)
        tot_deb = 0.0
        tot_crd = 0.0
        for r in rows:
            print(f"{r['code']:<6} {r['name']:<32} {r['total_debit']:<12.2f} {r['total_credit']:<12.2f} {r['net_balance']:<12.2f}")
            tot_deb += r["total_debit"]
            tot_crd += r["total_credit"]
        print("-" * 75)
        print(f"{'TOTAL':<38} {tot_deb:<12.2f} {tot_crd:<12.2f} (Diff: {tot_deb - tot_crd:.2f})\n")

    elif args.command == "mytax":
        jm = JournalManager(conn)
        summary = jm.get_mytax_summary(args.start, args.end)
        print(f"\n🇦🇺 SUGGESTED AUSTRALIAN ATO myTAX SUMMARY ({args.start} to {args.end}):")
        print(f"Note: {ATO_SUGGESTION_DISCLAIMER}")
        if not summary:
            print("No deductible business/PAYG transactions found for this period.")
            return
        print(f"{'Stream':<7} {'myTax Label':<35} {'Count':<7} {'Net ($)':<10} {'GST ($)':<10} {'Claimable ($)'}")
        print("-" * 85)
        tot_gross = 0.0
        for s in summary:
            stream_tag = str(s.get("claim_stream") or "abn").upper()
            print(f"{stream_tag:<7} {s['tax_label']:<35} {s['count']:<7} ${s['net_amount']:<9.2f} ${s['total_gst']:<9.2f} ${s['gross_amount']:<9.2f}")
            tot_gross += s["gross_amount"]
        print("-" * 85)
        print(f"Total Suggested Claimable Deductions: ${tot_gross:.2f} AUD\n")

    elif args.command == "backup":
        print("⏳ Creating database snapshot and uploading to Google Drive...")
        backup_id = gdrive.backup_database([db_path])
        if backup_id:
            print(f"✅ Backup successfully uploaded to Google Drive! (File ID: {backup_id})")
        else:
            err = gdrive.last_error or "Check GDRIVE_BACKUPS_FOLDER_ID in .env"
            print(f"⚠️ Google Drive Backup Failed ({err}) — snapshot was not uploaded.")


if __name__ == "__main__":
    main()
