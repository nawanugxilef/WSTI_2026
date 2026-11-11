"""Owner dashboard access shared by the Telegram bot and dashboard server."""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from hashlib import sha256
from http.cookies import CookieError, SimpleCookie
from pathlib import Path


DASHBOARD_ORIGIN = "https://neo.madewithfelix.app"
DASHBOARD_COOKIE = "__Host-neo-dashboard"
LOGIN_SECONDS = 5 * 60
SESSION_SECONDS = 14 * 24 * 60 * 60
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def _store_path(db_path: str) -> Path:
    return Path(f"{Path(db_path).resolve()}.dashboard-auth.db")


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=3)


def _digest(token: str) -> str:
    return sha256(token.encode("ascii")).hexdigest()


def _cookie_token(header: str) -> str | None:
    try:
        cookie = SimpleCookie(header).get(DASHBOARD_COOKIE)
    except CookieError:
        return None
    if cookie and _TOKEN.fullmatch(cookie.value):
        return cookie.value
    return None


def _session_cookie(token: str, max_age: int) -> str:
    return (
        f"{DASHBOARD_COOKIE}={token}; Max-Age={max_age}; "
        "Path=/; Secure; HttpOnly; SameSite=Strict"
    )


def create_login_url(db_path: str) -> str:
    """Mint a short-lived code for delivery to the owner's private Telegram chat."""
    path = _store_path(db_path)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)

    code = secrets.token_urlsafe(32)
    now = int(time.time())
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dashboard_tokens (
                token_hash TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('login', 'session')),
                expires_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("DELETE FROM dashboard_tokens WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO dashboard_tokens VALUES (?, 'login', ?)",
            (_digest(code), now + LOGIN_SECONDS),
        )
    return f"{DASHBOARD_ORIGIN}/dashboard#code={code}"


def issue_session(db_path: str, code: str) -> str | None:
    """Redeem a valid code once and return the Set-Cookie header."""
    if not isinstance(code, str) or not _TOKEN.fullmatch(code):
        return None
    now = int(time.time())
    code_hash = _digest(code)
    try:
        with closing(_connect(_store_path(db_path))) as conn, conn:
            candidate = conn.execute(
                """
                SELECT 1 FROM dashboard_tokens
                WHERE token_hash = ? AND kind = 'login' AND expires_at > ?
                """,
                (code_hash, now),
            ).fetchone()
            if candidate is None:
                return None
            used = conn.execute(
                """
                DELETE FROM dashboard_tokens
                WHERE token_hash = ? AND kind = 'login' AND expires_at > ?
                """,
                (code_hash, now),
            )
            if not used.rowcount:
                return None
            session_token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO dashboard_tokens VALUES (?, 'session', ?)",
                (_digest(session_token), now + SESSION_SECONDS),
            )
    except sqlite3.Error:
        return None
    return _session_cookie(session_token, SESSION_SECONDS)


def get_session(db_path: str, cookie_header: str) -> bool:
    """Check that the browser holds a current owner session."""
    token = _cookie_token(cookie_header)
    if not token:
        return False
    try:
        with closing(_connect(_store_path(db_path))) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM dashboard_tokens
                WHERE token_hash = ? AND kind = 'session' AND expires_at > ?
                """,
                (_digest(token), int(time.time())),
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def clear_session(db_path: str, cookie_header: str) -> str:
    """Revoke the browser session and return an expired Set-Cookie header."""
    token = _cookie_token(cookie_header)
    path = _store_path(db_path)
    if token and path.is_file():
        with closing(_connect(path)) as conn, conn:
            conn.execute(
                "DELETE FROM dashboard_tokens WHERE token_hash = ? AND kind = 'session'",
                (_digest(token),),
            )
    return _session_cookie("", 0)
