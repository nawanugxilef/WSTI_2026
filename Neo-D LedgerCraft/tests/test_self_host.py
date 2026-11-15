import hashlib
import importlib.util
from pathlib import Path

from src.agents.finance_agent import FinanceAgent
from src.interfaces.telegram_receipt import format_receipt_card
from src.storage.local_archive import LocalArchive

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("setup_wizard", ROOT / "setup_wizard.py")
wizard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wizard)


def test_local_archive_saves_and_verifies(tmp_path):
    data = b"%PDF-1.4 fictional receipt"
    digest = hashlib.sha256(data).hexdigest()
    archive = LocalArchive(tmp_path / "receipts")
    file_id = archive.upload_receipt_bytes(data, "../weird name.pdf", file_hash=digest)
    assert file_id.startswith("local:") and archive.last_status == "uploaded"
    saved = list((tmp_path / "receipts").iterdir())
    assert len(saved) == 1 and saved[0].read_bytes() == data and ".." not in saved[0].name
    assert archive.upload_receipt_bytes(data, "x.pdf", file_hash="0" * 64) is None
    assert archive.last_status == "failed"


def test_bot_in_local_mode_records_photo_without_drive(tmp_path, monkeypatch):
    from src.interfaces.telegram_bot import TelegramReceiptBot

    monkeypatch.setenv("NEO_RECEIPT_STORAGE", "local")
    bot = TelegramReceiptBot(
        bot_token="fictional_token", owner_chat_id=1, db_path=str(tmp_path / "ledger.db"),
        finance_agent=FinanceAgent(api_key=""), load_dotenv=False,
    )
    assert isinstance(bot.gdrive, LocalArchive)
    data = b"\xff\xd8\xff fictional jpg"
    file_id, status, _ = bot.cos._archive_original("r.jpg", data, hashlib.sha256(data).hexdigest())
    assert status == "uploaded" and file_id.startswith("local:")
    assert (tmp_path / "receipts").is_dir()


def test_receipt_card_says_saved_on_this_computer():
    from src.agents.chief_of_staff import IntakeSummary

    summary = IntakeSummary(
        receipt_id=1, status="recorded", vendor="[FICTIONAL DEMO] Cafe", date="2026-11-10", total_amount=6.6,
        category="meals_entertainment", claim_stream="personal", business_activity="none", business_pct=0,
        currency="AUD", purpose="", tax_label="", flag_reason="", summary_text="",
        gdrive_file_id="local:abc_r.jpg", gdrive_status="uploaded",
    )
    card = format_receipt_card(summary)
    assert "Saved on this computer" in card and "Drive" not in card


def test_wizard_links_first_private_chat_and_writes_env(tmp_path, monkeypatch):
    calls = []
    updates = [
        {"update_id": 5, "message": {"chat": {"id": -100, "type": "group"}}},
        {"update_id": 6, "message": {"chat": {"id": 4242, "type": "private", "first_name": "Sam"}}},
    ]

    def fake_telegram(token, method, **params):
        calls.append((method, params))
        return updates if method == "getUpdates" and params.get("timeout") else []

    monkeypatch.setattr(wizard, "telegram", fake_telegram)
    assert wizard.wait_for_start("t", seconds=5) == {"id": 4242, "first_name": "Sam"}
    assert ("getUpdates", {"offset": 7, "timeout": 0}) in calls

    env = tmp_path / "data" / "neo.env"
    wizard.write_env({"TELEGRAM_OWNER_CHAT_ID": "4242", "NEO_RECEIPT_STORAGE": "local"}, env)
    text = env.read_text(encoding="utf-8")
    assert "TELEGRAM_OWNER_CHAT_ID=4242" in text and "NEO_RECEIPT_STORAGE=local" in text
