# WSTI 2026 Contest Build Log & Event Disclosure

**Project:** Neo-D LedgerCraft — The ADHD Double-Entry Accounting & Physical Companion  
**Team / Author:** Felix Gunawan  
**Contest Build Window:** November 5–17, 2026 (Final Submission: Tuesday, Nov 17, 2026, 2:00 PM AEDT)  
**Pitch Night:** November 20, 2026  
**QA / Reviewers:** ChatGPT (Codex) and Claude  
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

### 2.2 Security Hardening & Web Intake (Nov 8, 2026)
- **Telegram Bot Authorization:** Fail-closed authorization enforcing numeric `TELEGRAM_OWNER_CHAT_ID`.
- **Web Server Protection & Intake:** `ThreadingHTTPServer` bound to `127.0.0.1`, input validation on mutation endpoints, timing-safe HMAC token comparison, fast zero-friction note intake (`POST /api/intake`), and isolated `DEMO_MODE` with disposable demo database (`data/demo.db`).
- **Deployment Blueprint (`docs/deploy_aws.md`):** Complete AWS EC2 deployment guide with non-root system user, systemd service units, swapfile allocation, and billing alarms.

### 2.3 3D Office & Agent Personalities (Nov 9, 2026)
- **Interactive 3D Office:** The agents appear as characters at their desks: Neo (Chief of Staff), Corgi (Finance), Bear (QA) and Girraffe (Knowledge Manager), with Felix in the boss office.

### 2.4 Telegram, Drive & Owner Dashboard (Nov 11, 2026)
- **Receipt Intake Fixes:** Safe posting, compact Telegram receipt cards with category icons, manual capture and Womby receipt feedback.
- **Google Drive Originals:** Uploaded receipt originals are stored only in the owner's Google Drive.
- **Live Office Activity:** The 3D office follows real receipt stages, with a task board (Inbox, In progress, Review, Done) and an activity calendar.
- **Mobile Owner Dashboard:** Installable dashboard with Telegram one-time sign-in and Receipts, myTax and Books tabs.
- **Corrections & Duplicates:** One-tap receipt corrections; a resent duplicate opens the original receipt.

### 2.5 Public Web Demo & Server Deployment (Nov 12–13, 2026)
- **Isolated Web Demo:** No-login demo with a private 30-minute session and its own temporary ledger per visitor, fixture extraction for the fictional samples, up to 10 photos per send, live agent activity and the Womby 3D companion. Reached from **Try Neo** at [neo.madewithfelix.app](https://neo.madewithfelix.app/).
- **Smarter Notes:** Written dates, shop names and short informal notes are understood. An unfamiliar personal item is recorded straight away with Neo's best guess, and one tap changes its category or moves it to ABN or PAYG.
- **One-Command Deploy:** A single server command deploys the web demo, Telegram bot and owner dashboard from this repository to AWS EC2 (`m7i-flex.large`, Ubuntu 24.04, Sydney).
- **Test Suite:** 217 tests and 28 subtests passing on 13 November 2026.

### 2.6 Pitch Night Preparation & Hardware Sync (Nov 17–20, 2026)
- **Pitch Night Interactive Demo:** Interactive stage demo featuring live web receipt intake and ATO deduction reporting review.
- **Physical Companion Preview:** Hardware kit card assembly guide (`docs/hardware_bom_and_kitcard_spec.md` and `docs/assembly_guide_3d.html`) and serial protocol specification for physical desk companion boxes.
