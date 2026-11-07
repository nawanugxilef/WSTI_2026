---
name: adhd-neurodivergent-ux
description: "Comprehensive blueprint for designing and implementing ADHD-friendly, neurodivergent-optimized software, IoT hardware companions, and conversational workflows based on neuropsychology (Barkley's Externalized EF, Dodson's ICNU, RSD mitigation, and non-punitive gamification)."
---

# ADHD & Neurodivergent UX Design Guide

A definitive architectural, behavioral, and UI/UX standard for engineering digital tools, conversational agents, and physical IoT desk companions tailored for neurodivergent brains (ADHD, Executive Dysfunction, and Pathological Demand Avoidance / PDA).

---

## 1. Neuropsychological Foundations

To design effective systems for ADHD, conventional "neurotypical" productivity rules (discipline, streaks, rigid schedules, moralized willpower) must be actively rejected.

### A. The Interest-Based Nervous System (IBNS) — Dr. William Dodson
* **Importance vs. Interest:** Neurotypical brains prioritize tasks based on **Importance** and **Consequence** (e.g., *"I need to file my tax receipts because it's important"*). An ADHD nervous system cannot reliably trigger dopamine release from "importance."
* **The ICNU Triggers:** Dopamine gating opens and executive function activates ONLY when at least one of these 4 triggers is present:
  1. **I – Interest:** Fascinating, aesthetically pleasing, or fun.
  2. **C – Challenge:** A mini-game, puzzle, or quest to complete.
  3. **N – Novelty:** Fresh visual cues, unpredictable micro-rewards, or unique hardware.
  4. **U – Urgency:** Imminent deadline (must be used gently; chronic urgency causes burnout and paralysis).

### B. Point-of-Performance & Externalized Executive Function — Dr. Russell Barkley
* **Prosthetic Prefrontal Cortex:** Executive function is a biological deficit in working memory, sustained attention, and temporal anchoring. Systems cannot assume the user will "remember later."
* **Point of Performance Rule:** Scaffolding must exist in the **physical and temporal environment** where the action happens. If an action requires unlocking a phone, finding an app, and navigating menus, the working memory buffer flushes, resulting in task abandonment (*executive paralysis*).
* **Object Permanence ("Out of Sight, Out of Mind"):** If a digital tool is buried inside a phone menu or background tab, it ceases to exist for an ADHD user. Physical anchors (desk hardware, ambient lights) keep the habit in conscious awareness without cognitive strain.

### C. Rejection Sensitive Dysphoria (RSD) & The Shame Spiral
* Pervasive emotional vulnerability to perceived failure, rejection, or criticism.
* **The Guilt Trap:** When an app displays warnings, red badges, or dying virtual pets (*"You haven't logged in 3 days! Your pet is starving"*), the brain experiences acute shame.
* **Avoidance Response:** Instead of motivating, shame triggers self-protection: the user silences, deletes, or completely abandons the tool.

### D. Pathological Demand Avoidance (PDA) & Autonomy Protection
* Acute resistance to perceived loss of autonomy or being micromanaged.
* **The Hostage Trap:** Never withhold personal rewards behind mandatory chores (*"Log 10 receipts before you can eat a snack"*). The user feels infantilized, triggering rebellion and refusal.
* **The Loyal Ally Stance:** The system must act as an eager, non-judgmental executive assistant or faithful team member, never a nagging parent or authoritarian boss.

---

## 2. The "ADHD Tax" & Financial Behavioral Science

* **What is the ADHD Tax?** The direct financial loss resulting from executive dysfunction: lost receipts for tax/deductions (e.g., ATO claims in Australia), unpaid late fees, missed warranty deadlines, and impulse purchases caused by dopamine-seeking behavior.
* **Friction Asymmetry:** Buying something with digital tap-to-pay has **near-zero friction**. But manually logging receipts, categorizing expenses, and reconciling bank balances has **extremely high cognitive friction**.
* **Design Mandate:** Inverting the friction—making the recording of transactions as instantaneous, effortless, and dopaminergically rewarding as the purchase itself.

---

## 3. ATM Framework (Amati, Tiru, Modifikasi) — Global Benchmarks

Lessons derived from existing international apps, neobanks, and hardware tools:

| Reference App / System | Amati (Observation) | Tiru (Pattern to Adopt) | Modifikasi (Our ADHD Standard) |
|---|---|---|---|
| **Up Bank** *(Melbourne, Australia)* | Colorful, highly gamified neobank with emoji Savers and the *"Maybuy"* delay mechanism. Extremely popular among Australian neurodivergents. | **High-Dopamine Visual Feedback:** Bright, expressive, satisfying micro-animations and zero-judgment tone. | **Cross-Platform & Physical Anchor:** Bring the delight outside a single banking app onto a desk companion, capturing receipts from any payment channel (cash, multi-bank, invoices). |
| **PocketSmith / YNAB** | Comprehensive cash-flow forecasting and envelope budgeting. | **Forward-Looking Financial Visibility:** Visual balance forecasting to combat time-blindness. | **Zero-Spreadsheet Ingestion:** Eliminate dense categorization tables. Replace manual data entry with background AI agent extraction (1 photo = done). |
| **Goblin.tools** | Single-input box that breaks overwhelming tasks into micro-steps with tone-adjusting AI. | **Zero-Friction Intake:** Single-touch interaction without onboarding forms or mandatory category picks. | **Silent Multi-Agent Tidy:** Let background agents (Chief of Staff, Finance Agent, QA) process, clean, and verify data without prompting the user. |
| **Timeular & Time Timer** | Tangible physical gadgets (rotating 8-sided die, red disappearing disc) sitting visibly on desks. | **Physical Environmental Anchor:** Hardware on the desk that permanently bypasses screen-based "out-of-sight, out-of-mind." | **IoT Companion for Finance:** An ESP32 + OLED desktop companion that physically reacts when a receipt is logged via Telegram. |
| **MyBudget** *(Australia)* | Human caseworkers take full manual control of user finances for thousands of dollars per year. | **Offloading Administrative Dread:** Eliminating the chore of ledger-keeping. | **Affordable Autonomous AI Team:** AI agents act as the user's private executive staff for pennies, preserving the user's dignity, agency, and privacy. |
| **Finnagotchi / Virtual Pets** | Gamifying finance with a Tamagotchi-style pet. | **Emotional Connection & Companionship:** Users care for and enjoy visual progress. | **100% Non-Punitive:** Eliminate dying/sick pet mechanics completely. Pets only celebrate, sleep, or cheer—never guilt-trip. |

---

## 4. Core Anti-Patterns (NEVER Implement in ADHD Apps)

1. ❌ **Binary Streaks (Day 1..N):**
   * *Problem:* A single missed day resets the counter to 0, invalidating weeks of effort and triggering the "All-or-Nothing" quit response.
   * *Solution:* **Cumulative XP** (Count UP only, lifetime points never reset) + "Welcome Back!" bonus.
2. ❌ **Guilt-Tripping & Hostage Mechanics:**
   * *Problem:* Threatening that a virtual character will get sick, or locking physical privileges behind chore walls.
   * *Solution:* **Gift-Style Rewards & Celebration:** Unlocked perks are treats and badges of honor, not conditional survival rations.
3. ❌ **Nested Categorization & Form Fields:**
   * *Problem:* Asking the user to select from 15 dropdown categories freezes working memory.
   * *Solution:* Smart AI category defaults + Vendor Memory with single-tap emoji confirmations.
4. ❌ **Moralizing Spending (Good vs. Bad Money):**
   * *Problem:* Awarding points only for "saving" or penalizing "spending" causes users to avoid logging purchases when they splurge.
   * *Solution:* **Reward the Administrative Act of Logging:** Every receipt logged earns XP regardless of whether it was a salad or a luxury gadget.
5. ❌ **App-Only Isolation:**
   * *Problem:* When notifications are dismissed or the phone screen is off, the routine disappears.
   * *Solution:* **Multimodal Anchoring:** Pair software with physical desktop feedback (IoT LED, buzzer, OLED companion).

---

## 5. Non-Punitive Gamification Architecture

```
[ Receipt Photo Sent ] ──► [ AI OCR & Extraction ] ──► [ Silent Background QA ]
                                                               │
                                                               ▼
[ Physical Desk Companion ] ◄── [ MQTT / WebSocket ] ◄── [ Instant +10 XP Reward ]
  • Happy mascot dance                                         │
  • Soft pleasant chime                                        ▼
  • Progress bar advances ───────────────► [ Milestone: Indomie Reward Unlocked! ]
                                           (Autonomous, zero-guilt real-world treat)
```

* **Instant Micro-Dopamine Loop:** Delay between action and reward must be < 2 seconds. The desk companion immediately wakes up and plays an animation.
* **Zero-Loss Economy:** Points only accumulate. There are no penalty deductions.
* **Variable Reward Surprise Drops:** Occasional randomized animations or bonus badges to leverage Dodson's Novelty (N) trigger.
* **Self-Directed Milestone Box:** Users name their own rewards (e.g., *"2 Packs of Indomie Goreng"*, *"Play Nintendo Switch for 30 minutes"*). The app acts as the party horn when unlocked, not the gatekeeper.

---

## 6. Development & Review Checklist for Engineers & Agents

Before shipping any UI, workflow, prompt, or hardware companion feature for neurodivergent users, verify:

- [ ] **Action Friction ≤ 1 Step:** Can the user execute the core action (e.g., capture receipt) in a single physical motion without typing or navigating menus?
- [ ] **Point-of-Performance Scaffolding:** Is there an environmental or physical cue that exists in the user's line of sight?
- [ ] **Zero-Judgment Language:** Are all messages free from moralizing terms (*"overspending"*, *"overdue"*, *"do not repeat"*)?
- [ ] **RSD-Safe Tone:** If an error occurs, is the system framed as apologizing to the user (*"Oops, my camera vision was a bit blurry, could you snap it once more for me boss?"*) rather than blaming the user?
- [ ] **Cumulative Progress:** Is progress displayed as continuous lifetime growth rather than vulnerable streaks?
- [ ] **Cognitive Load Reduction:** Are numbers rounded or visually represented with progress bars and color cues rather than overwhelming tables?
- [ ] **Physical Sensory Feedback:** Does the interaction include a pleasant tactile, visual, or acoustic cue (haptic vibration, soft chime, OLED face)?
