"""
Neo-D LedgerCraft setup wizard: connect your own Telegram bot and Gemini key.

Run with Docker:  docker compose run --rm setup
Run with Python:  python setup_wizard.py

It writes data/neo.env (kept out of git) and links the bot to your Telegram
account, so the bot answers only you. Standard library only.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / "data" / "neo.env"
TELEGRAM = "https://api.telegram.org/bot{token}/{method}"
GEMINI_MODELS = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"
LINK_SECONDS = 180


def telegram(token: str, method: str, **params) -> dict | None:
    """Call the Telegram Bot API; None on any failure (the token is never printed)."""
    data = urllib.parse.urlencode(params).encode() if params else None
    try:
        with urllib.request.urlopen(TELEGRAM.format(token=token, method=method), data=data, timeout=40) as response:
            body = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return body.get("result") if body.get("ok") else None


def gemini_key_works(key: str) -> bool:
    request = urllib.request.Request(GEMINI_MODELS, headers={"x-goog-api-key": key})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def wait_for_start(token: str, seconds: int = LINK_SECONDS, clock=time.monotonic) -> dict | None:
    """Return the first private chat that messages the bot: {"id", "first_name"}."""
    deadline = clock() + seconds
    offset = 0
    while clock() < deadline:
        updates = telegram(token, "getUpdates", offset=offset, timeout=25, allowed_updates='["message"]')
        if updates is None:
            time.sleep(2)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private" and isinstance(chat.get("id"), int):
                telegram(token, "getUpdates", offset=offset, timeout=0)  # mark it read
                return {"id": chat["id"], "first_name": chat.get("first_name") or ""}
    return None


def write_env(values: dict[str, str], path: Path = ENV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Written by setup_wizard.py. Keep this file private."]
    lines += [f"{key}={value}" for key, value in values.items()]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def ask_secret(prompt: str) -> str:
    return getpass.getpass(prompt + " (hidden while you paste): ").strip()


def main() -> int:
    print("\n=== Neo-D LedgerCraft setup ===\n")
    if ENV_PATH.exists() and input("Neo is already set up here. Replace the settings? [y/N] ").strip().lower() != "y":
        print("Nothing changed.")
        return 0

    print("Step 1 of 3 · Your Telegram bot")
    print("  1. Open Telegram and search for @BotFather.")
    print("  2. Send /newbot, pick a name, then a username that ends in 'bot'.")
    print("  3. BotFather replies with a token. Copy it.\n")
    bot = None
    for _ in range(3):
        token = ask_secret("Paste the bot token")
        bot = telegram(token, "getMe") if token else None
        if bot:
            break
        print("  That token didn't work. Copy the whole line from BotFather and try again.")
    if not bot:
        print("Setup stopped. Run it again when you have the token.")
        return 1
    username = bot.get("username", "")
    print(f"  ✓ Connected to @{username}\n")

    print("Step 2 of 3 · Gemini AI key (reads receipt photos)")
    print("  Open https://aistudio.google.com/apikey , sign in, and create a key.")
    print("  Press Enter to skip: typed notes still work with simple rules, but photos can't be read.\n")
    gemini = ""
    for _ in range(3):
        gemini = ask_secret("Paste the Gemini key")
        if not gemini or gemini_key_works(gemini):
            break
        print("  Google didn't accept that key. Check it and try again, or press Enter to skip.")
        gemini = ""
    print("  ✓ Gemini key works\n" if gemini else "  Skipped. Run setup again later to add it.\n")

    print("Step 3 of 3 · Link your Telegram account")
    print(f"  Open https://t.me/{username} and press Start (or send /start).")
    print(f"  Waiting up to {LINK_SECONDS // 60} minutes...")
    owner = wait_for_start(token)
    if not owner:
        print("  No message arrived. Run setup again and press Start in the bot chat.")
        return 1
    name = owner["first_name"] or "there"
    print(f"  ✓ Linked to {name}. The bot will answer only this account.\n")

    write_env({
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_OWNER_CHAT_ID": str(owner["id"]),
        "GEMINI_API_KEY": gemini,
        "NEO_RECEIPT_STORAGE": "local",
    })
    telegram(token, "sendMessage", chat_id=owner["id"],
             text=f"✅ Hi {name}, Neo is linked to you. Once Neo is started, send /setup to choose your businesses.")

    start = "docker compose up -d neo" if os.environ.get("NEO_IN_DOCKER") else "python -m src.interfaces.telegram_bot"
    print("All set. Settings saved in data/neo.env (private, not uploaded anywhere).")
    print(f"Start Neo with:  {start}")
    print("Then send /setup in Telegram to choose your businesses, and send your first receipt.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled. Nothing was saved.")
        sys.exit(1)
