# Problem Statement & Research Synthesis – Desk Companion for ADHD Users

---

## Executive Summary & Research Synthesis

Managing personal finances is one of the most taxing cognitive challenges for individuals with **Attention Deficit Hyperactivity Disorder (ADHD)**. The core breakdown is not a lack of financial knowledge, but **Executive Dysfunction, Time-Blindness, and Working Memory Deficits**—leading to what clinical psychology and policymakers in countries like Australia call the **"ADHD Tax"** (the persistent cost of lost receipts, missed tax deductions, late fees, and impulse buying).

### Why Traditional Solutions Fail ADHD Brains:
1. **The Software Paradox (Out-of-Sight, Out-of-Mind):** Traditional apps (YNAB, PocketSmith, WeMoney) require high initial activation energy (unlocking phones, entering spreadsheets, categorizing items). When an app is closed, it disappears from the ADHD user's working memory. Even gamified neobanks (such as Melbourne's **Up Bank**) remain locked inside a mobile OS and bank-specific ecosystem.
2. **The "Tamagotchi Trap" (Guilt & Shame Spirals):** Hobbyist attempts at financial gamification (e.g., wallet pets that get sick or die when users overspend) trigger **Rejection Sensitive Dysphoria (RSD)**. Rather than motivating change, punishment and guilt cause users to abandon or delete the app entirely.
3. **The Friction Asymmetry:** Tapping a card to make a purchase takes 1 second (zero friction, immediate dopamine). Logging and filing a receipt takes 5–10 cognitive steps (high friction, zero dopamine).

### The Proposed Paradigm:
To solve this, we introduce a **Multimodal Physical-Digital Scaffolding System**:
* **Zero-Friction Capture:** The user simply sends a raw receipt photo to a Telegram bot (`Neo-D LedgerCraft`). AI agents autonomously handle OCR, line items, and sanity checks.
* **Physical Desk Anchor:** A low-cost (< \$10 AUD), 3D-printed **ESP32 + OLED Desk Companion** sits directly at the user's workspace, externalizing executive function at the *Point of Performance* (Dr. Russell Barkley's rule).
* **100% Non-Punitive Dopamine Loop:** Instant visual/acoustic rewards (+XP, mascot celebration, progress bars) celebrate every act of recording, with zero streaks, zero penalties, and autonomous self-defined reward boxes.

---

## 1. Background & The "ADHD Tax"
- **Lost Tax Deductions & Reimbursable Claims**: Forgetting or losing physical and digital receipts (e.g., ATO work-related expense claims in Australia, business deductions, or insurance claims).
- **Avoidance & Late Penalties**: Procrastination and dread surrounding financial administration leading to overdue bills, lapsed warranties, and subscription renewals that were meant to be canceled.
- **Impulse Spending & Lack of Tangible Friction**: High sensitivity to immediate dopamine, combined with abstract digital payments (tap-to-pay, cards), disconnects spending from consequences.

> **Global & Australian Context**:
> In countries with high mental health awareness such as Australia, the *ADHD Tax* is frequently debated in parliament and within the **NDIS (National Disability Insurance Scheme)** community. The NDIS recognizes that executive dysfunction requires **assistive technology** rather than mere willpower. However, current market offerings remain fragmented, expensive, or counterproductive.

---

## 2. Market Benchmark & Existing Solutions (The Gap)

Current commercial and DIY solutions fail to solve this problem comprehensively for neurodivergent brains:

| Category | Representative Solutions | What They Do Well | The Fatal Flaw for ADHD |
| :--- | :--- | :--- | :--- |
| **ADHD-Friendly Neobanks** | **Up Bank** *(Melbourne, Australia)* | Gamified, responsive UI, visual "Savers" with custom emojis, automated bill-locking, and the *"Maybuy"* feature to delay impulse purchases. | **Software-only & bank-locked**: Does not serve as a general receipt/invoice capture tool across external payment methods or physical cash, and lacks a permanent, physical desk anchor. |
| **Automated Dashboards & Open Banking** | **PocketSmith**, **WeMoney**, **YNAB** | Calendar-based cash flow forecasting, multi-bank feed aggregation, granular category budgeting. | **Cognitive Overload**: Complex graphs, dense spreadsheets, and multi-step categorization demand sustained executive attention. Most ADHD users abandon them within 3–7 days ("App Burnout"). |
| **Manual / Outsourced Services** | **MyBudget** *(Australia)* | Professional human caseworkers take over income and allocate money on the user's behalf. | **Extremely Expensive & Disempowering**: Costs thousands of dollars annually and strips the user of self-efficacy and financial autonomy. |
| **Hobbyist "Financial Tamagotchis"** | *Finnagotchi*, virtual wallet pets | Turn savings or budgeting metrics into an animated pet. | **Punitive / Guilt-Driven Design**: The pet starves, gets sick, or dies when the user overspends or misses entries. For ADHD users, this triggers **Rejection Sensitive Dysphoria (RSD)** and shame spirals, causing immediate app deletion. |
| **Physical Desk Tools** | *Time Timer*, *Timeular*, *Pomodoro Cubes*, *Tidbyt* | Tangible presence on the desk; bypasses "out-of-sight, out-of-mind" time blindness. | **No Financial Connection**: Exclusively built for time management, pomodoros, or decorative pixel art; zero integration with receipt processing. |

---

## 3. As‑Is (Current Situation)
- **Executive Dysfunction & Time-Blindness**: Users plan to record receipts, but delay doing so immediately after purchase. Receipts pile up, fade, or get discarded.
- **"Out of Sight, Out of Mind"**: Once a banking or tracking app is closed on a smartphone, it ceases to exist in the user's working memory.
- **Guilt & Shame Spirals**: Missing a few days of entries triggers feelings of failure, leading to total abandonment of the tracking routine.
- **Fragmented Context-Switching**: Stopping a work session on a PC to unlock a phone, open a specialized budget app, type amounts, and categorize items breaks hyperfocus and introduces high friction.

---

## 4. To‑Be (Desired Future State)
- **Externalized Executive Support**: A dedicated, low-cost physical desk companion sits permanently in the user's line of sight, acting as a gentle environmental anchor.
- **Zero-Friction Ingestion**: The user simply snaps a photo of any receipt and drops it into a Telegram chat (`Neo-D LedgerCraft`). AI extracts all merchant and financial data automatically.
- **Instant Micro-Dopamine Feedback**: The moment the receipt is processed, the physical desk companion reacts instantly—displaying a cheerful animation, an audio cue, and advancing a progress bar.
- **100% Non-Punitive Gamification**: No streak resets, no penalties, no dying pets. Tracking is rewarded unconditionally.
- **Autonomous Reward Box**: Accumulated points unlock self-defined real-world rewards (e.g., a "2-pack Indomie treat", a gaming break), reinforcing positive financial habits without restricting basic needs.

---

## 5. Why This Companion Solves the Problem (The Novelty)
1. **Adherence to ICNU Psychology**: Leverages Interest (expressive mascot), Challenge (progress bars), Novelty (varied animations), and gentle cues rather than anxiety-driven deadlines.
2. **Environmental Scaffolding**: Moves the prompt from an ephemeral phone notification into the physical workspace, fulfilling Dr. Russell Barkley's core rule: *Executive function interventions must be situated at the point of performance in the physical environment.*
3. **High Affordability & Openness**: Built with ubiquitous, low-cost hardware (< \$10 AUD BOM: ESP32 + OLED + 3D printed housing), making it accessible as a low-cost assistive tool eligible for DIY or NDIS self-managed support.

---

## 6. Functional Requirements
- **Wi‑Fi Connectivity**: Auto-connect to local Wi-Fi with auto-reconnect and mDNS discovery.
- **Local Protocol Integration**: Subscribes to lightweight MQTT or WebSocket topics (e.g., `ledger/companion`) published by the `Neo-D LedgerCraft` bot.
- **Display & Audio Feedback**:
  - High-contrast OLED (SSD1306 0.96"/1.3") or circular LCD (GC9A01 1.28").
  - Animated mascot states (Idle, Happy/Receiving, Reward Unlocked, Level-Up).
  - Subtle passive buzzer sound (programmable chimes; disableable toggle).
- **Cumulative Progress & Reward UI**: Displays accumulated XP/points and notifies when a user-configured reward milestone is reached.
- **Low-Friction Configuration**: Configurable via JSON / web portal (points per receipt, mascot type, sleep timeout).

---

## 7. Non‑Functional Constraints
- **Bill of Materials (BOM)**: < \$10 AUD (~ \$6.10 AUD standard configuration).
- **Form Factor**: Desktop footprint ≤ 80 mm × 50 mm × 35 mm; 3D printable with PLA/PETG without complex supports.
- **Power Efficiency**: Deep/light sleep mode when inactive (> 30s), waking up instantly on network packet; idle power ≤ 30 mA.
- **Psychological Safety**: Strict prohibition of negative visual states (no crying, dying, or skull graphics; no "Points Deducted" banners).

---

## 8. Next Steps & Implementation Roadmap
1. **Firmware Prototype**: Develop minimal ESP32 firmware (MicroPython or Arduino C++) with MQTT subscriber and SSD1306 U8g2 animation engine.
2. **Bot Integration**: Add an MQTT publisher hook in `Neo-D LedgerCraft` triggered upon successful receipt confirmation.
3. **Enclosure Modeling**: Design a friendly 3D printable desk shell (CAD / STL).
