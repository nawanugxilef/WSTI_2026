# Neo-D LedgerCraft — Agent Skills
> What each agent does, in plain language.  
> These descriptions power the intro cards in the web interface.

---

## 🤖 Neo — Chief of Staff
**Role:** Orchestrator. Routes every message to the right agent and keeps the workflow moving.

**What Neo does:**
- Receives every message you send (photo, text, command)
- Decides which agent handles it — never guesses, always routes by rule
- Sends you the final reply after all agents finish
- Tracks your XP and triggers Womby's reactions
- Manages the work/personal question queue (asks once, never again)

**What Neo never does:**
- Reads receipt content (that's Corgi's job)
- Makes financial decisions (that's yours)
- Sends messages without you triggering something first

---

## 🐕 Corgi — Finance Agent
**Role:** Receipt intelligence. Reads every photo, PDF, or typed note you send.

**What Corgi does:**
- Reads the shop name, date, and total from your receipt photo using AI (Gemini Vision)
- Checks GST — if it's printed, records it exactly; if not, leaves it blank (never guesses)
- Suggests a category from a fixed list of 12 options
- If the shop is already in memory → uses your past decision, no AI needed
- If the category is unclear → marks it for one human question, then remembers your answer

**What Corgi never does:**
- Invents amounts it can't read
- Changes a category you already confirmed
- Accesses your bank account

---

## 🐻 Bear — QA Agent
**Role:** Quality checker. Reviews each receipt before it's recorded.

**What Bear does:**
- Checks for duplicate receipts (same vendor, same amount, same date)
- Flags receipts where the total looks unusual (e.g. very large amount, possible OCR error)
- Marks low-confidence receipts for your review
- Logs every flag with a reason so you can decide — Bear never deletes anything

**What Bear never does:**
- Rejects a receipt on its own
- Changes a category or amount
- Makes the final call — that's always you

---

## 🦒 Giraffe — Knowledge Manager
**Role:** Long-term memory. Remembers every vendor decision you've ever made.

**What Giraffe does:**
- Saves your work/personal and category decisions per vendor (e.g. "Bunnings = work, Tools & Equipment")
- Looks up the saved rule before Corgi calls AI — if the rule exists, AI is skipped entirely
- Maintains the journal: balances income vs expenses per business activity
- Assigns Australian ATO myTax labels automatically from a fixed lookup table
- Builds your FY summary: totals by category, claim type, and GST

**What Giraffe never does:**
- Guesses a vendor rule — only saves what you've confirmed
- Deletes past rules
- Calculates your tax deduction (that's your tax agent's job, not ours)

---

## 🦁 Felix — The Boss
**Role:** You. Final authority on every decision.

**What you decide:**
- Work or personal (asked once per shop, your answer saved forever)
- Whether a flagged receipt is correct or needs editing
- Which wishlist items to buy or skip
- When to export your summary

**What the agents handle for you:**
- Reading the receipt
- Suggesting the category
- Checking for errors
- Remembering your past decisions
- Building the tax summary

---

## 🐨 Womby — Your Companion
**Role:** The friendly face of Neo-D. Reacts to everything you do.

**What Womby does:**
- Celebrates every receipt you log (+XP, never judges timing)
- Goes `thinking` while Corgi reads your photo
- Goes `curious` when asking work/personal
- Goes `proud` when you make a wishlist decision
- Offers a break after 5 receipts in a row
- Welcomes you back after any absence — no guilt, no streaks

**Womby's one rule:**  
Womby notices what you *do*, never what you *don't*. No reminders, no "you haven't logged in X days."

---

## How They Work Together

```
You send a photo
  └─► Neo routes it to Corgi
        └─► Corgi reads the receipt (Gemini Vision)
              └─► Giraffe checks vendor memory
                    ├─ Known vendor → category from memory (no AI)
                    └─ Unknown vendor → Corgi picks category (AI)
                          └─► Bear checks for flags/duplicates
                                └─► Neo sends you the result
                                      └─► Womby reacts 🎉
                                            └─► Giraffe saves to journal
```

**AI is used in exactly one place:** reading the receipt photo.  
Everything else is rules, memory, and your decisions.
