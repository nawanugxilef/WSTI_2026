# Neo-D LedgerCraft

Autonomous multi-agent financial assistant for sole traders in Australia (`$ AUD`), built with **Google Antigravity** and synchronized with **Google Drive**.

> **Note:** All example transactions in documentation and tests are **fictional demo data (`[FICTIONAL DEMO]`)**.

---

## 🏛️ Architecture: 4-Agent Pipeline

```
Telegram / CLI / Notes / Receipts / Bank Statements
               │
               ▼
   [ Chief of Staff 🐕 ] ── Intake, file archiving & orchestration
               │
               ▼
      [ Finance Agent 🦊 ] ── Single-call Multimodal Gemini (vendor, AUD, GST, ABN & myTax)
               │ ▲
       Review  │ │ Re-read feedback loop (max 2 retries)
               ▼ │
        [ QA Agent 🐻‍❄️ ] ── Rule-based checks (math, 10% GST, dates, AUD, duplicates)
               │
          Approved / Flagged
               ▼
  [ Knowledge Manager 🦒 ] ── Double-entry ledger & Vendor Memory
               │
     ┌─────────┴─────────┐
     ▼                   ▼
Local SQLite (WAL)   Google Drive (Optional)
- ledger.db          - Receipts/
                     - Backups/ (Snapshots)
                     - Statements/
```

1. **Chief of Staff (🐕)**: Front-door intake coordinator (CLI, Telegram, single/multi receipt photos, PDFs, bank statements, and typed notes).
2. **Finance Agent (🦊)**: Single-call Gemini multimodal extractor (JSON schema, temperature 0, Australian myTax classification, and Personal vs. ABN Business split for **Dog Walking** and **3D Printing**).
3. **QA Agent (🐻‍❄️)**: Deterministic verification (Australian GST <= 10%, date sanity, strict AUD currency enforcement, line-item math, and duplicate detection with up to 2 retries).
4. **Knowledge Manager (🦒)**: Double-entry journal posting (`sum(debits) == sum(credits)`) and adaptive Vendor Memory learning.

---

## ☁️ Google Drive Structure (Configured via `.env`)

Set your own Google Drive folder IDs in `.env` (see `.env.example`):

| Environment Variable | Purpose |
|---|---|
| `GDRIVE_RECEIPTS_FOLDER_ID` | Original uploaded receipt photos and invoice PDFs |
| `GDRIVE_BACKUPS_FOLDER_ID` | Automated database snapshots (`.zip`) |
| `GDRIVE_STATEMENTS_FOLDER_ID` | Bank statement exports and reconciliations |

---

## 🚀 Quick Start (CLI — Fictional Examples)

```bash
# 1. Activate virtual environment
cd "Neo-D LedgerCraft"
source .venv/bin/activate

# 2. Initialize database and chart of accounts
python -m src.interfaces.cli init

# 3. Ingest a fictional personal expense note
python -m src.interfaces.cli note "Vendor: [FICTIONAL DEMO] Cafe | coffee AUD 6.50 today"

# 4. Ingest a fictional 3D printing ABN business expense note
python -m src.interfaces.cli note "Vendor: [FICTIONAL DEMO] 3D Maker Supply | nozzle kit AUD 32.99 today"

# 5. Check double-entry trial balance
python -m src.interfaces.cli balance

# 6. View Australian myTax tax deductions summary
python -m src.interfaces.cli mytax

# 7. Take a database snapshot and upload to Google Drive
python -m src.interfaces.cli backup
```

---

## 🧪 Running Automated Tests

```bash
python -m unittest discover tests
```
10 unit tests covering QA rules, double-entry balance, vendor memory, and the end-to-end 4-agent Chief of Staff pipeline.
