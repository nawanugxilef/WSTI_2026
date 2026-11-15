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
LINK_SECONDS = 600
SETUP_PAGE = "https://neo-wsti-demo.madewithfelix.app/setup"


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


def wait_for_start(token: str, seconds: int = LINK_SECONDS, clock=time.monotonic, on_wait=None) -> dict | None:
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
        if on_wait:
            on_wait()
    return None


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    return {key.strip(): value.strip() for key, value in pairs}


def write_env(values: dict[str, str], path: Path = ENV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Written by setup_wizard.py. Keep this file private."]
    lines += [f"{key}={value}" for key, value in values.items()]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --- screens: one action at a time, progress always visible, nothing ever blames the user ---

def header(step: int, title: str, minutes: str) -> None:
    bar = "".join("●" if i <= step else "○" for i in range(1, 4))
    print(f"\n{bar}  Step {step} of 3 · {title}  (about {minutes})\n")


def pause(prompt: str = "Press Enter when you're ready") -> None:
    input(f"  → {prompt} ")


def ask_secret(prompt: str) -> str:
    value = getpass.getpass(f"  → {prompt} (it stays hidden, that's normal): ").strip()
    if value.lower() in ("q", "quit", "exit"):
        raise KeyboardInterrupt
    return value


def step_bot(saved: dict) -> tuple[str, str]:
    token = saved.get("TELEGRAM_BOT_TOKEN", "")
    bot = telegram(token, "getMe") if token else None
    if bot:
        print(f"✓ Already done: your bot @{bot.get('username', '')} is connected. Skipping step 1.")
        return token, bot.get("username", "")
    header(1, "Make your own Telegram bot", "2 minutes")
    print("  Easiest: open this checklist with copy buttons, then come back here with your token:")
    print(f"  {SETUP_PAGE}\n")
    print("  Or follow along here:")
    print("  In Telegram, search for  @BotFather  and open it.")
    pause()
    print("\n  Send it this message:  /newbot")
    print("  It asks for a name, then a username ending in 'bot'. Anything works, e.g. MyNeoBot.")
    pause("Press Enter when BotFather sends you a long token")
    while True:
        token = ask_secret("Paste the token here")
        bot = telegram(token, "getMe") if token else None
        if bot:
            break
        print("\n  Hmm, Telegram didn't recognise that one. My fault for not being clearer:")
        print("  the token looks like  123456789:AAH...  (numbers, a colon, then letters).")
        print("  Copy that whole line and paste again. Type q to stop; you can come back anytime.\n")
    username = bot.get("username", "")
    write_env({**saved, "TELEGRAM_BOT_TOKEN": token})
    print(f"\n  ✓ Nice! @{username} is yours. Step 1 done, saved.")
    return token, username


def step_gemini(saved: dict) -> str:
    if saved.get("GEMINI_API_KEY"):
        print("✓ Already done: Gemini key saved. Skipping step 2.")
        return saved["GEMINI_API_KEY"]
    header(2, "Let Neo read photos", "1 minute")
    print("  Open  https://aistudio.google.com/apikey  , sign in, and tap 'Create API key'.")
    print("  No key? Just press Enter to skip. Typed notes still work, and you can add it later.")
    while True:
        key = ask_secret("Paste the key, or press Enter to skip")
        if not key:
            print("\n  No problem, skipped. Run setup again whenever you want photo reading.")
            return ""
        if gemini_key_works(key):
            write_env({**read_env(), "GEMINI_API_KEY": key})
            print("\n  ✓ Great, Google accepted it. Step 2 done, saved.")
            return key
        print("\n  Google didn't accept that key. It usually starts with  AIza .")
        print("  Copy it again and paste, or press Enter to skip for now.\n")


def step_link(token: str, username: str) -> dict | None:
    header(3, "Connect the bot to you", "30 seconds")
    print(f"  Open  https://t.me/{username}  and press  Start .")
    print("  I'll wait here. No rush.\n")
    ticks = iter(range(1, 1000))
    owner = wait_for_start(token, on_wait=lambda: print(f"  …still waiting ({next(ticks)}), take your time"))
    if not owner:
        print("\n  I didn't see a message from you yet. That's okay.")
        print("  Run setup again: steps 1 and 2 are saved, so it jumps straight here.")
    return owner


def main() -> int:
    print("\n=== Neo-D LedgerCraft setup ===")
    print("3 short steps, about 4 minutes. Your progress is saved after each one.")
    saved = read_env()
    if saved.get("TELEGRAM_OWNER_CHAT_ID") and input("\nNeo is already set up here. Start over? [y/N] ").strip().lower() != "y":
        print("All good, nothing changed.")
        return 0
    if saved.get("TELEGRAM_OWNER_CHAT_ID"):
        saved = {}
        ENV_PATH.unlink(missing_ok=True)

    token, username = step_bot(saved)
    gemini = step_gemini(read_env())
    owner = step_link(token, username)
    if not owner:
        return 1
    name = owner["first_name"] or "there"
    write_env({
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_OWNER_CHAT_ID": str(owner["id"]),
        "GEMINI_API_KEY": gemini,
        "NEO_RECEIPT_STORAGE": "local",
    })
    telegram(token, "sendMessage", chat_id=owner["id"],
             text=f"✅ Hi {name}, Neo is linked to you! Once Neo is running, send /setup to pick your businesses.")
    start = "docker compose up -d neo" if os.environ.get("NEO_IN_DOCKER") else "python -m src.interfaces.telegram_bot"
    print(f"\n●●●  🎉 All done, {name}! Neo answers only you.")
    print("\n  One last thing. Start Neo with:\n")
    print(f"      {start}\n")
    print("  Then say hi in Telegram with /setup. Your data stays on this computer (data folder).")
    print("\n  💡 Neo answers while this computer is on. Off or asleep? Telegram holds your")
    print("     receipts for up to 24 hours and Neo catches up. Tip: turn off sleep mode.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n\nPaused. Anything you finished is saved; run setup again to pick up where you left off.")
        sys.exit(1)
