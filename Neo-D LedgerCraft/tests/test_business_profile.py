import sqlite3

import pytest

from src.agents.finance_agent import FinanceAgent, system_prompt
from src.agents.knowledge_manager import KnowledgeManager
from src.storage import businesses as biz
from src.storage.categories import load_category_catalog
from src.storage.db import init_db
from src.storage.journal import JournalManager


@pytest.fixture
def profile(tmp_path):
    biz.configure(tmp_path / "ledger.db")
    yield tmp_path
    biz.configure(None)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_default_profile_is_the_two_original_businesses(profile):
    assert biz.abn_activity_keys() == ("printing_3d", "dog_walking")
    assert not biz.is_configured()
    assert "materials_3d_printing" in load_category_catalog()


def test_setup_replaces_businesses_and_categories(profile):
    biz.save_profile(["cleaning", {"key": biz.custom_key("Mobile Mechanic"), "name": "Mobile *Mechanic*"}], has_payg_job=False)
    assert biz.abn_activity_keys() == ("cleaning", "custom_mobile_mechanic")
    assert biz.get_business("custom_mobile_mechanic").name == "Mobile Mechanic"
    assert not biz.has_payg()
    cat = load_category_catalog()
    assert cat["supplies_cleaning"].tax_label == "Purchases and other costs"
    assert cat["income_custom_mobile_mechanic"].tax_label == "Other business income"
    assert "materials_3d_printing" not in cat and "dog_walking_supplies" not in cat
    assert "office_supplies" in cat  # shared categories stay
    assert '"cleaning" for Cleaning' in system_prompt()


def test_rule_fallback_and_pnl_follow_the_profile(profile):
    biz.save_profile(["cleaning"], has_payg_job=True)
    parsed = FinanceAgent.__new__(FinanceAgent)._heuristic_parse_text("Bunnings cleaning products $30")
    assert (parsed.category, parsed.business_activity) == ("supplies_cleaning", "cleaning")

    conn = _conn()
    km = KnowledgeManager(conn)
    for cat, amount in (("supplies_cleaning", 30.0), ("income_cleaning", 200.0)):
        km.record_receipt({
            "vendor": "[FICTIONAL DEMO] Sparkle", "date": "2026-11-10", "total_amount": amount,
            "category": cat, "claim_stream": "abn", "business_activity": "cleaning",
            "business_pct": 100, "confidence": 0.95, "classification_source": "owner_correction",
        })
    pnl = JournalManager(conn).get_pnl_summary()
    assert pnl["cleaning"] == {"income": 200.0, "expenses": 30.0, "pending_income": 0.0, "net": 170.0}
    assert [b["key"] for b in pnl["businesses"]] == ["cleaning"]
    revenue = conn.execute("SELECT name FROM accounts WHERE code = '4200'").fetchone()
    assert revenue["name"] == "Cleaning Revenue"


def test_bad_profile_falls_back_to_defaults(profile):
    (profile / "business_profile.json").write_text("{not json", encoding="utf-8")
    assert biz.abn_activity_keys() == biz.DEFAULT_KEYS
    with pytest.raises(ValueError):
        biz.save_profile(["no_such_business"], has_payg_job=True)


def test_setup_command_flow_saves_the_profile(profile):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from src.interfaces.telegram_bot import TelegramReceiptBot

    bot = TelegramReceiptBot(
        bot_token="fictional_token", owner_chat_id=12345, db_path=str(profile / "ledger.db"),
        finance_agent=FinanceAgent(api_key=""), load_dotenv=False,
    )
    context = MagicMock()
    context.user_data = {}
    update = MagicMock()
    update.effective_chat.id = 12345
    update.message.reply_text = AsyncMock()
    asyncio.run(bot.setup_command(update, context))
    assert context.user_data["setup_picked"] == ["printing_3d", "dog_walking"]

    def tap(data):
        cb = MagicMock()
        cb.effective_chat.id = 12345
        cb.callback_query.data = data
        cb.callback_query.answer = AsyncMock()
        cb.callback_query.edit_message_text = AsyncMock()
        cb.callback_query.message.reply_text = AsyncMock()
        asyncio.run(bot.handle_setup_callback(cb, context))

    tap("setup:toggle:dog_walking")
    tap("setup:toggle:photography")
    tap("setup:custom:_")
    asyncio.run(bot.handle_text(_text_update("Mobile Mechanic"), context))
    tap("setup:next:_")
    tap("setup:payg:no")
    assert biz.abn_activity_keys() == ("printing_3d", "photography", "custom_mobile_mechanic")
    assert not biz.has_payg()


def _text_update(text):
    from unittest.mock import AsyncMock, MagicMock

    update = MagicMock()
    update.effective_chat.id = 12345
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update
