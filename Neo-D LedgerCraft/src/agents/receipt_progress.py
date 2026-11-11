"""Bounded receipt pipeline events with no accounting or source details."""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


_STAGE_RANK = {
    "neo_intake": 1,
    "corgi_reading": 2,
    "bear_checking": 3,
    "girraffe_filing": 4,
}
_OUTCOMES = {"recorded", "needs_review", "duplicate", "extraction_unavailable", "archive_unavailable", "error"}
_MAX_EVENTS = 80
_MAX_STATE_CHARS = 64 * 1024
_RUN_ID = re.compile(r"[a-f0-9]{32}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
logger = logging.getLogger(__name__)


def receipt_progress_path(db_path: str | os.PathLike) -> Path:
    return Path(db_path).expanduser().resolve().with_name("receipt_progress.json")


def _read_events(path: Path) -> list[dict]:
    try:
        with path.open(encoding="utf-8") as stream:
            payload = stream.read(_MAX_STATE_CHARS + 1)
        if len(payload) > _MAX_STATE_CHARS:
            return []
        state = json.loads(payload)
    except (OSError, ValueError, RecursionError):
        return []
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("events"), list):
        return []

    events = []
    for item in state["events"][-_MAX_EVENTS:]:
        if not isinstance(item, dict):
            continue
        run_id, seq, stage, at = (item.get(field) for field in ("run_id", "seq", "stage", "at"))
        if (
            not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id)
            or type(seq) is not int or not 1 <= seq <= len(_STAGE_RANK) + 1
            or not isinstance(stage, str) or (stage not in _STAGE_RANK and stage != "terminal")
            or not isinstance(at, str) or not _TIMESTAMP.fullmatch(at)
        ):
            continue
        event = {"run_id": run_id, "seq": seq, "stage": stage, "at": at}
        if stage == "terminal":
            outcome = item.get("outcome")
            if not isinstance(outcome, str) or outcome not in _OUTCOMES:
                continue
            event["outcome"] = outcome
        events.append(event)
    return events


def read_receipt_progress(db_path: str | os.PathLike) -> dict:
    """Return only the fields approved for the public office."""
    return {"version": 1, "events": _read_events(receipt_progress_path(db_path))}


@contextmanager
def _locked(path: Path):
    """Serialize short snapshot writes from the bot and web processes."""
    with path.open("a+b") as handle:
        deadline = time.monotonic() + 0.25
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _replace_snapshot(source: Path, target: Path) -> None:
    deadline = time.monotonic() + 0.10
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            # Windows readers can briefly hold the old snapshot open during replacement.
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.005)


def _append_event(path: Path, event: dict) -> None:
    with _locked(path.with_suffix(".lock")):
        events = [*_read_events(path), event][-_MAX_EVENTS:]
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=".receipt-progress-", suffix=".tmp", delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump({"version": 1, "events": events}, stream, separators=(",", ":"))
            _replace_snapshot(temp_path, path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


class ReceiptProgress:
    """One ordered run. Recording a stage never changes the intake result."""

    def __init__(self, path: Path | None):
        self.path = path
        self.run_id = uuid4().hex
        self.seq = 0
        self.rank = 0
        self.finished = False
        self._warned = False
        self.advance("neo_intake")

    @classmethod
    def for_connection(cls, conn: sqlite3.Connection) -> ReceiptProgress:
        try:
            for _, name, filename in conn.execute("PRAGMA database_list"):
                if name == "main" and filename:
                    return cls(receipt_progress_path(filename))
        except (sqlite3.Error, OSError, RuntimeError):
            logger.debug("Could not locate receipt progress database directory")
        return cls(None)

    def __enter__(self) -> ReceiptProgress:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.finished:
            self.finish("error")

    def _record(self, stage: str, outcome: str | None = None) -> None:
        self.seq += 1
        if self.path is None:
            return
        event = {
            "run_id": self.run_id,
            "seq": self.seq,
            "stage": stage,
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
        if outcome is not None:
            event["outcome"] = outcome
        try:
            _append_event(self.path, event)
        except OSError:
            if not self._warned:
                logger.warning("Receipt progress storage unavailable")
                self._warned = True

    def advance(self, stage: str) -> None:
        rank = _STAGE_RANK.get(stage, 0)
        if not self.finished and rank > self.rank:
            self.rank = rank
            self._record(stage)

    def finish(self, status: str) -> None:
        if self.finished:
            return
        self.finished = True
        if status in ("flagged", "pending", "unverified"):
            status = "needs_review"
        elif status in ("confirmed", "corrected"):
            status = "recorded"
        self._record("terminal", status if status in _OUTCOMES else "error")
