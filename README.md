# Neo-D LedgerCraft & IoT Desk Companion (`WSTI_2026`)

An ADHD-friendly autonomous financial assistant and **$6.10 AUD ESP32-C3 Physical Desk Companion** designed for sole traders in Sydney, Australia. Built strictly in **Australian Dollars (`$ AUD`)** with **ATO myTax** compliance (covering **Dog Walking** and **3D Printing** ABN activities) and **Non-Punitive Gamification**.

> **Privacy Notice:** All example vendors, receipts, and transactions in this repository are **100% synthetic (`[FICTIONAL DEMO]`)**. No real financial records, account balances, or credentials are stored in this repository.

---

## Quick Navigation (For Humans, Antigravity & ChatGPT)

- 🤖 **[`AGENTS.md`](./AGENTS.md)** — Native Antigravity & OpenAI Codex/ChatGPT workspace rules and architecture index.
- 📦 **[`CHATGPT_MASTER_CONTEXT.md`](./CHATGPT_MASTER_CONTEXT.md)** — All-in-One consolidated project dossier + 4-Agent Python source code for instant 1-file review in ChatGPT.
- 📓 **[`docs/notebooklm_project_brief.md`](./docs/notebooklm_project_brief.md)** — Presentation dossier and copy-paste prompts for Google NotebookLM Audio/Video Overview.
- 🧠 **[`docs/companion_problem_statement.md`](./docs/companion_problem_statement.md)** — Problem Statement, ADHD Tax research synthesis, NDIS alignment, and market benchmarks.
- 🛠️ **[`docs/hardware_bom_and_kitcard_spec.md`](./docs/hardware_bom_and_kitcard_spec.md)** — `< $10 AUD` ($6.10 AUD standard) Bill of Materials and 3D-printable snap-fit Kit Card specification.
- 🧊 **[`docs/assembly_guide_3d.html`](./docs/assembly_guide_3d.html)** — Interactive 3D WebGL step-by-step assembly manual for the Kit Card.
- 🎯 **[`.agents/skills/adhd-neurodivergent-ux/SKILL.md`](./.agents/skills/adhd-neurodivergent-ux/SKILL.md)** — Reusable ADHD & Neurodivergent UX/IoT design skill.
- 🧪 **[`Neo-D LedgerCraft/config/demo_fictional_data.json`](./Neo-D%20LedgerCraft/config/demo_fictional_data.json)** — Clearly labelled synthetic sample dataset.

---

## Deployed 4-Agent AI Architecture ([`Neo-D LedgerCraft/src/agents/`](./Neo-D%20LedgerCraft/src/agents/))

1. **🐕 Chief of Staff / Intake ([`chief_of_staff.py`](./Neo-D%20LedgerCraft/src/agents/chief_of_staff.py))**
   - Receives single receipts, multi-receipt batch photos, casual notes, and bank statements (CSV/PDF/Image) via Telegram (`@neo_ledgercraft_bot`) or Email.
   - Archives raw files locally and to Google Drive, runs the agent pipeline, triggers the IoT Dopamine Reward Box (`+10 XP`), and replies to the user.
2. **🦊 Finance AI ([`finance_agent.py`](./Neo-D%20LedgerCraft/src/agents/finance_agent.py))**
   - Extracts vendor, date (`YYYY-MM-DD`), total (`$ AUD`), and 10% GST while simultaneously classifying **Personal vs. Business** (`Dog Walking` / `3D Printing` ABN activities) and **Australian ATO myTax** labels in **ONE single multimodal LLM call** (not two separate agents).
3. **🐻‍❄️ QA / Reviewer ([`qa_agent.py`](./Neo-D%20LedgerCraft/src/agents/qa_agent.py))**
   - Deterministic auditor checking completeness, date validity, `AUD` currency, 10% GST math, and duplicate detection (SHA-256 hash + bank statement auto-reconciliation).
   - Requests Finance AI to re-read up to a **maximum of 2 retries (`max_retries = 2`)** if issues are detected.
4. **🦒 Knowledge Manager ([`knowledge_manager.py`](./Neo-D%20LedgerCraft/src/agents/knowledge_manager.py) & [`memory.py`](./Neo-D%20LedgerCraft/src/storage/memory.py))**
   - Posts balanced Double-Entry Bookkeeping entries (`Debit = Credit`) and persists **Vendor Memory** so past categorizations and corrections are automatically reused for recurring merchants.

---

## Running Locally

```bash
# 1. Start the Live Web Dashboard & API Server (http://localhost:8080/web/)
cd "Neo-D LedgerCraft"
.venv/bin/python -u -m src.interfaces.web_server

# 2. Start the Telegram Bot (@neo_ledgercraft_bot)
cd "Neo-D LedgerCraft"
.venv/bin/python -u -m src.interfaces.telegram_bot

# 3. Run the Deterministic Test Suite
cd "Neo-D LedgerCraft"
.venv/bin/python -m unittest discover tests
```
