# Neo-D LedgerCraft & Desk Companion: Comprehensive Dossier for NotebookLM

---

## 1. Executive Summary & Core Elevator Pitch
**Neo-D LedgerCraft** is an AI-powered personal financial assistant and physical IoT desk companion engineered specifically for individuals with **Attention Deficit Hyperactivity Disorder (ADHD)** and executive dysfunction.

By combining:
1. **Multimodal AI Vision & Autonomous Agents** (via Telegram),
2. **A \$5 Open-Hardware Physical Desk Companion** (ESP32 + OLED Kit Card), and
3. **Neuropsychology-Backed, Non-Punitive Gamification** (Dodson's ICNU & Barkley's Point-of-Performance),

the system completely eliminates the administrative friction of financial tracking, stops the financially devastating **"ADHD Tax"**, and restores autonomy without guilt or cognitive overload.

---

## 2. The Core Problem: The "ADHD Tax" & Executive Dysfunction

### What is the ADHD Tax?
Adults with ADHD frequently incur thousands of dollars in annual financial losses not due to a lack of intelligence, but due to neurological deficits in working memory, time-blindness, and dopamine regulation:
- **Lost Tax Deductions**: Physical receipts for work-related expenses (e.g., ATO claims in Australia) are misplaced, discarded, or forgotten in pockets and drawers.
- **Dread & Procrastination**: Financial interfaces are typically rigid, dense, and punishing. The friction of manually logging transactions freezes executive function (*executive paralysis*).
- **The "Out of Sight, Out of Mind" Phenomenon**: If an expense app is closed on a smartphone, it ceases to exist in the user's conscious working memory.

### Why Existing Solutions Fail:
- **Traditional Budgeting Apps (YNAB, PocketSmith, Excel)**: Require high sustained executive effort, complex category sorting, and manual reconciliation. Most ADHD users burn out and abandon them in under a week.
- **Gamified Apps & "Financial Tamagotchis"**: Threaten users with starving or dying virtual pets if they fail to budget. This activates **Rejection Sensitive Dysphoria (RSD)** and shame spirals, causing users to delete the app.
- **Manual Outsourcing (e.g., MyBudget in Australia)**: Costs thousands of dollars annually, removing personal agency and creating financial dependence.

---

## 3. The Vital & Indispensable Role of AI in this Project

AI is not an aesthetic add-on or a buzzword in this project; **it is the foundational engine that makes the system accessible for neurodivergent cognition.** 

Without AI, the entire solution collapses back into traditional high-friction manual labor.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        HOW AI POWERS NEO-D                             │
├─────────────────────────┬──────────────────────────┬───────────────────┤
│    1. AI MULTIMODAL     │  2. AUTONOMOUS MULTI-    │   3. BEHAVIORAL   │
│        VISION           │       AGENT TEAM         │    PSYCHOLOGY     │
├─────────────────────────┼──────────────────────────┼───────────────────┤
│ • Zero-click data entry │ • Chief of Staff Agent   │ • Zero-shame tone │
│ • Wrinkled/faded OCR    │ • Finance Tax Agent      │ • RSD-safe dialog │
│ • Multilingual parse    │ • Deterministic QA Agent │ • Ally stance     │
│ • 1-second capture      │ • Vendor Memory Learner  │ • Dynamic XP eval │
└─────────────────────────┴──────────────────────────┴───────────────────┘
```

### A. AI Multimodal Vision: The Prosthetic Prefrontal Cortex
* **Inverting the Friction Curve**: Tapping a credit card to spend money takes 1 second (zero friction). Recording a transaction manually takes 5–10 cognitive steps.
* **The AI Solution**: The user snaps a single messy, crumpled, or poorly-lit photo of a receipt in Telegram. 
* **What the AI Does**: Advanced Multimodal LLM (Gemini Vision) extracts:
  - Merchant name & date
  - Line-item details and quantities
  - Subtotal, GST/Tax, and Total
  - Payment method
  All in **under 2 seconds**, with zero forms or manual typings required from the user.

### B. Autonomous 4-Agent Architecture: Deployed Skills & Responsibilities
People with ADHD struggle to initiate and organize multi-step administrative tasks. **Neo-D LedgerCraft** deploys **4 specialized AI Agents**, each with distinct skills and strict architectural boundaries:

1. **Agent 1 — Chief of Staff / Intake (`chief_of_staff.py`)**:
   - **Skills**: Receives incoming receipts, multi-receipt batch photos, bank statements (CSV/PDF/Images), or casual text notes from **Telegram** (`@neo_ledgercraft_bot`) or **Email**.
   - **Responsibilities**: Archives the original raw files locally and to **Google Drive**, orchestrates the Build–Review–Revise pipeline across the other agents, triggers the **IoT Dopamine Reward Box (`+10 XP`)**, and delivers an instant, RSD-safe response back to the user.

2. **Agent 2 — Finance AI (`finance_agent.py`)**:
   - **Skills**: Extracts vendor name, transaction date (`YYYY-MM-DD`), total amount (strictly `$ AUD`), and 10% Australian GST; simultaneously suggests the expense category, splits **Personal vs. Business** (`Dog Walking` or `3D Printing`), and assigns the official **Australian ATO myTax deduction code** (`D1–D5` / `ABN Business`).
   - **Key Architectural Advantage (Single-Call Efficiency)**: **Both OCR reading AND financial/tax categorization are executed inside a SINGLE multimodal LLM call (`1 API Call`)**, rather than splitting OCR and categorization across two separate agents. This cuts latency in half (< 2.5 seconds) and prevents context loss between line items and tax rules.

3. **Agent 3 — QA / Reviewer (`qa_agent.py`)**:
   - **Skills**: Deterministic financial auditor that inspects completeness, validates dates (blocking future dates or malformed formats), enforces strict `$ AUD` currency, verifies 10% GST math, and checks for duplicates (via SHA-256 file hash and vendor/date/amount matching).
   - **Self-Correction Loop (Max 2 Retries)**: If QA detects any issue or discrepancy, it does not bother the user—instead, it sends structured diagnostic feedback back to **Finance AI** to re-read the receipt up to a **maximum of 2 retries (`max_retries = 2`)**. If an item still needs human attention after 2 retries, it is recorded with a clear `flagged` notice.

4. **Agent 4 — Knowledge Manager (`knowledge_manager.py` & `memory.py`)**:
   - **Skills**: Posts balanced Double-Entry Bookkeeping entries (`Debit = Credit` in SQLite) across Personal and ABN Business accounts (`Dog Walking` & `3D Printing`) and manages **Vendor Memory**.
   - **Adaptive Learning**: Stores historical classifications and user corrections per vendor in `vendor_memory`. Whenever the user buys from the same merchant again (e.g., fictional demo stores like *Sydney 3D Maker Supplies* or *Centennial Park Pet Gear*), Knowledge Manager injects those past examples into Finance AI so corrections are automatically reused forever.

---

### C. ADHD Safety Net: Multi-Receipt Batching & Bank Statement AI Auto-Sync
Even with a 1-step Telegram bot, ADHD users will occasionally forget to photograph receipts during a busy week (*"what if some receipts were missed?"*). Neo-D includes a **2-Layer ADHD Safety Net**:
1. **Multi-Receipt Batch Capture**:
   - Users can lay 3–5 crumpled receipts on a desk and snap **one single photo** (or upload multiple photos/PDFs at once). Finance AI extracts all receipts in one pass.
2. **Bank Statement Upload & AI Auto-Synchronization (`process_batch_or_statement`)**:
   - At the end of the week or month, the user can upload their Australian **Bank Statement (CSV, PDF, or Screenshot)** directly to the Telegram bot.
   - **Automatic Reconciliation & Deduplication**: Chief of Staff & QA Agent cross-check every bank statement row against existing receipts in SQLite (`ledger.db`) by date and `$ AUD` amount:
     - **Already Logged (`✅ Matched`)**: Transactions that already have a photographed receipt are automatically linked and **skipped**—preventing duplicate entries.
     - **Missed Receipts (`🔄 Auto-Synced`)**: Any forgotten transaction that was missed during the week is automatically categorized by Finance AI, verified by QA, recorded into the ledger by Knowledge Manager, and awarded catch-up XP!

### D. Conversational Framing for RSD (Rejection Sensitive Dysphoria)
* People with ADHD are hyper-sensitive to criticism and moralizing language.
* Traditional software uses robotic warnings (*"Warning: You exceeded your budget!"*).
* **AI Behavioral Conditioning**: The LLM prompt architecture enforces **"The Loyal Ally Stance"**:
  - Unconditional validation (*"Receipt locked in, boss! That’s another \$15.00 AUD saved for your tax return."*)
  - Forgiving re-entry (*"Welcome back! All your logs are safe and ready whenever you want to glance at them."*)
  - Eliminates all moralizing about spending (logging a luxury splurge earns the exact same praise as logging groceries).

### D. AI as the Event Bridge to the Physical World (IoT Desk Companion)
* AI does not keep the user glued to a screen.
* Upon completing extraction and verification, the AI backend computes the gamification event (XP earned, progress milestone, reward box status) and publishes a lightweight JSON packet over MQTT/WebSocket to the **ESP32 physical desk companion**.
* This triggers physical tactile feedback (OLED animation, buzzer chime) directly in the user's physical workspace.

---

## 4. Hardware Innovation: The \$6 AUD "Kit Card" Desk Companion

* **Form Factor**: A 3D-printable "Kit Card" (135 × 95 × 2.4 mm) that prints flat in 45 minutes on any basic 3D printer.
* **Tool-Free Assembly**: Parts snap cleanly off the runner frame and lock together via friction-fit tabs and dovetails. **No screws, no glue, no soldering required.**
* **Bill of Materials (< \$7.50 AUD / ~\$6.10 AUD standard configuration)**:
  - ESP32-C3 SuperMini (Type-C, Wi-Fi, BLE)
  - 0.96" I2C OLED (SSD1306, 128×64)
  - 3.3V Passive Piezo Buzzer (for soft melodic chimes)
  - 6x Dupont jumper cables
  - 28g PLA filament
* **Point-of-Performance Scaffolding (Dr. Russell Barkley)**:
  Sits directly under the monitor at an ergonomic 60° angle. Destroys the "out-of-sight, out-of-mind" curse by acting as an ambient visual reminder in the real world.

---

## 5. Non-Punitive Gamification Mechanics

* **Dodson's ICNU Framework**:
  - **I (Interest)**: Expressive animated mascot (`idle`, `happy`, `reward_unlocked`, `level_up`).
  - **C (Challenge)**: Cumulative progress bar climbing toward self-defined goals.
  - **N (Novelty)**: Tactile hardware unboxing + randomized celebratory animations.
  - **U (Urgency)**: Gentle ambient visual presence without threatening alarms.
* **Zero Streaks / No Penalties**: Missing days never resets XP to zero. The economy is strictly additive.
* **The "Indomie Box" Reward Schema**:
  - Users define their own real-world treats (e.g., *"Eat 2 packs of Indomie"*, *"30 mins gaming break"*).
  - When the cumulative XP threshold is hit, the desk companion celebrates and "unlocks" the box. The reward is a gift, never a hostage permit.

---

## 6. Social & Economic Impact (Australia & Global)

* **Aligned with NDIS (National Disability Insurance Scheme)**:
  In Australia, low-cost assistive technology for executive functioning is eligible for NDIS self-managed support. Neo-D delivers an industrial-grade assistive solution for under \$10 AUD, replacing multi-thousand-dollar caseworker services.
* **Combating Wealth Drainage**: Ensures freelancers, gig workers, and neurodivergent professionals retain 100% of their eligible tax deductions.

---

## 7. Key Talking Points for Presentations

1. *"We didn't build another budgeting app. We built an AI prosthetic for the prefrontal cortex."*
2. *"Traditional apps fail ADHD brains because they demand executive function to fix executive function. We externalize the work entirely."*
3. *"AI acts as the silent executive staff; the physical companion acts as the joyful desk ally."*
4. *"Zero friction. Zero guilt. Zero lost receipts."*

---

## 8. Copy-Paste Prompt for NotebookLM "Audio / Video Overview" (Customize Instructions)

When generating an **Audio Overview** or **Video / Slide Script** inside Google NotebookLM, click **Customize** and paste the following prompt:

```text
Create an engaging, clear walkthrough for first-time viewers explaining how Neo-D LedgerCraft solves the "ADHD Tax" for a sole trader in Sydney, Australia (strictly in $ AUD).

Make sure to cover these 4 core pillars in order:
1. The Problem: Why ADHD brains abandon traditional finance apps (manual data entry friction, out-of-sight out-of-mind, and punishing streak resets).
2. The 4 Deployed AI Agents & Their Specific Skills:
   - Agent 1: Chief of Staff / Intake (receives receipts via Telegram or email, backs up raw files to Google Drive, orchestrates the workflow, and replies with instant XP).
   - Agent 2: Finance AI (reads vendor, date, $ AUD total, and 10% GST while simultaneously categorizing Personal vs. Business and ATO myTax codes in ONE single LLM model call, not two separate agents).
   - Agent 3: QA / Reviewer (deterministic auditor checking completeness, date validity, AUD currency, GST math, and duplicates; if any issue is found, it asks Finance AI to re-read up to a maximum of 2 retries).
   - Agent 4: Knowledge Manager (records Double-Entry Bookkeeping for Personal and ABN Business activities—Dog Walking and 3D Printing—and stores Vendor Memory so user corrections are reused automatically for the same vendor).
3. The ADHD Safety Net for Missed Receipts: Explain how users can photograph multiple crumpled receipts at once OR upload a Bank Statement (CSV/PDF), and the AI automatically synchronizes—skipping already-logged receipts so there are zero duplicates, and auto-recording only the missed transactions.
4. The Physical $6.10 AUD ESP32 Kit Card & Customizable Reward Box: Explain how every logged receipt gives +10 XP (never resets) toward a user-customizable physical treat (like Indomie or Matcha) locked in the desk companion.
```
