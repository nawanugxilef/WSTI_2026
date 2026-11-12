# Neo-D LedgerCraft

Autonomous multi-agent financial assistant for sole traders in Australia (`$ AUD`), built with **Google Antigravity** and synchronized with **Google Drive**.

> **Note:** All example transactions in documentation and tests are **fictional demo data (`[FICTIONAL DEMO]`)**.

---

## 🏛️ Architecture: 4-Agent Pipeline

```
Telegram / Web demo / CLI / Notes / Receipts / Bank Statements
               │
               ▼
   [ Neo · Chief of Staff ] ── Intake, file archiving & orchestration
               │
               ▼
      [ Corgi · Finance 🐕 ] ── Single-call Multimodal Gemini (vendor, AUD, GST, ABN & myTax)
               │ ▲
       Review  │ │ Re-read feedback loop (max 2 retries)
               ▼ │
        [ Bear · QA 🐻‍❄️ ] ── Rule-based checks (math, 10% GST, dates, AUD, duplicates)
               │
          Approved / Flagged
               ▼
  [ Girraffe · Knowledge 🦒 ] ── Double-entry ledger & Vendor Memory
               │
     ┌─────────┴─────────┐
     ▼                   ▼
Local SQLite (WAL)   Google Drive (Receipt Originals)
- ledger.db          - Receipts/
                     - Backups/ (Snapshots)
                     - Statements/
```

1. **Neo, Chief of Staff**: Front-door intake coordinator (CLI, Telegram, single/multi receipt photos, PDFs, bank statements, and typed notes).
2. **Corgi, Finance Agent (🐕)**: Single-call Gemini multimodal extractor (JSON schema, temperature 0, Australian myTax classification, and Personal vs. ABN Business split for **Dog Walking** and **3D Printing**).
3. **Bear, QA Agent (🐻‍❄️)**: Deterministic verification (Australian GST <= 10%, date sanity, strict AUD currency enforcement, line-item math, and duplicate detection with up to 2 retries).
4. **Girraffe, Knowledge Manager (🦒)**: Double-entry journal posting (`sum(debits) == sum(credits)`) and adaptive Vendor Memory learning.

---

## ☁️ Google Drive Structure (Configured via `.env`)

Uploaded receipt files are held in memory until Google Drive confirms the original. SQLite stores the structured ledger and the Drive file ID. If Drive cannot verify the original, the upload is rejected before OCR and bookkeeping.

For a My Drive folder, connect the Google account that can write to that folder:

1. Enable the Google Drive API and Google Picker API in the Neo-D Google Cloud project. Configure Google Auth Platform and create a Desktop OAuth client with the `drive.file` scope.
2. On the owner's computer, install the setup-only package `google-auth-oauthlib`. Run the following command with the OAuth client JSON, intended folder ID, and a new credentials path outside the repository. Sign in and select the intended Receipts folder in Google's picker.

   ```bash
   python scripts/authorize_receipt_drive.py --client-secrets /private/desktop-client.json \
     --folder-id YOUR_RECEIPTS_FOLDER_ID --output /private/neo-drive-adc.json
   ```

3. Transfer the generated credentials securely to the server. Set `GOOGLE_APPLICATION_CREDENTIALS` to its private path and set `GDRIVE_RECEIPTS_FOLDER_ID` to the authorized folder ID in the bot and dashboard service environment. Restrict the file to the service user. Keep the credential files out of the repository.
4. Verify with a `[FICTIONAL DEMO]` photo. The owner should see the original link in Telegram, and the file should appear in the chosen Drive folder.

The Google Drive folder settings are:

| Environment Variable | Purpose |
|---|---|
| `GDRIVE_RECEIPTS_FOLDER_ID` | Original uploaded receipt photos and invoice PDFs |
| `GDRIVE_BACKUPS_FOLDER_ID` | Automated database snapshots (`.zip`) |
| `GDRIVE_STATEMENTS_FOLDER_ID` | Bank statement exports and reconciliations |

---

## Mobile dashboard

Open [Neo Dashboard](https://neo.madewithfelix.app/dashboard) and choose **Open Neo in Telegram**, or send `/dashboard` in the owner's private chat with Neo. Open the one-time link within five minutes.

Receipts, myTax, and Books use the Neo ledger.

To install the dashboard, open it in Safari on iPhone or Chrome on Android. In Safari, tap **Share**, then **Add to Home Screen**, and turn on **Open as Web App** if shown. In Chrome, open the menu and select **Install app**. To sign in from the Home Screen app, copy the one-time code from Neo's `/dashboard` reply and paste it into the dashboard's sign-in form.

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
214 tests and 28 subtests (13 November 2026) covering QA rules, double-entry balance, vendor memory, the end-to-end 4-agent pipeline, Telegram flows, dashboard sign-in and the public web demo.
