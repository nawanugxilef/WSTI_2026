# Hardware BOM & Kit Card Specification – ADHD Desk Companion

---

## 1. Executive Summary & Design Goal
The **Neo-D Desk Companion** is an open-hardware, low-cost tactile device designed to externalize executive function for neurodivergent individuals in Australia. 
To remove all manufacturing, shipping, and assembly friction:
1. **Total Bill of Materials (BOM) Cost**: Under **\$7.50 AUD** (~ **\$6.10 AUD** standard configuration).
2. **Form Factor: 3D-Printable "Kit Card"**: All structural chassis components are printed in a single flat card with snap-away runner sprues (like a model kit).
3. **Tool-Free Assembly**: 100% snap-fit dovetails and friction pins. **No screws, no glue, no special tools required**.
4. **Interactive 3D Assembly Manual**: An interactive WebGL/CSS 3D step-by-step visualizer with haptic chimes to guide the user without cognitive overload.

---

## 2. Complete Bill of Materials (BOM — Strictly AUD)

### A. Core Electronics (Sydney / NSW Local Sourcing)

| Item | Component | Specs & Features | Qty | Est. Cost (AUD) | Sydney Sourcing Keyword |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **MCU (Option A - Recommended)** | **ESP32-C3 SuperMini** | RISC-V 160MHz, Wi-Fi 2.4GHz + BLE 5, Type-C port, ultra-compact (22.5 × 18 mm). | 1 | \$2.90 AUD | `ESP32-C3 SuperMini Type-C` |
| *MCU (Option B - Alternative)* | *ESP32-WROOM-32 DevKit* | Dual-core Tensilica, 30-pin DIP form factor (larger chassis required). | 1 | \$4.60 AUD | `ESP32 DevKit V1 30 Pin` |
| **Display (Option A - Recommended)** | **0.96" I2C OLED (SSD1306)** | 128×64 resolution, 4-pin I2C (`GND, VCC, SCL, SDA`), High contrast, Yellow-Blue or White. | 1 | \$2.10 AUD | `OLED 0.96 I2C SSD1306` |
| *Display (Option B - Large OLED)* | *1.3" I2C OLED (SH1106)* | 128×64 resolution, slightly larger viewing area, same 4-pin interface. | 1 | \$3.80 AUD | `OLED 1.3 inch I2C SH1106` |
| *Display (Option C - Round Color)* | *1.28" Round LCD (GC9A01)* | 240×240 IPS Color, 7-pin SPI (`CS, DC, RES, SDA, SCL, BLK, VCC, GND`). | 1 | \$5.90 AUD | `GC9A01 1.28 inch Round IPS` |
| **Audio Buzzer** | **Passive Piezo Buzzer 3.3V** | 9×5.5mm or 12×9.5mm, 2-pin passive (supports PWM frequency melody/chimes). | 1 | \$0.30 AUD | `Passive Buzzer 3.3V 9mm` |
| **Wiring** | **Dupont Female-Female Wires** | 10cm ribbon cables (4 pins for OLED, 2 pins for buzzer), can be soldered direct. | 6 wires | \$0.30 AUD | `Female to Female Jumper Leads 10cm` |
| **Power Supply** | **USB-C Cable (Host)** | Connects to desk PC, USB hub, or standard 5V phone charger (usually user-owned). | 1 | \$0.00 AUD | Existing desk accessory |

### B. 3D Printing Material & Print Stats

| Item | Specification | Value |
| :--- | :--- | :--- |
| **Material** | PLA / PLA+ (or PETG) | Matte Black, Pastel Yellow, or Forest Green |
| **Weight** | ~28 grams filament | Cost: ~\$0.50 AUD |
| **Print Bed Footprint** | 135 mm × 95 mm × 2.4 mm | Fits Ender-3, Prusa Mini, Bambu Lab A1 Mini |
| **Print Duration** | ~38 - 50 minutes | 0.2mm layer height, 20% infill, 2 perimeters |
| **No Supports Needed** | Flat planar orientation | 100% support-free print |

> **Total Estimated Build Cost (Standard Config):**  
> **\$6.10 AUD** — Well under the \$15.00 AUD budget ceiling!

---

## 3. Pinout & Wiring Diagram

### Pin Mapping (ESP32-C3 SuperMini + SSD1306 I2C + Passive Buzzer)

```
                 ESP32-C3 SuperMini
                  ┌───────────────┐
                  │ [ USB-C PORT] │
                  │               │
       (3.3V) ────┤ 3V3       GND ├──── (GND)
                  │ GPIO0   GPIO1 │
       (SDA)  ────┤ GPIO8   GPIO2 ├──── (Buzzer PWM +)
       (SCL)  ────┤ GPIO9   GPIO3 │
                  │ GPIO10  GPIO4 │
                  │ GPIO20  GPIO5 │
                  │ GPIO21  GPIO6 │
                  └───────────────┘
```

| Component | Component Pin | ESP32-C3 Pin | ESP32-WROOM-32 Pin | Function / Notes |
| :--- | :--- | :--- | :--- | :--- |
| **OLED SSD1306** | `VCC` | `3V3` | `3V3` | 3.3V Power rail |
| **OLED SSD1306** | `GND` | `GND` | `GND` | Common Ground |
| **OLED SSD1306** | `SCL` | `GPIO 9` | `GPIO 22` | I2C Clock Line |
| **OLED SSD1306** | `SDA` | `GPIO 8` | `GPIO 21` | I2C Data Line |
| **Passive Buzzer**| `Positive (+)` | `GPIO 2` | `GPIO 25` | PWM Chime Tone Output |
| **Passive Buzzer**| `Negative (-)` | `GND` | `GND` | Ground Return |

---

## 4. The "Kit Card" Concept & Mechanics

### Why a Kit Card for ADHD Users?
1. **The Joy of Tactile Unboxing (Model Kit Nostalgia)**: Satisfying *snip* and *pop* activation removes chore dread and provides an instant tactile dopamine hit.
2. **Zero Missing Parts**: Every housing part is cast inside a single rectangular perimeter card with thin breakaway runner sprues (0.6mm thickness with V-notches).
3. **No Hardware Tool Friction**: No hunting for M2/M3 screws or allen wrenches. Everything locks together via precision **friction-fit tabs** and **slide-in dovetails**.

### The 5 Modular Parts on the Runner Card:
```
┌─────────────────────────────────────────────────────────────────┐
│  [NEO-D COMPANION KIT CARD]            v1.0  (135 x 95 x 2.4mm) │
│ ┌─────────────────────────┐   ┌───────────────────────────────┐ │
│ │                         │   │                               │ │
│ │     PART 1: FRONT       │   │        PART 2: BACK           │ │
│ │     BEZEL & SCREEN      │   │        HOUSING TRAY           │ │
│ │     WINDOW (w/ Ears)    │   │        (ESP32 & Buzzer bay)   │ │
│ │                         │   │                               │ │
│ └────────────┬────────────┘   └───────────────┬───────────────┘ │
│         [Sprue Gate]                    [Sprue Gate]            │
│ ┌────────────┴────────────┐   ┌───────────────┴───────────────┐ │
│ │  PART 3: LEFT DESK LEG  │   │  PART 4: RIGHT DESK LEG       │ │
│ │  (60° Angled Easel)     │   │  (60° Angled Easel)           │ │
│ └─────────────────────────┘   └───────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │  PART 5: 4x PIN TOGGLE WEDGES / CABLE CLIPS (Spares)        │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

1. **Part 1: Front Bezel**: Framing with 4 corner snap-recesses for the 0.96" OLED glass, styled with friendly rounded corners and optional decorative animal ears.
2. **Part 2: Back Housing**: Enclosed cradle with friction guide rails to grip the ESP32-C3 PCB snugly, a circular 9.5mm cavity for the passive buzzer, and side routing paths for wires.
3. **Part 3 & 4: Left & Right Kickstand Legs**: Slide into dovetail receiver slots on the back plate, canting the screen upwards at a **60° ergonomic viewing angle** directly in the desk user's eyeline.
4. **Part 5: Cable strain relief clip**: Snaps over the USB-C cable to prevent cord pulls from upsetting the companion.

---

## 5. Software & Display States (Micro-Animations)

The ESP32 firmware renders 4 distinct animated emotional states to reinforce positive receipt tracking without moralizing:

```
[ 1. IDLE / SLEEP ]      [ 2. RECEIPT DETECTED ]   [ 3. REWARD UNLOCKED ]    [ 4. LEVEL-UP ]
      ( - . - )                  ( ^ ▽ ^ )                 ( ★ ▽ ★ )            ( ✧ ‿ ✧ )
   Gentle Zzz pulsing       Cheering jump + Chime      Trophy / Indomie icon    Fireworks / Confetti
```

1. **Idle State (`idle.anim`)**: Mascot blinks gently every 4–6 seconds. Ambient, unobtrusive, calm.
2. **Happy / Ingestion State (`happy.anim`)**: Triggered when Telegram bot sends `{ "status": "ok", "xp": 10 }`. Mascot bounces with heart eyes; buzzer plays a pleasant 2-tone ascending chime (C6 → G6).
3. **Milestone / Treat Unlocked (`box_open.anim`)**: Triggered when cumulative XP reaches user threshold (e.g., 100 XP). Display flashes a playful box opening with user's reward text (*"INDOMIE UNLOCKED!"*); buzzer plays a 4-tone victory fanfare.
4. **Level-Up (`levelup.anim`)**: Lifetime achievement celebration. Zero pressure, no resets.

---

## 6. Interactive 3D Assembly Manual Guide
To make building frictionless, an interactive 3D manual is provided:
- **Location**: Viewable as an interactive Generative UI artifact (`assembly_guide_3d.html`).
- **Features**: 
  - 360° orbital rotation with mouse/touch drag.
  - Step-by-step assembly timeline (Steps 1 to 5).
  - One-click "Explode / Assemble" toggle.
  - Part isolation highlighting with wiring callouts.
  - Interactive audio chimes demonstrating the buzzer tones.
