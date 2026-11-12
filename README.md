# Neo-D LedgerCraft & Desk Companion (`WSTI_2026`)

An ADHD-friendly receipt catch-up assistant for sole traders in Sydney, Australia. Send a receipt in a chat. A crew of four agents reads it, checks it, files it in a double-entry ledger and builds the ATO myTax summary. Womby the wombat celebrates every receipt with +10 XP. Nothing punishes you for being late. Built in **Australian Dollars (`$ AUD`)** for **Dog Walking** and **3D Printing** ABN activities.

> **Most apps help you keep up. Neo-D helps you catch up.**

> **Privacy Notice:** All example vendors, receipts, and transactions in this repository are **100% synthetic (`[FICTIONAL DEMO]`)**. No real financial records, account balances, or credentials are stored in this repository.

---

## Try it

**[neo.madewithfelix.app](https://neo.madewithfelix.app/)**: open the 3D office, then choose **Try Neo**.

- No login. Each visitor gets a private session that expires after 30 minutes.
- Type a note such as `Bunnings hammer $45.90`, or upload the fictional sample receipts offered in the chat (up to 10 photos in one send, or one PDF).
- Watch the agents work in the office: Neo receives, Corgi reads, Bear checks, Girraffe files. The task board moves each card from Inbox to Done.
- Fix any receipt with one tap: **Category**, **ABN**, **PAYG** or **Personal**.
- Send the same file twice and Neo finds the duplicate. No second entry, no extra XP.

The public demo runs in fixture mode: the sample receipts use known extraction data, so judges can try it without an AI quota. The owner's Telegram bot (`@neo_ledgercraft_bot`) and the private dashboard run the full Gemini pipeline and only answer the owner.

---

## What is built

| Area | Status |
|---|---|
| Receipt intake: typed notes, photos, PDFs, multi-receipt photos, bank statements (Telegram); notes, up to 10 photos or one PDF (web) | Live |
| Work or personal question, ABN / PAYG / Personal streams, business share, 23 categories, ATO myTax labels | Live |
| Vendor memory, duplicate detection, "Needs review" flag, double-entry ledger | Live |
| Womby companion: +10 XP per recorded receipt, no streaks, no penalties, reward box | Live |
| 3D office with live agent activity and task board | Live |
| Owner dashboard (installable on a phone): Receipts, myTax, Books, CSV export | Live, owner only |
| Telegram commands: `/recent`, `/mytax`, `/pnl`, `/spending`, `/balance`, `/dashboard`, `/reward`, `/companion`, `/backup` and more | Live, owner only |
| Physical desk companion (ESP32-C3, about $6.10 AUD in parts) | Design: bill of materials and 3D assembly guide, not yet built |

Automated tests: **217 tests and 28 subtests pass** (13 November 2026).

---

## Quick Navigation

- 🤖 **[`AGENTS.md`](./AGENTS.md)** — Workspace rules and architecture index for coding agents.
- 📦 **[`CHATGPT_MASTER_CONTEXT.md`](./Neo-D%20LedgerCraft/docs/CHATGPT_MASTER_CONTEXT.md)** — Consolidated project dossier for one-file review.
- 🌐 **[`docs/wsti_online_demo.md`](./Neo-D%20LedgerCraft/docs/wsti_online_demo.md)** — How the public web demo works, its limits and data handling.
- ☁️ **[`docs/deploy_aws.md`](./Neo-D%20LedgerCraft/docs/deploy_aws.md)** — AWS EC2 deployment runbook.
- 📓 **[`docs/notebooklm_project_brief.md`](./docs/notebooklm_project_brief.md)** — Presentation dossier.
- 🧠 **[`docs/companion_problem_statement.md`](./docs/companion_problem_statement.md)** — Problem statement, ADHD Tax research synthesis and market benchmarks.
- 🛠️ **[`docs/hardware_bom_and_kitcard_spec.md`](./docs/hardware_bom_and_kitcard_spec.md)** — Desk companion bill of materials ($6.10 AUD standard) and 3D-printable snap-fit Kit Card specification.
- 🧊 **[`docs/assembly_guide_3d.html`](./docs/assembly_guide_3d.html)** — Interactive 3D step-by-step assembly guide for the Kit Card.
- 🎯 **[`.agents/skills/adhd-neurodivergent-ux/SKILL.md`](./.agents/skills/adhd-neurodivergent-ux/SKILL.md)** — Reusable ADHD and neurodivergent UX design skill.
- 🧪 **[`Neo-D LedgerCraft/config/demo_fictional_data.json`](./Neo-D%20LedgerCraft/config/demo_fictional_data.json)** — Clearly labelled synthetic sample dataset.

---

## The 4-agent crew ([`Neo-D LedgerCraft/src/agents/`](./Neo-D%20LedgerCraft/src/agents/))

AI is used in one place only: reading the receipt. Everything else is rules, memory and the user's decisions.

1. **Neo, Chief of Staff ([`chief_of_staff.py`](./Neo-D%20LedgerCraft/src/agents/chief_of_staff.py))**
   - Receives single receipts, multi-receipt photos, typed notes and bank statements (CSV/PDF/image) from Telegram (`@neo_ledgercraft_bot`) or the web demo.
   - Keeps the original file in Google Drive (Telegram), runs the agent pipeline, awards Womby's `+10 XP` and replies to the user.
2. **🐕 Corgi, Finance ([`finance_agent.py`](./Neo-D%20LedgerCraft/src/agents/finance_agent.py))**
   - Extracts vendor, date (`YYYY-MM-DD`), total (`$ AUD`) and 10% GST, and classifies **Personal vs. Business** (`Dog Walking` / `3D Printing` ABN activities) and **ATO myTax** labels in **one multimodal LLM call**.
3. **🐻‍❄️ Bear, QA ([`qa_agent.py`](./Neo-D%20LedgerCraft/src/agents/qa_agent.py))**
   - Deterministic checks for completeness, date validity, `AUD` currency, 10% GST maths and duplicates (SHA-256 hash and bank statement reconciliation). Flags, never deletes.
   - Asks Corgi to re-read up to **2 times (`max_retries = 2`)** when something looks wrong.
4. **🦒 Girraffe, Knowledge Manager ([`knowledge_manager.py`](./Neo-D%20LedgerCraft/src/agents/knowledge_manager.py) & [`memory.py`](./Neo-D%20LedgerCraft/src/storage/memory.py))**
   - Posts balanced double-entry journals (`Debit = Credit`) and keeps **Vendor Memory**, so a confirmed shop is sorted the same way next time.

---

## Running Locally

```bash
# 1. Public web demo (fictional samples): http://localhost:8090/demo
cd "Neo-D LedgerCraft"
.venv/bin/python -m src.interfaces.demo_server --mode fixture

# 2. Owner dashboard & API server (http://localhost:8080/web/)
.venv/bin/python -u -m src.interfaces.web_server

# 3. Telegram bot (@neo_ledgercraft_bot, needs your own bot token in .env)
.venv/bin/python -u -m src.interfaces.telegram_bot

# 4. Test suite
.venv/bin/python -m unittest discover tests
```
