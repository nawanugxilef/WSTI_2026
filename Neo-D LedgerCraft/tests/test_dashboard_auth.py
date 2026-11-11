import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from src.interfaces import dashboard_auth as auth
from src.interfaces.telegram_bot import TelegramReceiptBot


def _login_code(url):
    parsed = urlsplit(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{auth.DASHBOARD_ORIGIN}/dashboard"
    assert not parsed.query
    return parse_qs(parsed.fragment)["code"][0]


def test_dashboard_link_and_session_are_distinct_and_revocable(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    code = _login_code(auth.create_login_url(db_path))
    assert not auth.get_session(db_path, f"{auth.DASHBOARD_COOKIE}={code}")

    cookie = auth.issue_session(db_path, code)
    assert cookie.endswith(f"; Max-Age={auth.SESSION_SECONDS}; Path=/; Secure; HttpOnly; SameSite=Strict")
    browser_cookie = cookie.split(";", 1)[0]
    session_token = browser_cookie.split("=", 1)[1]
    assert auth.get_session(db_path, f"other=1; {browser_cookie}")
    assert auth.issue_session(db_path, code) is None
    assert auth.issue_session(db_path, session_token) is None

    store = Path(f"{db_path}.dashboard-auth.db").read_bytes()
    assert code.encode() not in store
    assert session_token.encode() not in store
    assert not Path(db_path).exists()

    assert "Max-Age=0" in auth.clear_session(db_path, browser_cookie)
    assert not auth.get_session(db_path, browser_cookie)


def test_dashboard_login_code_redeems_once_under_concurrency(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    code = _login_code(auth.create_login_url(db_path))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: auth.issue_session(db_path, code), range(4)))
    assert sum(result is not None for result in results) == 1


def test_dashboard_codes_and_sessions_expire(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ledger.db")
    now = 1_000_000
    monkeypatch.setattr(auth.time, "time", lambda: now)
    code = _login_code(auth.create_login_url(db_path))
    session_code = _login_code(auth.create_login_url(db_path))
    cookie = auth.issue_session(db_path, session_code).split(";", 1)[0]

    now += auth.LOGIN_SECONDS
    assert auth.issue_session(db_path, code) is None
    assert auth.get_session(db_path, cookie)

    now += auth.SESSION_SECONDS - auth.LOGIN_SECONDS
    assert not auth.get_session(db_path, cookie)


def test_dashboard_auth_fails_closed_without_store(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    assert auth.issue_session(db_path, "A" * 43) is None
    assert auth.issue_session(db_path, None) is None
    assert not auth.get_session(db_path, f"{auth.DASHBOARD_COOKIE}={'A' * 43}")
    assert not auth.get_session(db_path, f"{auth.DASHBOARD_COOKIE}=garbage")
    assert "Max-Age=0" in auth.clear_session(db_path, "")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("chat_id", "chat_type", "sender_id", "allowed"),
    [
        (123, "private", 123, True),
        (456, "private", 456, False),
        (123, "private", 456, False),
        (123, "group", 123, False),
    ],
)
@pytest.mark.parametrize("deep_link", [False, True])
def test_telegram_dashboard_command_only_issues_in_owner_private_chat(
    tmp_path, chat_id, chat_type, sender_id, allowed, deep_link
):
    bot = TelegramReceiptBot.__new__(TelegramReceiptBot)
    bot.owner_chat_id = 123
    bot.db_path = str(tmp_path / "ledger.db")
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=sender_id),
        message=SimpleNamespace(reply_text=reply_text),
    )

    if deep_link:
        asyncio.run(bot.start_command(update, SimpleNamespace(args=["dashboard"])))
    else:
        asyncio.run(bot.dashboard_command(update, None))

    if not allowed:
        reply_text.assert_not_awaited()
        assert list(tmp_path.iterdir()) == []
        return
    reply_text.assert_awaited_once()
    reply = reply_text.await_args.kwargs
    buttons = reply["reply_markup"].to_dict()["inline_keyboard"]
    code = _login_code(buttons[0][0]["url"])
    assert reply["protect_content"] is True
    assert buttons[0][0]["text"] == "Open Dashboard"
    assert buttons[1][0] == {"text": "Copy sign-in code", "copy_text": {"text": code}}
    assert auth.issue_session(bot.db_path, buttons[1][0]["copy_text"]["text"])
    assert auth.issue_session(bot.db_path, code) is None
