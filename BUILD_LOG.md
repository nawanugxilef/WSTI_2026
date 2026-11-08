# WSTI 2026 Contest Build Log & Event Disclosure

**Project:** Neo-D LedgerCraft — The ADHD Double-Entry Accounting & Physical Companion  
**Team / Author:** Felix Gunawan  
**Contest Build Window:** November 5–17, 2026 (Final Submission: Tuesday, Nov 17, 2026, 2:00 PM AEDT)  
**Pitch Night:** November 20, 2026  
**Auditor / QA Agent:** Claude  
**Lead Builder / Coder:** Antigravity  

---

## 1. Transparency & Baseline Disclosure

In strict compliance with WSTI 2026 hackathon rules and judging criteria, this disclosure clarifies the project timeline, baseline provenance, and active contest development:

1. **Repository Genesis:**
   - This repository was created and initialized on **November 7, 2026** (initial commit `9d4bbef`), squarely within the official November 5–17 build period.
   - Every line of code, configuration, test, and documentation in this repository has been written from November 7 onwards.
2. **Prior Background & Independent Work:**
   - Prior to the hackathon, Felix experimented with a private, unreleased personal receipt bot for personal use only.
   - That personal experiment served solely as inspiration and domain discovery. **Zero code, libraries, or files from that private project were copied or reused in this repository.**
   - This clean-slate development approach was reviewed and cleared with contest organizers on November 7, 2026.
3. **Public Git Audit Trail:**
   - All commits are public on GitHub (`https://github.com/nawanugxilef/WSTI_2026`) with chronological Git timestamps and complete test validation runs.
   - Repository history is scanned for API keys, tokens, and personal data on every push by independent QA (Claude); none have been found.

---

## 2. Contest Build Workplan (Nov 7–17, 2026)

The development roadmap completed and underway during the contest build period:

### 2.1 Core Accounting & Multi-Agent Architecture (Nov 7–8, 2026)
- **Double-Entry Relational Engine:** Strict mathematical conservation (`Debit = Credit`), SQLite relational storage (`ledger.db`), accounts chart, and transaction logs.
- **Single-Call Finance AI (`FinanceAgent`):** Multimodal classification of merchant, date, AUD total, 10% GST, business percentage, and ATO myTax labels in a single structured LLM call.
- **Deterministic QA Auditor (`QAAgent`):** Automated checks for valid dates, AUD currency, 1/11th GST sanity bounds, SHA-256 duplicate detection, and retry loops up to `max_retries = 2`.
- **Knowledge Manager & Vendor Memory:** Structured double-entry journals and automatic reuse of past vendor classifications.
- **ADHD-Optimized Intake & Corrections:** Casual text note intake, instant 1-tap inline corrections in Telegram, and non-punitive gamification (+XP rewards without streaks or penalties).

### 2.2 Security Hardening & DevOps (Nov 8, 2026)
- **Telegram Bot Authorization:** Fail-closed authorization enforcing numeric `TELEGRAM_OWNER_CHAT_ID`.
- **Web Server Protection:** `ThreadingHTTPServer` bound to `127.0.0.1`, input validation on all mutation endpoints, timing-safe HMAC token comparison, and disposable `DEMO_MODE`.
- **Deployment Blueprint (`docs/deploy_aws.md`):** Complete AWS EC2 deployment guide with non-root system user, systemd service units, swapfile allocation, and billing alarms.

### 2.3 Live Pitch Night Demonstration (Nov 17–20, 2026)
- **Web Intake UI:** Zero-friction typed expense note and receipt processing directly in the web dashboard for interactive stage demos without mobile phone dependency.
- **Physical Companion Preview:** Hardware kit card assembly guide (`docs/hardware_bom_and_kitcard_spec.md` and `docs/assembly_guide_3d.html`) and serial protocol specification for physical desk companion boxes.
