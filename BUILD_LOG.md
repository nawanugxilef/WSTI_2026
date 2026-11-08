# WSTI 2026 Contest Build Log & Pre-Event Disclosure

**Project:** Neo-D LedgerCraft — The ADHD Double-Entry Accounting & Physical Companion  
**Team / Author:** Felix Gunawan  
**Contest Date:** November 13–14, 2026  
**Auditor / QA Agent:** Claude  
**Lead Builder / Coder:** Antigravity  

---

## 1. Transparency & Baseline Disclosure

In strict compliance with hackathon transparency rules, this log explicitly delineates all foundational engineering completed prior to the competition kickoff versus features and hardening developed live during the contest period.

### 1.1 Pre-Contest Hardening & Baseline (Completed by Nov 8, 2026)
1. **Core Accounting Engine:**
   - Double-entry bookkeeping system enforcing balanced journal legs (`Debit = Credit`).
   - SQLite relational storage (`ledger.db`) with accounts, journal transactions, receipts, and vendor rules.
   - Australian Taxation Office (ATO) myTax categorization and 10% GST mathematical validation.
2. **4-Agent Multi-Agent Architecture:**
   - **Intake / Chief of Staff (`ChiefOfStaff`):** Multi-modal receipt ingestion, bank statements, and casual note parsing.
   - **Finance AI (`FinanceAgent`):** Single multimodal LLM call for merchant, date, total, GST, business %, stream (ABN/PAYG/Personal), and myTax label classification.
   - **Deterministic QA Auditor (`QAAgent`):** Validates future dates, AUD currency checks, GST sanity limits (max 1/11th), duplicate detection, and triggers re-analysis up to `max_retries = 2`.
   - **Knowledge Manager (`KnowledgeManager`):** Memory persistence for learned vendor rules and structured correction application.
3. **Security & Deployment Hardening:**
   - Fail-closed Telegram bot authorization restricting operations strictly to `TELEGRAM_OWNER_CHAT_ID`.
   - Web server security hardening (`ThreadingHTTPServer`, input validation, bearer token auth, timing-safe HMAC verification).
   - Full secrets audit ensuring zero production credentials, API keys, or personal financial data in repository history.
   - Cloud deployment blueprints (`docs/deploy_aws.md`).

---

## 2. Contest Build Workplan (Nov 13–14, 2026)

The following milestone items are reserved for live demonstration and live execution during the contest window:

- **Milestone A (Live Web Intake):** Real-time web photo upload and casual typed note processing wired to the Chief of Staff pipeline for live pitch-night demonstration without requiring a mobile phone.
- **Milestone B (IoT Hardware Companion Sync):** ESP32 / Arduino serial protocol and physical dopamine feedback trigger (`+10 XP` servo and LED animation upon receipt confirmation).
- **Milestone C (Pitch Night Interactive Demo):** 3D printable kit card assembly walkthrough and live ATO deduction reporting dashboard review.

---

## 3. Commit Audit Trail

All commits are cryptographically signed and pushed directly to `https://github.com/nawanugxilef/WSTI_2026` with full automated test suite validation passing on every commit.
