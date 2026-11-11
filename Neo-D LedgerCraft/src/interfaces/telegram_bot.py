"""
Telegram Bot Interface for Neo-D LedgerCraft
Dedicated to zero-friction ADHD receipt & expense intake and 1-tap owner corrections:
- Photos & PDF invoices (with honest Google Drive upload status and safe offline OCR handling)
- Bank statement uploads (CSV/PDF/Image with AI auto-sync & deduplication)
- Casual typed notes (e.g. "Fictional Demo Cafe coffee 6.50 today")
- Conditional 1-Tap Inline Keyboard & /correct command for Category, Business %,
  Stream (ABN/PAYG/Personal), Activity (Dog Walking / 3D Printing), Vendor, and explicit GST
- Full double-entry journal & ATO myTax recalculation on every correction without duplicate entries
- Segregates Needs-Review items from Confirmed myTax claim totals
Restricted to owner only. Strictly English and AUD ($ AUD).
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shlex
import sys
from typing import Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.agents.chief_of_staff import ChiefOfStaff
from src.agents.finance_agent import FinanceAgent
from src.interfaces.cli import load_config, seed_accounts
from src.storage.categories import (
    ATO_SUGGESTION_DISCLAIMER,
    VALID_ACTIVITIES,
    VALID_STREAMS,
    format_activity_badge,
    format_stream_badge,
    list_selectable_categories,
    require_finite_float,
)
from src.storage.db import get_connection, init_db
from src.storage.gdrive import GDriveAdapter
from src.storage.journal import JournalManager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def build_correction_keyboard(
    receipt_id: int,
    claim_stream: str = "abn",
    show_all_categories: bool = False,
) -> InlineKeyboardMarkup:
    """
    Builds a conditional 1-tap owner correction inline keyboard for a recorded receipt:
    - Personal entries show Stream switcher and Personal category controls ONLY (no Business % or ABN activity buttons).
    - PAYG entries show Stream switcher, Work %, and PAYG category controls (no ABN activity buttons).
    - ABN entries show Stream switcher, ABN Activity (Dog Walking / 3D Printing), Business %, and ABN category controls.
    """
    stream = (claim_stream or "abn").strip().lower()

    if show_all_categories:
        cats = list_selectable_categories()
        rows: List[List[InlineKeyboardButton]] = []
        row: List[InlineKeyboardButton] = []
        for spec in cats:
            prefix = "💼" if spec.stream == "abn" else ("👔" if spec.stream == "payg" else "🏠")
            label = f"{prefix} {spec.key}"
            row.append(
                InlineKeyboardButton(label[:28], callback_data=f"corr:{receipt_id}:cat:{spec.key}")
            )
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ Back to Controls", callback_data=f"corr:{receipt_id}:menu:main")])
        return InlineKeyboardMarkup(rows)

    stream_row = [
        InlineKeyboardButton("💼 ABN ✓" if stream == "abn" else "💼 ABN", callback_data=f"corr:{receipt_id}:stream:abn"),
        InlineKeyboardButton("👔 PAYG ✓" if stream == "payg" else "👔 PAYG", callback_data=f"corr:{receipt_id}:stream:payg"),
        InlineKeyboardButton("🏠 Personal ✓" if stream == "personal" else "🏠 Personal", callback_data=f"corr:{receipt_id}:stream:personal"),
    ]

    # Personal stream: hide Business % and ABN activity buttons
    if stream == "personal":
        keyboard = [
            stream_row,
            [
                InlineKeyboardButton("☕ Dining/Coffee", callback_data=f"corr:{receipt_id}:cat:dining_coffee"),
                InlineKeyboardButton("🛒 Groceries", callback_data=f"corr:{receipt_id}:cat:groceries"),
                InlineKeyboardButton("🏥 Medical", callback_data=f"corr:{receipt_id}:cat:health_medical"),
            ],
            [
                InlineKeyboardButton("🔌 Utilities", callback_data=f"corr:{receipt_id}:cat:utilities"),
                InlineKeyboardButton("🎬 Entertainment", callback_data=f"corr:{receipt_id}:cat:entertainment"),
                InlineKeyboardButton("📂 All Categories...", callback_data=f"corr:{receipt_id}:menu:cats"),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    pct_row = [
        InlineKeyboardButton("100%", callback_data=f"corr:{receipt_id}:pct:100"),
        InlineKeyboardButton("80%", callback_data=f"corr:{receipt_id}:pct:80"),
        InlineKeyboardButton("50%", callback_data=f"corr:{receipt_id}:pct:50"),
        InlineKeyboardButton("30%", callback_data=f"corr:{receipt_id}:pct:30"),
        InlineKeyboardButton("0%", callback_data=f"corr:{receipt_id}:pct:0"),
    ]

    # PAYG stream: show Stream, Work %, and PAYG categories (hide ABN activity buttons)
    if stream == "payg":
        keyboard = [
            stream_row,
            pct_row,
            [
                InlineKeyboardButton("🚗 D1 Car/Travel", callback_data=f"corr:{receipt_id}:cat:work_car_travel"),
                InlineKeyboardButton("👕 D3 Clothing", callback_data=f"corr:{receipt_id}:cat:work_uniform_clothing"),
            ],
            [
                InlineKeyboardButton("🎓 D4 Education", callback_data=f"corr:{receipt_id}:cat:self_education"),
                InlineKeyboardButton("💻 D5 Home/Other", callback_data=f"corr:{receipt_id}:cat:work_home_office_other"),
                InlineKeyboardButton("📂 All Categories...", callback_data=f"corr:{receipt_id}:menu:cats"),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    # ABN stream: show Stream, ABN Activity, Business %, and ABN categories
    keyboard = [
        stream_row,
        [
            InlineKeyboardButton("🐕 Dog Walking", callback_data=f"corr:{receipt_id}:act:dog_walking"),
            InlineKeyboardButton("🖨️ 3D Printing", callback_data=f"corr:{receipt_id}:act:printing_3d"),
            InlineKeyboardButton("➖ No Activity", callback_data=f"corr:{receipt_id}:act:none"),
        ],
        pct_row,
        [
            InlineKeyboardButton("🖨️ 3D Materials", callback_data=f"corr:{receipt_id}:cat:materials_3d_printing"),
            InlineKeyboardButton("🐕 Dog Supplies", callback_data=f"corr:{receipt_id}:cat:dog_walking_supplies"),
            InlineKeyboardButton("🚗 Car/Fuel", callback_data=f"corr:{receipt_id}:cat:car_fuel_maintenance"),
        ],
        [
            InlineKeyboardButton("💻 Software", callback_data=f"corr:{receipt_id}:cat:software_subscriptions"),
            InlineKeyboardButton("☕ Dining/Coffee", callback_data=f"corr:{receipt_id}:cat:dining_coffee"),
            InlineKeyboardButton("📂 All Categories...", callback_data=f"corr:{receipt_id}:menu:cats"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def parse_correction_tokens(tokens: List[str]) -> Dict[str, object]:
    """
    Parses `/correct <id> ...` arguments into structured correction kwargs.
    Supports both explicit `key=value` pairs (`category=dog_walking_supplies`, `pct=50`,
    `stream=payg`, `activity=dog_walking`, `vendor="Fictional Vendor"`, `gst=0`) and shorthand tokens.
    """
    if not tokens:
        raise ValueError("Usage: /correct <receipt_id> [category=...] [pct=0..100] [stream=abn|payg|personal] [activity=dog_walking|printing_3d|none] [vendor=...] [gst=...]")

    first = tokens[0].lstrip("#")
    if not first.isdigit():
        raise ValueError(f"First argument must be a numeric receipt ID (got '{tokens[0]}').")

    result: Dict[str, object] = {"receipt_id": int(first)}
    known_cats = {c.key for c in list_selectable_categories()}

    for tok in tokens[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            k_low = k.strip().lower()
            v_clean = v.strip().strip("'\"")
            if k_low in ("cat", "category"):
                c_low = v_clean.lower()
                if c_low not in known_cats:
                    raise ValueError(f"Unsupported category '{v_clean}'.")
                result["category"] = c_low
            elif k_low in ("pct", "business_pct", "percent", "share"):
                pct_val = require_finite_float(v_clean.rstrip("%"), "business_pct")
                if pct_val < 0.0 or pct_val > 100.0:
                    raise ValueError(f"Business percentage must be between 0 and 100 (got {pct_val}).")
                result["business_pct"] = pct_val
            elif k_low in ("stream", "claim", "claim_stream", "purpose"):
                v_low = v_clean.lower()
                s_val = "abn" if v_low == "business" else v_low
                if s_val not in VALID_STREAMS:
                    raise ValueError(f"Unsupported claim_stream '{v_clean}'. Expected one of {VALID_STREAMS}.")
                result["claim_stream"] = s_val
            elif k_low in ("act", "activity", "business_activity", "business"):
                v_low = v_clean.lower()
                if v_low in ("dogwalking", "dog_walking", "dog"):
                    result["business_activity"] = "dog_walking"
                elif v_low in ("printing3d", "printing_3d", "3d", "3d_printing"):
                    result["business_activity"] = "printing_3d"
                elif v_low in ("payg", "payg_work"):
                    result["business_activity"] = "payg_work"
                elif v_low == "none":
                    result["business_activity"] = "none"
                else:
                    raise ValueError(f"Unsupported business_activity '{v_clean}'.")
            elif k_low == "vendor":
                if not v_clean:
                    raise ValueError("Vendor name cannot be empty.")
                result["vendor"] = v_clean
            elif k_low == "tax_label":
                result["tax_label"] = v_clean
            elif k_low in ("gst", "tax", "tax_amount"):
                gst_val = require_finite_float(v_clean.lstrip("$"), "tax_amount")
                if gst_val < 0.0:
                    raise ValueError(f"GST amount (${gst_val:.2f}) cannot be negative.")
                result["tax_amount"] = gst_val
            elif k_low in ("payment", "payment_method"):
                result["payment_method"] = v_clean
            elif k_low == "date":
                result["date"] = v_clean
            else:
                raise ValueError(f"Unsupported correction field '{k}'.")
            continue

        t_low = tok.strip().lower()
        if t_low.endswith("%"):
            pct_val = require_finite_float(t_low[:-1], "business_pct")
            if pct_val < 0.0 or pct_val > 100.0:
                raise ValueError(f"Business percentage must be between 0 and 100 (got {pct_val}).")
            result["business_pct"] = pct_val
        elif t_low in VALID_STREAMS:
            result["claim_stream"] = t_low
        elif t_low == "business":
            result["claim_stream"] = "abn"
        elif t_low in ("dog_walking", "dogwalking"):
            result["business_activity"] = "dog_walking"
        elif t_low in ("printing_3d", "printing3d", "3d_printing"):
            result["business_activity"] = "printing_3d"
        elif t_low in VALID_ACTIVITIES:
            result["business_activity"] = t_low
        elif t_low in known_cats:
            result["category"] = t_low
        else:
            raise ValueError(f"Unsupported category or correction token '{tok}'.")

    return result


class TelegramReceiptBot:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        owner_chat_id: Optional[int] = None,
        db_path: Optional[str] = None,
        finance_agent: Optional[FinanceAgent] = None,
        load_dotenv: bool = True,
    ):
        self.cfg = load_config(load_dotenv=load_dotenv)
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        owner_env = os.environ.get("TELEGRAM_OWNER_CHAT_ID")
        if owner_chat_id is not None:
            self.owner_chat_id = owner_chat_id
        elif owner_env:
            try:
                self.owner_chat_id = int(owner_env)
            except ValueError:
                print("ERROR: TELEGRAM_OWNER_CHAT_ID must be a numeric ID.")
                sys.exit(1)
        else:
            self.owner_chat_id = None

        resolved_db_path = db_path or self.cfg.get("storage", {}).get("db_path", "./data/ledger.db")
        self.db_path = resolved_db_path
        self.conn = get_connection(resolved_db_path)
        init_db(self.conn)
        seed_accounts(self.conn)

        gdrive_cfg = self.cfg.get("google_drive", {})
        self.gdrive = GDriveAdapter(
            receipts_folder_id=gdrive_cfg.get("receipts_folder_id"),
            backups_folder_id=gdrive_cfg.get("backups_folder_id"),
            statements_folder_id=gdrive_cfg.get("statements_folder_id"),
        )

        self.cos = ChiefOfStaff(main_conn=self.conn, gdrive=self.gdrive, finance_agent=finance_agent)
        self.jm = JournalManager(self.conn)

    def _is_authorized(self, update: Update) -> bool:
        if not self.owner_chat_id:
            # Fail closed: reject all messages if owner chat ID is not configured
            return False
        chat = update.effective_chat
        return bool(chat and chat.id == self.owner_chat_id)

    def _get_companion_state(self) -> dict:
        cur = self.conn.execute(
            "SELECT total_xp, reward_name, reward_target_xp, boxes_unlocked, last_anim FROM companion_state WHERE id = 1"
        )
        row = cur.fetchone()
        if not row:
            return {
                "total_xp": 0,
                "reward_name": "2 Packs of Indomie Goreng 🍜",
                "reward_target_xp": 100,
                "boxes_unlocked": 0,
                "last_anim": "idle",
            }
        return dict(row)

    def _award_xp_and_emit(self, xp_gain: int = 10, event_label: str = "Receipt Logged") -> str:
        """Awards non-punitive cumulative XP, updates companion state, and returns a celebratory status block."""
        state = self._get_companion_state()
        old_xp = state["total_xp"]
        new_xp = old_xp + xp_gain
        target = state["reward_target_xp"]
        reward_name = state["reward_name"]

        old_boxes = old_xp // target
        new_boxes = new_xp // target
        unlocked_now = new_boxes > old_boxes
        anim = "reward_unlocked" if unlocked_now else "happy"

        with self.conn:
            self.conn.execute(
                """
                UPDATE companion_state
                SET total_xp = ?, boxes_unlocked = ?, last_anim = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (new_xp, new_boxes, anim),
            )

        box_progress = new_xp % target
        if unlocked_now and box_progress == 0:
            box_progress = target
        filled = int((box_progress / target) * 10)
        bar = "█" * filled + "░" * (10 - filled)

        event_payload = {
            "timestamp": datetime.datetime.now().isoformat(),
            "event": event_label,
            "xp_gained": xp_gain,
            "total_xp": new_xp,
            "box_progress": box_progress,
            "reward_target_xp": target,
            "reward_name": reward_name,
            "unlocked_now": unlocked_now,
            "boxes_unlocked": new_boxes,
            "anim": anim,
            "face": "( ★ ▽ ★ )" if unlocked_now else "( ^ ▽ ^ )",
            "chime": "victory_fanfare" if unlocked_now else "two_tone_chime",
        }
        try:
            event_dir = os.path.dirname(os.path.abspath(self.db_path)) or "./data"
            os.makedirs(event_dir, exist_ok=True)
            with open(os.path.join(event_dir, "companion_event.json"), "w") as f:
                json.dump(event_payload, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not write companion_event.json: {e}")

        lines = [
            f"\n✨ *+{xp_gain} XP Earned!* (Lifetime XP: `{new_xp} XP` — Never Resets)",
            f"🎁 *Reward Box* [{reward_name}]:",
            f"`[{bar}] {box_progress}/{target} XP`",
        ]
        if unlocked_now:
            lines.append(
                f"\n🎉 *REWARD BOX UNLOCKED!* 🎉\n"
                f"Treat unlocked: *{reward_name}* (Unconditional gift, zero guilt!)\n"
                f"📟 *Desk Companion*: `( ★ ▽ ★ )` Victory Fanfare & Confetti Triggered!"
            )
        else:
            lines.append(
                f"📟 *Desk Companion*: `( ^ ▽ ^ )` 2-Tone Chime & Happy Dance Triggered!"
            )
        return "\n".join(lines)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("⛔ Unauthorized user.")
            return

        chat_id = update.effective_chat.id
        state = self._get_companion_state()
        msg = (
            f"🦁 *Welcome back, Boss!* Your *Neo-D LedgerCraft* AI Executive Crew & Desk Companion are online.\n"
            f"Chat ID: `{chat_id}`\n\n"
            f"🤖 *Your 4 Deployed AI Agents:*\n"
            f"• 🐕 *Chief of Staff*: Zero-friction intake, file archiving & routing\n"
            f"• 🦊 *Finance AI*: Single-call multimodal OCR, Stream (`ABN`/`PAYG`/`Personal`), Activity (`Dog Walking`/`3D Printing`), Business %, & suggested ATO myTax labels\n"
            f"• 🐻‍❄️ *QA Reviewer*: Deterministic math, GST & duplicate audit (max 2 retries)\n"
            f"• 🦒 *Knowledge Manager*: Double-entry ledger & adaptive Vendor Memory\n\n"
            f"⚡ *How It Works (No Approve Buttons — Record & Correct):*\n"
            f"• 📸 *Snap a receipt photo/PDF* or 💬 *type a quick note* (e.g. `Fictional Demo Cafe coffee 6.50` or `Fictional 3D Maker Supply nozzle kit 32.99`)\n"
            f"• 🎛️ *Conditional 1-Tap Inline Buttons*: Adjust *Stream* (`ABN`/`PAYG`/`Personal`), *Activity* (`Dog Walking`/`3D Printing`), *Business %* (`100%`/`80%`/`50%`/`30%`/`0%`), or *Category* — books & tax totals recalculate instantly and Vendor Memory remembers your confirmed fix!\n"
            f"• `/correct <id> ...` — Command-based correction (e.g. `/correct 1 pct=50 stream=abn activity=dog_walking gst=0`)\n"
            f"• `/recent` — List recent receipts and allocations\n"
            f"• `/categories` — View all ABN, PAYG, and Personal categories\n"
            f"• `/balance` — View Double-Entry Trial Balance\n"
            f"• `/mytax` (or `/total`) — View Confirmed vs Needs-Review ATO myTax Summary\n"
            f"• `/pnl` (or `/report`) — View Dog Walking vs 3D Printing Profit & Loss\n"
            f"• `/spending [month]` — Monthly spending breakdown (e.g. `/spending`, `/spending last`)\n"
            f"• `/reward` — View or customize your Reward Box treat (`{state['reward_name']}`)\n"
            f"• `/companion` — Ping your ESP32 Desk Companion `( ^ ▽ ^ )`\n"
            f"• `/backup` — Snapshot database to Google Drive"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def recent_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        cur = self.conn.execute(
            """
            SELECT id, date, vendor, total_amount, category, claim_stream, business_activity,
                   business_pct, claimable_amount, tax_label, status, classification_source
            FROM receipts
            ORDER BY id DESC
            LIMIT 10
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            await update.message.reply_text("No receipts recorded yet.")
            return

        lines = ["🧾 *RECENT RECEIPTS (Newest First)*:\n"]
        for r in rows:
            stream_badge = format_stream_badge(r.get("claim_stream") or "abn")
            act_badge = format_activity_badge(r.get("business_activity") or "none")
            status_tag = "⚠️ Needs Review" if r.get("status") in ("flagged", "needs_review") else "✅ Confirmed"
            lines.append(
                f"• *#{r['id']}* ({r['date']}) *{r['vendor']}* — `${float(r['total_amount']):.2f} AUD` [{status_tag}]\n"
                f"  {stream_badge} | {act_badge} | `{r['category']}` | `{float(r.get('business_pct') or 0):.0f}%` "
                f"(Claimable: `${float(r.get('claimable_amount') or 0):.2f} AUD`)"
            )
        lines.append("\n_Tip: Use `/correct <id> pct=50 category=...` or tap the inline buttons on any receipt card._")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def categories_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        cats = list_selectable_categories()
        lines = [
            "📂 *CATEGORIES & SUGGESTED ATO myTAX LABELS (AUD)*",
            f"_{ATO_SUGGESTION_DISCLAIMER}_\n",
        ]
        for stream_key, title in (
            ("abn", "💼 *ABN Sole Trader Categories*"),
            ("payg", "👔 *PAYG Employee Categories*"),
            ("personal", "🏠 *Personal / Non-Deductible Categories*"),
        ):
            lines.append(title)
            for c in [spec for spec in cats if spec.stream == stream_key]:
                lbl = f" → _{c.tax_label}_" if c.tax_label else ""
                lines.append(f"• `{c.key}` ({c.default_business_pct:.0f}% default){lbl}")
            lines.append("")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def correct_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        raw_text = (update.message.text or "").strip()
        try:
            parts = shlex.split(raw_text)[1:]
        except ValueError:
            parts = (context.args or [])

        try:
            parsed_args = parse_correction_tokens(parts)
            rec_id = int(parsed_args.pop("receipt_id"))
            summary = self.cos.correct_receipt(receipt_id=rec_id, **parsed_args)
            kb = build_correction_keyboard(rec_id, claim_stream=summary.claim_stream)
            await update.message.reply_text(
                f"{summary.summary_text}\n\n✅ _Journal, GST, and ATO myTax totals recalculated without duplicate entries. Confirmed rule saved to Vendor Memory._",
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Could not apply correction: `{e}`\n"
                f"Usage: `/correct <id> [category=...] [pct=0..100] [stream=abn|payg|personal] [activity=dog_walking|printing_3d|none] [vendor=...] [gst=...]`",
                parse_mode="Markdown",
            )

    async def handle_correction_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query:
            return
        if not self._is_authorized(update):
            await query.answer("Unauthorized", show_alert=True)
            return

        data = query.data or ""
        parts = data.split(":", 3)
        if len(parts) < 4 or parts[0] != "corr":
            await query.answer("Unknown action")
            return

        _, rec_id_str, action, val = parts
        if not rec_id_str.isdigit():
            await query.answer("Invalid receipt ID")
            return
        receipt_id = int(rec_id_str)

        if action == "menu":
            show_all = val == "cats"
            rec_row = self.cos.km.get_receipt(receipt_id) or {}
            cur_stream = str(rec_row.get("claim_stream") or "abn")
            await query.answer("Select a category" if show_all else "Main controls")
            await query.edit_message_reply_markup(
                reply_markup=build_correction_keyboard(
                    receipt_id,
                    claim_stream=cur_stream,
                    show_all_categories=show_all,
                )
            )
            return

        try:
            kwargs: Dict[str, object] = {}
            if action == "stream":
                kwargs["claim_stream"] = val
            elif action == "act":
                kwargs["business_activity"] = val
            elif action == "pct":
                kwargs["business_pct"] = float(val)
            elif action == "cat":
                kwargs["category"] = val
            else:
                await query.answer("Unsupported field")
                return

            summary = self.cos.correct_receipt(receipt_id=receipt_id, **kwargs)
            await query.answer(f"Updated #{receipt_id} ({action}={val})")
            await query.edit_message_text(
                text=(
                    f"{summary.summary_text}\n\n"
                    f"✅ _Recalculated books, GST & myTax totals (0 duplicate entries). Confirmed rule remembered for {summary.vendor}._"
                ),
                parse_mode="Markdown",
                reply_markup=build_correction_keyboard(
                    receipt_id,
                    claim_stream=summary.claim_stream,
                    show_all_categories=False,
                ),
            )
        except Exception as e:
            logger.error(f"Callback correction failed for #{receipt_id}: {e}")
            await query.answer(f"Error: {e}", show_alert=True)

    async def reward_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        args_list = context.args if context.args else []
        if args_list:
            new_target = None
            if args_list[0].isdigit() and len(args_list) > 1:
                new_target = max(10, int(args_list[0]))
                new_name = " ".join(args_list[1:]).strip()
            else:
                new_name = " ".join(args_list).strip()

            with self.conn:
                if new_target is not None:
                    self.conn.execute(
                        "UPDATE companion_state SET reward_name = ?, reward_target_xp = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                        (new_name, new_target),
                    )
                else:
                    self.conn.execute(
                        "UPDATE companion_state SET reward_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                        (new_name,),
                    )
            state = self._get_companion_state()
            await update.message.reply_text(
                f"🎁 *Reward Box Updated!*\n"
                f"Your next milestone treat is now set to: *{state['reward_name']}*\n"
                f"Target per box: `{state['reward_target_xp']} XP` (Lifetime XP: `{state['total_xp']} XP`)",
                parse_mode="Markdown",
            )
            return

        state = self._get_companion_state()
        target = state["reward_target_xp"]
        box_progress = state["total_xp"] % target
        filled = int((box_progress / target) * 10)
        bar = "█" * filled + "░" * (10 - filled)

        msg = (
            f"🎁 *AUTONOMOUS REWARD BOX (100% Non-Punitive)*\n\n"
            f"• *Current Treat*: {state['reward_name']}\n"
            f"• *Progress*: `[{bar}] {box_progress}/{target} XP`\n"
            f"• *Lifetime XP*: `{state['total_xp']} XP` (Never resets!)\n"
            f"• *Total Treats Unlocked*: `{state['boxes_unlocked']}`\n\n"
            f"💡 *Tip*: Change your treat anytime with `/reward <your treat>`\n"
            f"Example: `/reward 2 Packs of Indomie Goreng 🍜`"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def companion_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        state = self._get_companion_state()
        msg = (
            f"📟 *ESP32-C3 DESK COMPANION STATUS*\n\n"
            f"• *OLED Display*: `( ^ ▽ ^ )` HAPPY // DESK ALLY\n"
            f"• *Buzzer Audio*: 🔔 2-Tone PWM Chime (C6 → G6) Ready\n"
            f"• *Lifetime XP*: `{state['total_xp']} XP`\n"
            f"• *Active Reward*: {state['reward_name']}\n"
            f"• *Local Event Topic*: `ledger/companion` (`./data/companion_event.json`)\n\n"
            f"✅ Test ping dispatched! Your companion just winked and played a happy chime."
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    @staticmethod
    async def _send_intake_result(send, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
        try:
            await send(text, parse_mode="Markdown", reply_markup=reply_markup)
        except BadRequest as error:
            if "can't parse entities" not in str(error).lower():
                raise
            await send(text, parse_mode=None, reply_markup=reply_markup)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        text = update.message.text.strip()
        if not text or text.startswith("/"):
            return

        await update.message.reply_chat_action("typing")
        try:
            summary = self.cos.process_note(text)
            response = summary.summary_text
            kb = None
            if summary.receipt_id:
                xp_block = self._award_xp_and_emit(xp_gain=10, event_label=f"Note: {summary.vendor}")
                kb = build_correction_keyboard(summary.receipt_id, claim_stream=summary.claim_stream)
                response = f"{response}\n{xp_block}"
        except Exception as e:
            logger.error("Error processing note: %s", e)
            await update.message.reply_text(
                "🐕 I had trouble reading that note. Could you send it again with the vendor, date and amount in AUD?",
            )
            return

        await self._send_intake_result(update.message.reply_text, response, reply_markup=kb)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        await self._handle_media(update.message, context, update.message.photo[-1])

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        doc = update.message.document
        # Telegram document names are user-controlled. Retain only the basename.
        filename = os.path.basename((doc.file_name or "").replace("\\", "/"))
        if not filename.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".csv")):
            await update.message.reply_text("Please send a Receipt/Invoice (PDF/JPG/PNG) or Bank Statement (CSV/PDF).")
            return

        await self._handle_media(update.message, context, doc, document_name=filename)

    async def _handle_media(self, message, context, media, *, document_name: Optional[str] = None):
        caption = message.caption
        caption_lower = (caption or "").lower()
        filename_lower = (document_name or "").lower()
        batch = filename_lower.endswith(".csv") or any(
            keyword in filename_lower or keyword in caption_lower
            for keyword in ("statement", "bank", "sync", "batch", "multi")
        )
        receipt_label = "Photo Receipt" if document_name is None else "Document"
        filename = f"{media.file_unique_id}_{document_name}" if document_name else f"{media.file_unique_id}.jpg"

        progress = await message.reply_text("📥 Got it. I’m reading your file now…")
        kb = None

        try:
            file = await context.bot.get_file(media.file_id)
            today = datetime.date.today()
            receipt_dir = os.path.join(
                os.path.dirname(os.path.abspath(self.db_path)), "receipts", str(today.year), f"{today.month:02d}"
            )
            os.makedirs(receipt_dir, exist_ok=True)
            local_path = os.path.join(receipt_dir, filename)
            await file.download_to_drive(local_path)

            if batch:
                batch_res = self.cos.process_batch_or_statement(local_path, user_note=caption)
                response = batch_res["summary_text"]
                count = batch_res["recorded_count"]
                if count > 0:
                    event_label = (
                        f"Batch/Statement ({count} synced)"
                        if document_name is None else f"Bank Statement Sync ({count} added)"
                    )
                    xp_block = self._award_xp_and_emit(
                        xp_gain=batch_res["xp_earned"],
                        event_label=event_label,
                    )
                    response = f"{response}\n{xp_block}"
            else:
                summary = self.cos.process_file(local_path, user_note=caption)
                response = summary.summary_text
                if summary.receipt_id:
                    xp_block = self._award_xp_and_emit(xp_gain=10, event_label=f"{receipt_label}: {summary.vendor}")
                    kb = build_correction_keyboard(summary.receipt_id, claim_stream=summary.claim_stream)
                    response = f"{response}\n{xp_block}"
        except Exception as e:
            logger.error("Error processing Telegram receipt: %s", e)
            response = "🦊 I had trouble reading that file. Could you try sending it again?"

        await self._send_intake_result(progress.edit_text, response, reply_markup=kb)

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        rows = self.jm.get_trial_balance()
        lines = ["⚖️ *DOUBLE-ENTRY TRIAL BALANCE (AUD)*:\n"]
        tot_deb = 0.0
        tot_crd = 0.0
        for r in rows:
            if r["total_debit"] > 0 or r["total_credit"] > 0:
                lines.append(f"`{r['code']}` {r['name']}: Deb ${r['total_debit']:.2f} | Crd ${r['total_credit']:.2f}")
                tot_deb += r["total_debit"]
                tot_crd += r["total_credit"]
        lines.append(f"\n*TOTAL*: Deb ${tot_deb:.2f} | Crd ${tot_crd:.2f} (Diff: ${tot_deb - tot_crd:.2f})")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def mytax_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        confirmed = self.jm.get_mytax_summary("2026-07-01", "2027-06-30")
        review_items = self.jm.get_mytax_review_summary("2026-07-01", "2027-06-30")
        unallocated = self.jm.get_unallocated_review_summary("2026-07-01", "2027-06-30")
        if not confirmed and not review_items and unallocated["count"] == 0:
            await update.message.reply_text("No business or PAYG tax deductions recorded yet for this financial year (FY 2026/2027).")
            return

        lines = [
            "🇦🇺 *SUGGESTED ATO myTAX DEDUCTIONS SUMMARY* (FY 2026/2027):",
            f"_{ATO_SUGGESTION_DISCLAIMER}_\n",
        ]
        tot_confirmed = 0.0
        if confirmed:
            lines.append("✅ *Confirmed / Ready Claim Totals:*")
            for s in confirmed:
                stream_tag = str(s.get("claim_stream") or "abn").upper()
                lines.append(
                    f"• *[{stream_tag}] {s['tax_label']}* ({s['count']}x): `${s['gross_amount']:.2f} AUD` "
                    f"(Net: `${s['net_amount']:.2f}`, GST: `${s['total_gst']:.2f}`)"
                )
                tot_confirmed += s["gross_amount"]
        else:
            lines.append("✅ *Confirmed / Ready Claim Totals:* `$0.00 AUD`")

        lines.append(f"\n*Total Confirmed Suggested Deductions*: `${tot_confirmed:.2f} AUD`")

        if review_items:
            tot_review = sum(r["gross_amount"] for r in review_items)
            lines.append("\n⚠️ *Segregated Needs-Review Items (Excluded from Confirmed Totals):*")
            for r in review_items:
                stream_tag = str(r.get("claim_stream") or "abn").upper()
                lines.append(
                    f"• *[{stream_tag}] {r['tax_label']}* ({r['count']}x): `${r['gross_amount']:.2f} AUD` _(Needs owner review)_"
                )
            lines.append(f"*Pending Review Total (Excluded)*: `${tot_review:.2f} AUD`")

        if unallocated["count"] > 0:
            lines.append(f"\n⚠️ *Unallocated Needs-Review Records ({unallocated['count']})*:")
            lines.append(
                f"• {unallocated['count']} receipt(s) pending activity/percentage allocation "
                f"(Receipt IDs: {', '.join(f'#{i}' for i in unallocated['receipt_ids'])})"
            )
            lines.append("_Excluded from deductions until activity/stream allocation is confirmed with `/correct`._")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def total_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Muscle memory alias for /mytax."""
        await self.mytax_command(update, context)

    async def pnl_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        pnl = self.jm.get_pnl_summary("2026-07-01", "2027-06-30")
        lines = [
            f"📊 *PROFIT & LOSS SUMMARY* ({pnl['period']})",
            f"_Strictly in Australian Dollars ({pnl['currency']})_\n",
            "🐕 *Dog Walking ABN Activity*:",
            f"• Confirmed Income: `${pnl['dog_walking']['income']:.2f} AUD`",
            f"• Expenses (Claimable): `${pnl['dog_walking']['expenses']:.2f} AUD`",
            f"• Net Profit: `${pnl['dog_walking']['net']:.2f} AUD`",
        ]
        if pnl["dog_walking"].get("pending_income", 0.0) > 0:
            lines.append(f"• ⚠️ Pending Review Income (Excluded): `${pnl['dog_walking']['pending_income']:.2f} AUD`")

        lines.extend([
            "",
            "🖨️ *3D Printing ABN Activity*:",
            f"• Confirmed Income: `${pnl['printing_3d']['income']:.2f} AUD`",
            f"• Expenses (Claimable): `${pnl['printing_3d']['expenses']:.2f} AUD`",
            f"• Net Profit: `${pnl['printing_3d']['net']:.2f} AUD`",
        ])
        if pnl["printing_3d"].get("pending_income", 0.0) > 0:
            lines.append(f"• ⚠️ Pending Review Income (Excluded): `${pnl['printing_3d']['pending_income']:.2f} AUD`")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━",
            f"💰 *Total Revenue*: `${pnl['total_income']:.2f} AUD`",
            f"💸 *Total Business Expenses*: `${pnl['total_expenses']:.2f} AUD`",
            f"🏆 *Net Profit (FY 2026/27)*: `${pnl['net_profit']:.2f} AUD`",
        ])
        if pnl.get("pending_income", 0.0) > 0:
            lines.append(f"⚠️ *Pending Review Income (Excluded from Profit)*: `${pnl['pending_income']:.2f} AUD`")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def spending_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        arg = (context.args[0] if context and context.args else "").strip().lower()
        import datetime
        if arg == "last":
            today = datetime.date.today()
            first = today.replace(day=1)
            prev_month = first - datetime.timedelta(days=1)
            target_month = prev_month.strftime("%Y-%m")
        elif len(arg) == 7 and arg[4] == "-":
            target_month = arg
        else:
            target_month = datetime.date.today().strftime("%Y-%m")

        data = self.jm.get_spending_summary(target_month)
        if data["receipt_count"] == 0 and data.get("pending_review_count", 0) == 0:
            if data.get("transfers_excluded", 0.0) > 0:
                await update.message.reply_text(
                    f"No consumption expenses recorded for {data['month']} (${data['transfers_excluded']:.2f} AUD in internal transfers/repayments excluded)."
                )
            else:
                await update.message.reply_text(f"No expenses recorded for {data['month']}.")
            return

        lines = [
            f"💳 *MONTHLY SPENDING BREAKDOWN* ({data['month']})",
            f"_Total Expenses: ${data['total_spent']:.2f} AUD ({data['receipt_count']} receipts)_\n",
            f"💼 *Business Claimable*: `${data['business_claimable']:.2f} AUD`",
            f"🏠 *Personal Living*: `${data['personal_share']:.2f} AUD`\n",
        ]
        if data.get("pending_review_spending", 0.0) > 0:
            lines.append(
                f"⚠️ *Pending Review Expenses (Excluded from Confirmed)*: `${data['pending_review_spending']:.2f} AUD` ({data['pending_review_count']} receipts)\n"
            )

        if data["by_category"]:
            lines.append("📂 *By Category*:")
            for cat in data["by_category"]:
                lines.append(f"• `{cat['category']}`: `${cat['amount']:.2f} AUD` ({cat['pct']}%)")

        if data.get("transfers_excluded", 0.0) > 0:
            lines.append(
                f"\n_ℹ️ Excluded ${data['transfers_excluded']:.2f} AUD in internal transfers / financing repayments from consumption._"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def backup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        await update.message.reply_text("⏳ Taking a database snapshot and uploading to Google Drive...")
        backup_id = self.gdrive.backup_database([self.db_path])
        if backup_id:
            await update.message.reply_text(
                f"✅ Snapshot successfully backed up to Google Drive! (File ID: `{backup_id}`)",
                parse_mode="Markdown",
            )
        else:
            err = self.gdrive.last_error or "GDRIVE_BACKUPS_FOLDER_ID not configured or CLI error"
            await update.message.reply_text(
                f"⚠️ *Google Drive Backup Failed*: `{err}` — snapshot was not uploaded to Google Drive.",
                parse_mode="Markdown",
            )

    def run(self):
        """Starts the Telegram bot long polling loop."""
        if not self.bot_token:
            print("ERROR: TELEGRAM_BOT_TOKEN is not set.")
            sys.exit(1)
        if not self.owner_chat_id:
            print("ERROR: TELEGRAM_OWNER_CHAT_ID is not set.")
            sys.exit(1)

        app = Application.builder().token(self.bot_token).build()
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("recent", self.recent_command))
        app.add_handler(CommandHandler("categories", self.categories_command))
        app.add_handler(CommandHandler("correct", self.correct_command))
        app.add_handler(CommandHandler("reward", self.reward_command))
        app.add_handler(CommandHandler("companion", self.companion_command))
        app.add_handler(CommandHandler("balance", self.balance_command))
        app.add_handler(CommandHandler("mytax", self.mytax_command))
        app.add_handler(CommandHandler("total", self.total_command))
        app.add_handler(CommandHandler("pnl", self.pnl_command))
        app.add_handler(CommandHandler("report", self.pnl_command))
        app.add_handler(CommandHandler("spending", self.spending_command))
        app.add_handler(CommandHandler("backup", self.backup_command))
        app.add_handler(CallbackQueryHandler(self.handle_correction_callback, pattern=r"^corr:"))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))

        print("🤖 Telegram Bot Neo-D LedgerCraft (@neo_ledgercraft_bot) is running...")
        app.run_polling()


if __name__ == "__main__":
    bot = TelegramReceiptBot()
    bot.run()
