# Neo-D LedgerCraft — Antigravity Workspace Rules & Architecture Index

## 1. Strict Project Rules (Always On)
- **Language:** 100% English across all source code, comments, UI templates, Telegram bot messages, and documentation.
- **Currency:** Strictly **Australian Dollars (`$ AUD`)** only. Never use or reference IDR, Rupiah, or USD.
- **Privacy & Demo Data:** Never commit API keys, bot tokens, credentials, real Google Drive folder IDs, or real financial data/screenshots. All demo or sample data must be clearly labelled as `[FICTIONAL DEMO]`.
- **Database Integrity:** Never hardcode dummy financial totals into [`web/index.html`](./Neo-D%20LedgerCraft/web/index.html). All UI views must query live SQLite data from `./data/ledger.db` via [`src/interfaces/web_server.py`](./Neo-D%20LedgerCraft/src/interfaces/web_server.py).

## 2. Deployed 4-Agent AI Architecture
1. **Chief of Staff / Intake ([`src/agents/chief_of_staff.py`](./Neo-D%20LedgerCraft/src/agents/chief_of_staff.py))**:
   - Receives single receipts, multi-receipt batch photos, casual notes, and bank statements (CSV/PDF/Image) via Telegram (`@neo_ledgercraft_bot`) or Email.
   - Stores verified originals in Google Drive. Uploaded file bytes are processed in memory, then discarded. Runs the agent pipeline, triggers the IoT Dopamine Reward Box (`+10 XP`), and replies to the user.
2. **Finance AI ([`src/agents/finance_agent.py`](./Neo-D%20LedgerCraft/src/agents/finance_agent.py))**:
   - Reads vendor, date (`YYYY-MM-DD`), total (`$ AUD`), and 10% GST while simultaneously classifying **Personal vs. Business** (`Dog Walking` / `3D Printing` ABN activities) and **Australian ATO myTax** labels in **ONE single multimodal LLM call** (not two separate agents).
3. **QA / Reviewer ([`src/agents/qa_agent.py`](./Neo-D%20LedgerCraft/src/agents/qa_agent.py))**:
   - Deterministic auditor checking completeness, date validity, `AUD` currency, 10% GST math, and duplicate detection (SHA-256 hash + bank statement reconciliation).
   - Requests Finance AI to re-read up to a **maximum of 2 retries (`max_retries = 2`)** if issues are detected.
4. **Knowledge Manager ([`src/agents/knowledge_manager.py`](./Neo-D%20LedgerCraft/src/agents/knowledge_manager.py) & [`src/storage/memory.py`](./Neo-D%20LedgerCraft/src/storage/memory.py))**:
   - Posts balanced Double-Entry Bookkeeping entries (`Debit = Credit`) and persists **Vendor Memory** so past categorizations and user corrections are automatically reused for recurring merchants.

## 3. Native Antigravity Customizations & Documentation Index
- **Workspace Skill:** [`.agents/skills/adhd-neurodivergent-ux/SKILL.md`](./.agents/skills/adhd-neurodivergent-ux/SKILL.md)
- **NotebookLM Presentation Dossier:** [`docs/notebooklm_project_brief.md`](./Neo-D%20LedgerCraft/docs/notebooklm_project_brief.md)
- **Problem Statement & Research Synthesis:** [`docs/companion_problem_statement.md`](./Neo-D%20LedgerCraft/docs/companion_problem_statement.md)
- **Hardware BOM & Kit Card Spec:** [`docs/hardware_bom_and_kitcard_spec.md`](./Neo-D%20LedgerCraft/docs/hardware_bom_and_kitcard_spec.md)
- **Interactive 3D Kit Card Assembly Guide:** [`docs/assembly_guide_3d.html`](./Neo-D%20LedgerCraft/docs/assembly_guide_3d.html)
- **Fictional Demo Dataset:** [`Neo-D LedgerCraft/config/demo_fictional_data.json`](./Neo-D%20LedgerCraft/config/demo_fictional_data.json)
