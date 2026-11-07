"""
Telegram Bot Interface for Neo-D LedgerCraft
Dedicated to zero-friction ADHD receipt & expense intake:
- Photos & PDF invoices (1-step single or multi-receipt capture)
- Bank statement uploads (CSV/PDF/Image with AI auto-sync & deduplication)
- Casual typed notes (e.g. "Fictional Demo Cafe coffee 6.50 today")
- Non-punitive XP & Autonomous Reward Box (/reward, /companion)
- /balance, /mytax, /backup commands
Restricted to owner only.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import yaml
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.agents.chief_of_staff import ChiefOfStaff
from src.interfaces.cli import load_config, seed_accounts
from src.storage.db import get_connection, init_db
from src.storage.gdrive import GDriveAdapter
from src.storage.journal import JournalManager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


class TelegramReceiptBot:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        owner_chat_id: Optional[int] = None,
    ):
        self.cfg = load_config()
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        owner_env = os.environ.get("TELEGRAM_OWNER_CHAT_ID")
        self.owner_chat_id = owner_chat_id or (int(owner_env) if owner_env else None)

        # Storage setup
        db_path = self.cfg.get("storage", {}).get("db_path", "./data/ledger.db")
        self.conn = get_connection(db_path)
        init_db(self.conn)
        seed_accounts(self.conn)

        gdrive_cfg = self.cfg.get("google_drive", {})
        self.gdrive = GDriveAdapter(
            receipts_folder_id=gdrive_cfg.get("receipts_folder_id"),
            backups_folder_id=gdrive_cfg.get("backups_folder_id"),
            statements_folder_id=gdrive_cfg.get("statements_folder_id"),
        )

        self.cos = ChiefOfStaff(main_conn=self.conn, gdrive=self.gdrive)
        self.jm = JournalManager(self.conn)

    def _is_authorized(self, update: Update) -> bool:
        if not self.owner_chat_id:
            return True
        return update.effective_chat.id == self.owner_chat_id

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
            os.makedirs("./data", exist_ok=True)
            with open("./data/companion_event.json", "w") as f:
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
            f"• 🦊 *Finance AI*: Single-call multimodal OCR & Australian ATO myTax classification\n"
            f"• 🐻‍❄️ *QA Reviewer*: Deterministic math, GST & duplicate audit (max 2 retries)\n"
            f"• 🦒 *Knowledge Manager*: Double-entry ledger & adaptive Vendor Memory\n\n"
            f"🎁 *Current XP & Reward Box:*\n"
            f"• Lifetime XP: `{state['total_xp']} XP` (Zero-streak, never resets!)\n"
            f"• Active Treat: *{state['reward_name']}*\n\n"
            f"⚡ *How to Use (1-Step Zero Friction):*\n"
            f"• 📸 *Snap 1 or multiple receipts in a single photo / PDF*\n"
            f"• 🏦 *Upload Bank Statement (CSV / PDF / Screenshot)* — AI auto-reconciles & catches missed receipts without duplicates!\n"
            f"• 💬 *Type a casual note (fictional examples)*: `Fictional Demo Cafe coffee 6.50 today` or `Fictional 3D Maker Supply nozzle kit 32.99`\n"
            f"• /reward — View or set your custom Reward Box treat (e.g. `/reward 2 Packs of Indomie`)\n"
            f"• /companion — Ping your physical ESP32 Desk Companion `( ^ ▽ ^ )`\n"
            f"• /balance — View Double-Entry Trial Balance\n"
            f"• /mytax — View Australian ATO myTax Deductions Summary\n"
            f"• /backup — Snapshot database to Google Drive"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

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

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        text = update.message.text.strip()
        if not text or text.startswith("/"):
            return

        await update.message.reply_chat_action("typing")
        try:
            summary = self.cos.process_note(text)
            xp_block = self._award_xp_and_emit(xp_gain=10, event_label=f"Note: {summary.vendor}")
            await update.message.reply_text(f"{summary.summary_text}\n{xp_block}", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error processing note: {e}")
            await update.message.reply_text(
                f"🐕 *Chief of Staff*: Oops, I had a tiny hiccup reading that note (`{e}`). Could you try sending it once more, Boss?",
                parse_mode="Markdown",
            )

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        await update.message.reply_chat_action("typing")
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        today = datetime.date.today()
        receipt_dir = f"./data/receipts/{today.year}/{today.month:02d}"
        os.makedirs(receipt_dir, exist_ok=True)
        local_path = os.path.join(receipt_dir, f"{photo.file_unique_id}.jpg")

        await file.download_to_drive(local_path)

        try:
            caption = (update.message.caption or "").lower()
            if any(k in caption for k in ("batch", "multi", "statement", "sync", "bank")):
                batch_res = self.cos.process_batch_or_statement(local_path)
                xp_block = self._award_xp_and_emit(
                    xp_gain=batch_res["xp_earned"],
                    event_label=f"Batch/Statement ({batch_res['recorded_count']} synced)",
                )
                await update.message.reply_text(f"{batch_res['summary_text']}\n{xp_block}", parse_mode="Markdown")
            else:
                summary = self.cos.process_file(local_path)
                xp_block = self._award_xp_and_emit(xp_gain=10, event_label=f"Photo Receipt: {summary.vendor}")
                await update.message.reply_text(f"{summary.summary_text}\n{xp_block}", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error processing photo: {e}")
            await update.message.reply_text(
                f"🦊 *Finance Agent*: Oops, my camera vision was a bit blurry (`{e}`). Could you snap or resend that receipt for me, Boss?",
                parse_mode="Markdown",
            )

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        doc = update.message.document
        if not doc.file_name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".csv")):
            await update.message.reply_text("Please send a Receipt/Invoice (PDF/JPG/PNG) or Bank Statement (CSV/PDF).")
            return

        await update.message.reply_chat_action("typing")
        file = await context.bot.get_file(doc.file_id)

        today = datetime.date.today()
        receipt_dir = f"./data/receipts/{today.year}/{today.month:02d}"
        os.makedirs(receipt_dir, exist_ok=True)
        local_path = os.path.join(receipt_dir, f"{doc.file_unique_id}_{doc.file_name}")

        await file.download_to_drive(local_path)

        try:
            fname_lower = doc.file_name.lower()
            caption = (update.message.caption or "").lower()
            if fname_lower.endswith(".csv") or any(k in fname_lower or k in caption for k in ("statement", "bank", "sync", "batch", "multi")):
                batch_res = self.cos.process_batch_or_statement(local_path)
                xp_block = self._award_xp_and_emit(
                    xp_gain=batch_res["xp_earned"],
                    event_label=f"Bank Statement Sync ({batch_res['recorded_count']} added)",
                )
                await update.message.reply_text(f"{batch_res['summary_text']}\n{xp_block}", parse_mode="Markdown")
            else:
                summary = self.cos.process_file(local_path)
                xp_block = self._award_xp_and_emit(xp_gain=10, event_label=f"Document: {summary.vendor}")
                await update.message.reply_text(f"{summary.summary_text}\n{xp_block}", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error processing document: {e}")
            await update.message.reply_text(
                f"🦊 *Finance Agent*: Oops, I ran into an issue parsing that document (`{e}`).",
                parse_mode="Markdown",
            )

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        rows = self.jm.get_trial_balance()
        lines = ["⚖️ *DOUBLE-ENTRY TRIAL BALANCE*:\n"]
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

        summary = self.jm.get_mytax_summary("2026-07-01", "2027-06-30")
        if not summary:
            await update.message.reply_text("No business tax deductions recorded yet for this financial year (FY 2026/2027).")
            return

        lines = ["🇦🇺 *ATO myTAX DEDUCTIONS SUMMARY* (FY 2026/2027):\n"]
        tot_gross = 0.0
        for s in summary:
            lines.append(
                f"• *{s['tax_label']}* ({s['count']}x): ${s['gross_amount']:.2f} "
                f"(Net: ${s['net_amount']:.2f}, GST: ${s['total_gst']:.2f})"
            )
            tot_gross += s["gross_amount"]
        lines.append(f"\n*Total Claimable Business Deductions*: `${tot_gross:.2f} AUD`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def backup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return

        await update.message.reply_text("⏳ Taking a database snapshot and uploading to Google Drive...")
        db_path = self.cfg.get("storage", {}).get("db_path", "./data/ledger.db")
        backup_id = self.gdrive.backup_database([db_path])
        if backup_id:
            await update.message.reply_text(
                f"✅ Snapshot successfully backed up to Google Drive! (File ID: `{backup_id}`)",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("⚠️ Backup upload skipped/failed. Verify GDRIVE_BACKUPS_FOLDER_ID in .env.")

    def run(self):
        """Starts the Telegram bot long polling loop."""
        if not self.bot_token:
            print("ERROR: TELEGRAM_BOT_TOKEN is not set.")
            sys.exit(1)

        app = Application.builder().token(self.bot_token).build()
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("reward", self.reward_command))
        app.add_handler(CommandHandler("companion", self.companion_command))
        app.add_handler(CommandHandler("balance", self.balance_command))
        app.add_handler(CommandHandler("mytax", self.mytax_command))
        app.add_handler(CommandHandler("backup", self.backup_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))

        print("🤖 Telegram Bot Neo-D LedgerCraft (@neo_ledgercraft_bot) is running...")
        app.run_polling()


if __name__ == "__main__":
    bot = TelegramReceiptBot()
    bot.run()
