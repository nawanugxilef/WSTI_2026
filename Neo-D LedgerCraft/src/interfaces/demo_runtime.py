"""Isolated, bounded session intake for the local WSTI demo."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import re
import secrets
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.storage import businesses as biz
from src.agents.chief_of_staff import ChiefOfStaff, IntakeSummary
from src.agents.finance_agent import ExtractionUnavailableError, FinanceAgent, ParsedReceipt
from src.agents.receipt_progress import ReceiptProgress, read_receipt_progress, receipt_progress_path
from src.agents.wellbeing import coffee_check_in
from src.interfaces.cli import seed_accounts
from src.interfaces.demo_photo_ocr import PhotoOcrUnavailable
from src.interfaces.telegram_receipt import (
    category_icon,
    category_label,
    CATEGORY_STREAM_ICONS,
    format_existing_receipt_card,
    format_receipt_card,
)
from src.storage.categories import is_category_activity_compatible, load_category_catalog
from src.storage.db import get_connection, init_db


MAX_TEXT_CHARS = 300
MAX_FILES = 10
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * ((MAX_UPLOAD_BYTES + 2) // 3) + 65_536
SESSION_SECONDS = 30 * 60
MAX_SESSIONS = 32
SESSION_UNITS = 20
MAX_SESSION_CORRECTIONS = 40
GLOBAL_HOURLY_UNITS = 80
GEMINI_HOURLY_CALLS = 90
MAX_JOBS = 2
_ACTIVE = {"queued", "running"}
_POSTED = {"recorded", "confirmed", "corrected"}
_PERSONAL_CLASSIFICATION_REVIEW = {
    "Ambiguous note classified by heuristic — verify Category, Stream (ABN/PAYG/Personal), and Business %.",
    "Low-confidence AI suggestion — please verify Category, Business %, and ATO myTax label.",
    "Low confidence AI classification — needs owner review",
}
logger = logging.getLogger(__name__)


class DemoError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _sydney_today() -> date:
    try:
        return datetime.now(ZoneInfo("Australia/Sydney")).date()
    except ZoneInfoNotFoundError:
        return date.today()


def _category_editable(status: str, category: str, flag_reason: str | None) -> bool:
    return status in _POSTED or (
        status == "needs_review" and category != "transfer" and "duplicate" not in (flag_reason or "").casefold()
    )


@dataclass(frozen=True)
class Upload:
    name: str
    data: bytes


@dataclass(frozen=True)
class PhotoDraft:
    name: str
    digest: str


_PHOTO_DATE = re.compile(r"(?<!\w)(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4})(?!\w)")
_PHOTO_TOTAL = re.compile(
    r"^\s*(?:grand\s+total|total(?:\s+amount|\s+paid)?|amount\s+paid)\s*[:=]?\s*(?:AUD\s*)?(?:A?\$\s*)?"
    r"(\d+(?:,\d{3})*\.\d{2})(?:\s*AUD)?\s*$", re.I,
)


def suggest_photo_receipt(text: str) -> dict:
    """Extract editable suggestions only. The operator confirms all booking fields."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    dates = set()
    for candidate in _PHOTO_DATE.findall(text):
        try:
            pattern = "%d/%m/%Y" if "/" in candidate else "%Y-%m-%d"
            dates.add(datetime.strptime(candidate, pattern).date().isoformat())
        except ValueError:
            continue
    totals = set()
    for line in lines:
        match = _PHOTO_TOTAL.fullmatch(line)
        if match:
            value = float(match[1].replace(",", ""))
            if 0 < value <= 9_999_999.99:
                totals.add(value)
    vendor = next((line for line in lines[:4] if 2 <= len(line) <= 100
                   and any(char.isalpha() for char in line)
                   and not re.search(r"\d|\b(receipt|invoice|demo|date|abn|fictional)\b", line, re.I)), "")
    return {
        "vendor": vendor,
        "date": next(iter(dates)) if len(dates) == 1 else None,
        "total_amount": next(iter(totals)) if len(totals) == 1 else None,
    }


def parse_photo_confirmation(payload: object) -> tuple[str, ParsedReceipt]:
    if not isinstance(payload, dict) or set(payload) != {"draft_id", "vendor", "date", "total_amount"}:
        raise DemoError(400, "Confirm the vendor, date and AUD total.")
    draft_id, vendor, transaction_date, total = (payload[field] for field in ("draft_id", "vendor", "date", "total_amount"))
    if not isinstance(draft_id, str) or not re.fullmatch(r"[0-9a-f]{24}", draft_id):
        raise DemoError(400, "Choose a valid receipt photo.")
    if not isinstance(vendor, str) or not 1 <= len(vendor.strip()) <= 100 or not vendor.isprintable():
        raise DemoError(400, "Enter a vendor of up to 100 characters.")
    try:
        if date.fromisoformat(transaction_date).isoformat() != transaction_date:
            raise ValueError
    except (TypeError, ValueError) as error:
        raise DemoError(400, "Enter the transaction date.") from error
    if type(total) not in (int, float) or not 0 < total <= 9_999_999.99 or round(total, 2) != total:
        raise DemoError(400, "Enter the positive AUD total to two decimal places.")
    return draft_id, ParsedReceipt(
        vendor=vendor.strip(), date=transaction_date, total_amount=total, currency="AUD",
        category="general", purpose="personal", claim_stream="personal", business_activity="none", business_pct=0,
        payment_method="Unknown", confidence=1, classification_source="owner_correction",
    )


class ConfirmedPhotoFinance(FinanceAgent):
    def __init__(self, receipt: ParsedReceipt):
        super().__init__(api_key="")
        self.receipt = receipt

    def parse_document(self, *args, **kwargs):
        return self.receipt.model_copy()


def _decode_upload(value: object) -> Upload:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not isinstance(value.get("data"), str):
        raise DemoError(400, "Each file needs a name and file contents.")
    name = value["name"].replace("\\", "/").split("/")[-1]
    suffix = Path(name).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".pdf"):
        raise DemoError(400, "Choose a JPG or PNG photo, or one PDF.")
    encoded = value["data"]
    if len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        raise DemoError(413, "Each file must be 2 MiB or smaller.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise DemoError(400, "A file could not be decoded. Choose it again.") from error
    if len(data) > MAX_FILE_BYTES:
        raise DemoError(413, "Each file must be 2 MiB or smaller.")
    signature = {
        ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".png": b"\x89PNG\r\n\x1a\n", ".pdf": b"%PDF-",
    }[suffix]
    if not data.startswith(signature):
        raise DemoError(400, "A file does not match its extension. Choose a JPG, PNG, or PDF.")
    stem = re.sub(r"[^a-zA-Z0-9 ._-]", "", name[:-len(suffix)]).strip(" .")[:80]
    return Upload((stem or "receipt") + suffix, data)


def parse_intake(payload: object) -> tuple[str, list[Upload]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("text", ""), str):
        raise DemoError(400, "Enter a note or choose a receipt file.")
    note = payload.get("text", "").strip()
    if len(note) > MAX_TEXT_CHARS:
        raise DemoError(400, "Keep the note to 300 characters or fewer.")
    files = payload.get("files", [])
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise DemoError(400, "Upload at most 10 photos at a time.")
    if not note and not files:
        raise DemoError(400, "Enter a note or choose a receipt file.")
    uploads = [_decode_upload(item) for item in files]
    if len(uploads) > 1 and any(upload.name.endswith(".pdf") for upload in uploads):
        raise DemoError(400, "Send one PDF by itself or up to 10 photos together.")
    if sum(len(upload.data) for upload in uploads) > MAX_UPLOAD_BYTES:
        raise DemoError(413, "Keep the combined uploads to 10 MiB or less.")
    return note, uploads


def load_fixtures(path: Path) -> dict[str, ParsedReceipt]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    fixtures = {}
    for digest, data in payload.items():
        receipt = ParsedReceipt.model_validate(data)
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not receipt.vendor.startswith("[FICTIONAL DEMO]"):
            raise ValueError("The demo sample manifest contains an invalid sample.")
        fixtures[digest] = receipt
    return fixtures


class FixtureFinance(FinanceAgent):
    def __init__(self, fixtures: dict[str, ParsedReceipt]):
        super().__init__(api_key="")
        self._fixtures = fixtures

    def is_sample(self, digest: str) -> bool:
        return digest in self._fixtures

    def parse_document(self, file_path, user_note=None, qa_notes=None, vendor_examples=None, *, file_bytes=None):
        fixture = self._fixtures.get(hashlib.sha256(file_bytes or b"").hexdigest())
        if fixture is None:
            raise ExtractionUnavailableError("This file does not match a fictional demo sample.")
        return fixture.model_copy(deep=True)


class GeminiRequestBudget:
    """Count actual provider attempts, including model and QA fallbacks."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()

    def reserve(self) -> None:
        with self._lock:
            now = self._clock()
            while self._calls and self._calls[0] <= now - 3600:
                self._calls.popleft()
            if len(self._calls) >= GEMINI_HOURLY_CALLS:
                raise RuntimeError("The demo has reached its Gemini request limit.")
            self._calls.append(now)


class _DemoGeminiClient:
    def __init__(self, provider, budget: GeminiRequestBudget):
        self._provider = provider
        self._budget = budget
        self.models = self

    def generate_content(self, **kwargs):
        self._budget.reserve()
        try:
            kwargs["config"] = kwargs["config"].model_copy(update={"max_output_tokens": 2048})
            response = self._provider.models.generate_content(**kwargs)
            # Finance's fallback logger sees a generic error, never provider details or model input.
            receipt = ParsedReceipt.model_validate_json(response.text)
            return SimpleNamespace(text=receipt.model_dump_json())
        except Exception:
            raise RuntimeError("Gemini did not return a usable receipt.") from None

    def close(self) -> None:
        try:
            self._provider.close()
        except Exception:
            logger.warning("Could not close a demo Gemini connection")


def create_gemini_finance(api_key: str, budget: GeminiRequestBudget) -> FinanceAgent:
    # Use the existing model/fallback logic with a demo-owned client and request budget.
    from google import genai
    from google.genai import types

    finance = FinanceAgent(api_key="")
    provider = genai.Client(
        api_key=api_key,
        vertexai=False,
        http_options=types.HttpOptions(
            base_url="https://generativelanguage.googleapis.com",
            timeout=15_000,  # Gemini rejects deadlines under 10 s
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    finance._client = _DemoGeminiClient(provider, budget)
    return finance


class _MemoryUploadBoundary:
    def upload_receipt_bytes(self, *args, **kwargs):
        raise RuntimeError("This demo has no external archive.")


class DemoOrchestrator(ChiefOfStaff):
    """Reuse Neo's extraction, QA, and booking while keeping originals in RAM."""

    def __init__(self, connection, finance: FinanceAgent):
        super().__init__(connection, gdrive=_MemoryUploadBoundary(), finance_agent=finance)

    def existing_receipt(self, receipt_id: int) -> IntakeSummary | None:
        row = self.main_conn.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
        if row is None:
            return None
        original = self._build_summary_from_receipt_row(dict(row))
        original.exact_duplicate_of = original.receipt_id
        return original

    def exact_duplicate(self, digest: str) -> IntakeSummary | None:
        row = self.main_conn.execute("SELECT * FROM receipts WHERE file_hash = ?", (digest,)).fetchone()
        if row is None:
            return None
        original = self._build_summary_from_receipt_row(dict(row))
        original.exact_duplicate_of = original.receipt_id
        return original

    def process_demo_upload(self, upload: Upload, note: str, progress: ReceiptProgress, *, digest: str | None = None) -> IntakeSummary:
        with progress as trace:
            digest = digest or hashlib.sha256(upload.data).hexdigest()
            original = self.exact_duplicate(digest)
            if original:
                trace.finish("duplicate")
                return original
            archive = {"gdrive_file_id": None, "gdrive_status": "not_applicable", "gdrive_error": None}
            summary = self._process_archived_receipt(upload.name, upload.data, digest, note or None, archive, trace)
            trace.finish(summary.status)
            return summary


class _DemoProgress(ReceiptProgress):
    def __init__(self, database: Path, report: Callable[[str], None], run_id: str):
        self._report = report
        super().__init__(receipt_progress_path(database), run_id=run_id)

    def _record(self, stage: str, outcome: str | None = None) -> None:
        super()._record(stage, outcome)
        self._report(stage)


@dataclass
class Job:
    id: str
    total: int
    run_ids: list[str]
    state: str = "queued"
    stage: str | None = None
    results: list[dict] = field(default_factory=list)
    xp_gained: int = 0
    animation: str = "idle"


@dataclass
class Session:
    files: tempfile.TemporaryDirectory
    expires: float
    used: int = 0
    corrections: int = 0
    xp: int = 0
    job: Job | None = None
    photo_drafts: dict[str, PhotoDraft] = field(default_factory=dict)

    @property
    def database(self) -> Path:
        return Path(self.files.name) / "ledger.db"


class DemoRuntime:
    def __init__(
        self, mode: str, finance_factory: Callable[[], FinanceAgent], *,
        clock: Callable[[], float] = time.monotonic, session_seconds: int = SESSION_SECONDS,
        photo_reader: Callable[[bytes], str] | None = None,
        gemini_samples: FixtureFinance | None = None,
    ):
        self.mode = mode
        self.finance_factory = finance_factory
        self.photo_reader = photo_reader
        self.gemini_samples = gemini_samples
        self.session_seconds = session_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._category_catalog = load_category_catalog()
        self._sessions: dict[str, Session] = {}
        self._hourly: deque[tuple[float, int]] = deque()
        self._hourly_units = 0
        self._active = 0
        self._root = tempfile.TemporaryDirectory(prefix="neo-wsti-demo-")
        self._root_path = Path(self._root.name).resolve()
        self._executor = ThreadPoolExecutor(max_workers=MAX_JOBS, thread_name_prefix="neo-demo")
        self._stopping = threading.Event()
        self._sweeper = threading.Thread(target=self._reap, daemon=True, name="neo-demo-expiry")
        self._sweeper.start()

    def close(self) -> None:
        self._stopping.set()
        self._sweeper.join()
        self._executor.shutdown(wait=True)
        with self._lock:
            for session in self._sessions.values():
                self._clean_session(session)
            self._sessions.clear()
            if Path(self._root.name).resolve() == self._root_path:
                self._root.cleanup()

    def _clean_session(self, session: Session) -> None:
        if Path(session.files.name).resolve().parent == self._root_path:
            session.files.cleanup()

    def _reap(self) -> None:
        while not self._stopping.wait(15):
            with self._lock:
                self._sweep(self._clock())

    def _sweep(self, now: float) -> None:
        expired = [
            token for token, session in self._sessions.items()
            if session.expires <= now and (not session.job or session.job.state not in _ACTIVE)
        ]
        for token in expired:
            self._clean_session(self._sessions.pop(token))
        while self._hourly and self._hourly[0][0] <= now - 3600:
            self._hourly_units -= self._hourly.popleft()[1]

    def _find(self, token: str | None, now: float) -> Session | None:
        session = self._sessions.get(token)
        return session if session and session.expires > now else None

    def _require(self, token: str | None, now: float) -> Session:
        session = self._find(token, now)
        if session is None:
            raise DemoError(401, "This demo session has expired. Start a new session.")
        return session

    def _snapshot(self, session: Session, now: float) -> dict:
        job = session.job
        return {
            "session": {
                "xp": session.xp,
                "remaining": SESSION_UNITS - session.used,
                "mode": self.mode,
                "expires_in": max(0, math.ceil(session.expires - now)),
            },
            "job": None if job is None else {
                "id": job.id, "state": job.state, "stage": job.stage, "run_ids": list(job.run_ids),
                "completed": len(job.results), "total": job.total, "results": list(job.results),
                "xp": {"gained": job.xp_gained, "total": session.xp, "animation": job.animation},
            },
        }

    def open_session(self, token: str | None) -> tuple[str, dict]:
        with self._lock:
            now = self._clock()
            self._sweep(now)
            session = self._find(token, now)
            if session is None:
                if len(self._sessions) >= MAX_SESSIONS:
                    raise DemoError(429, "The demo is busy. Try again later.")
                token = secrets.token_urlsafe(32)
                files = tempfile.TemporaryDirectory(prefix="session-", dir=self._root.name)
                session = Session(files=files, expires=now + self.session_seconds)
                self._sessions[token] = session
            return token, self._snapshot(session, now)

    def session(self, token: str | None) -> dict:
        with self._lock:
            now = self._clock()
            self._sweep(now)
            return self._snapshot(self._require(token, now), now)

    def progress(self, token: str | None) -> dict:
        with self._lock:
            now = self._clock()
            self._sweep(now)
            session = self._find(token, now)
            return read_receipt_progress(session.database) if session else {"version": 1, "events": []}

    def submit(self, token: str | None, payload: object) -> dict:
        self.session(token)
        confirmation = None
        if isinstance(payload, dict) and "photo_confirmation" in payload:
            if not self.photo_reader or set(payload) != {"photo_confirmation"}:
                raise DemoError(400, "Send the photo confirmation on its own.")
            confirmation = parse_photo_confirmation(payload["photo_confirmation"])
            note, uploads = "", []
        else:
            note, uploads = parse_intake(payload)
        capture_date = None
        if note and not uploads:
            try:
                capture_date = datetime.now(ZoneInfo("Australia/Sydney")).date()
            except ZoneInfoNotFoundError:
                pass
        units = len(uploads) or 1
        with self._lock:
            now = self._clock()
            self._sweep(now)
            session = self._require(token, now)
            if session.job and session.job.state in _ACTIVE:
                raise DemoError(409, "Wait for this session's current receipt job to finish.")
            if confirmation and confirmation[0] not in session.photo_drafts:
                raise DemoError(404, "This photo is no longer awaiting confirmation in your session.")
            if session.used + units > SESSION_UNITS:
                raise DemoError(429, "This session has reached its receipt limit.")
            if self._hourly_units + units > GLOBAL_HOURLY_UNITS or self._active >= MAX_JOBS:
                raise DemoError(429, "The demo is busy. Try again later.")
            job = Job(id=secrets.token_hex(12), total=units, run_ids=[secrets.token_hex(16) for _ in range(units)])
            self._executor.submit(self._run, session, job, note, uploads, confirmation, capture_date)
            session.job = job
            session.used += units
            self._hourly.append((now, units))
            self._hourly_units += units
            self._active += 1
            return self._snapshot(session, now)

    def correct(self, token: str | None, payload: object) -> dict:
        if not isinstance(payload, dict) or payload.keys() - {
            "receipt_id", "claim_stream", "business_activity", "business_pct", "category",
        }:
            raise DemoError(400, "Send a receipt and its updated category or allocation.")
        receipt_id = payload.get("receipt_id")
        if type(receipt_id) is not int or not 0 < receipt_id < 2**63:
            raise DemoError(400, "Choose a valid demo receipt.")
        category_edit = "category" in payload
        if category_edit:
            category = payload["category"]
            if payload.keys() != {"receipt_id", "category"} or not isinstance(category, str):
                raise DemoError(400, "Choose a valid category for this receipt.")
            changes = {"category": category}
        else:
            changes = self._allocation_correction(payload)

        with self._lock:
            now = self._clock()
            self._sweep(now)
            session = self._require(token, now)
            job = session.job
            if job and job.state in _ACTIVE:
                raise DemoError(409, "Wait for this session's current receipt job to finish.")
            if session.corrections >= MAX_SESSION_CORRECTIONS:
                raise DemoError(429, "This session has reached its receipt edit limit.")
            session.corrections += 1
            if not session.database.is_file():
                raise DemoError(404, "This receipt was not found in your demo session.")
            with closing(get_connection(session.database)) as connection:
                orchestrator = DemoOrchestrator(connection, FinanceAgent(api_key=""))
                current = orchestrator.km.get_receipt(receipt_id)
                if not current or not (
                    current["status"] in _POSTED or _category_editable(
                        current["status"], current["category"], current["flag_reason"],
                    )
                ):
                    raise DemoError(404, "This editable receipt was not found in your demo session.")
                if category_edit:
                    stream = current["claim_stream"]
                    activity = current["business_activity"]
                    spec = self._category_catalog.get(category)
                    if not spec or spec.stream != stream or not is_category_activity_compatible(
                        category, activity, self._category_catalog,
                    ):
                        raise DemoError(400, "Choose a category for this receipt's current allocation.")
                    if (
                        current["status"] == "needs_review" and stream == "personal" and activity == "none"
                        and current["business_pct"] == 0 and current["flag_reason"] in _PERSONAL_CLASSIFICATION_REVIEW
                    ):
                        # Picking a Personal category confirms the entire 0% Personal allocation.
                        changes["claim_stream"] = stream
                summary = orchestrator.correct_receipt(receipt_id, **changes, remember_vendor=False)
            result = self._receipt_result(summary)
            if job:
                for index, previous in enumerate(job.results):
                    if previous.get("receipt_id") != receipt_id:
                        continue
                    if previous["status"] == "duplicate":
                        job.results[index] = self._receipt_result(summary, name=previous["name"], duplicate=True)
                    elif previous["status"] in _POSTED | {"needs_review"}:
                        result = {**result, "name": previous["name"], "extraction": previous["extraction"]}
                        job.results[index] = result
            return {**self._snapshot(session, now), "result": result}

    @staticmethod
    def _allocation_correction(payload: dict) -> dict:
        stream = payload.get("claim_stream")
        activity = payload.get("business_activity")
        pct = payload.get("business_pct", 0 if stream == "personal" else None)
        if stream not in ("abn", "payg", "personal"):
            raise DemoError(400, "Choose ABN, PAYG or Personal.")
        if stream == "abn" and activity not in biz.abn_activity_keys():
            raise DemoError(400, "Choose which business this is for.")
        if stream == "payg" and activity not in (None, "payg_work"):
            raise DemoError(400, "PAYG uses the work activity.")
        if stream == "personal" and activity not in (None, "none"):
            raise DemoError(400, "Personal receipts have no business activity.")
        if type(pct) not in (int, float) or not 0 <= pct <= 100:
            raise DemoError(400, "Enter a percentage between 0 and 100.")
        if (stream == "personal" and pct != 0) or (stream != "personal" and round(pct, 2) == 0):
            raise DemoError(400, "Use 0% for Personal and a positive share for ABN or PAYG.")
        return {"claim_stream": stream, "business_activity": activity, "business_pct": pct}

    def _prepare_photo(
        self, session: Session, orchestrator: DemoOrchestrator, upload: Upload, digest: str, trace: ReceiptProgress,
    ) -> dict:
        with trace:
            original = orchestrator.exact_duplicate(digest)
            if original:
                trace.finish("duplicate")
                return self._receipt_result(original, name=upload.name)
            trace.advance("corgi_reading")
            try:
                text = self.photo_reader(upload.data)
            except PhotoOcrUnavailable:
                text = ""
            if re.search(r"\b(?:USD|EUR|GBP|NZD|CAD|JPY|IDR|SGD)\b|[£€¥]", text, re.I):
                trace.finish("needs_review")
                return {"name": upload.name, "status": "needs_review", "message": "This preview records receipts in AUD only."}
            draft_id = secrets.token_hex(12)
            with self._lock:
                session.photo_drafts[draft_id] = PhotoDraft(upload.name, digest)
            trace.finish("needs_review")
            return {
                "name": upload.name, "status": "photo_confirmation", "draft_id": draft_id,
                "extraction": "local_ocr" if text else None,
                "message": "Check the details I read before recording." if text
                           else "I couldn’t read this photo clearly. Enter the details from the receipt.",
                **suggest_photo_receipt(text),
            }

    def _run(
        self, session: Session, job: Job, note: str, uploads: list[Upload],
        confirmation: tuple[str, ParsedReceipt] | None, capture_date: date | None,
    ) -> None:
        with self._lock:
            job.state = "running"

        def report(stage: str) -> None:
            with self._lock:
                job.stage = stage

        state = "done"
        finance = None
        try:
            with closing(get_connection(session.database)) as connection:
                init_db(connection)
                seed_accounts(connection)
                orchestrator = None
                for index, upload in enumerate(uploads or [None]):
                    if self._stopping.is_set() or self._clock() >= session.expires:
                        state = "failed"
                        break
                    trace = _DemoProgress(session.database, report, job.run_ids[index])
                    try:
                        digest = hashlib.sha256(upload.data).hexdigest() if upload else ""
                        if confirmation:
                            draft_id, parsed = confirmation
                            draft = session.photo_drafts[draft_id]
                            confirmed = DemoOrchestrator(connection, ConfirmedPhotoFinance(parsed))
                            summary = confirmed.process_demo_upload(Upload(draft.name, b""), "", trace, digest=draft.digest)
                            result = self._receipt_result(summary, name=draft.name, extraction="confirmed_photo")
                            with self._lock:
                                del session.photo_drafts[draft_id]
                        elif upload and self.gemini_samples is not None and self.gemini_samples.is_sample(digest):
                            sample_orchestrator = DemoOrchestrator(connection, self.gemini_samples)
                            summary = sample_orchestrator.process_demo_upload(upload, note, trace, digest=digest)
                            result = self._receipt_result(summary, name=upload.name, extraction="fixture")
                        else:
                            if orchestrator is None:
                                finance = self.finance_factory()
                                orchestrator = DemoOrchestrator(connection, finance)
                            if upload:
                                if (self.photo_reader and isinstance(finance, FixtureFinance)
                                        and not finance.is_sample(digest) and not upload.name.endswith(".pdf")):
                                    result = self._prepare_photo(session, orchestrator, upload, digest, trace)
                                else:
                                    summary = orchestrator.process_demo_upload(upload, note, trace, digest=digest)
                                    result = self._result(summary, upload)
                            else:
                                summary = orchestrator.process_note(note, capture_date=capture_date, progress=trace)
                                if summary.status == "duplicate" and summary.exact_duplicate_of:
                                    summary = orchestrator.existing_receipt(summary.exact_duplicate_of) or summary
                                result = self._result(summary, None)
                        if (result["status"] in _POSTED and connection.execute(
                            "SELECT 1 FROM journal_transactions WHERE reference = ?",
                            (f"receipt:{result['receipt_id']}",),
                        ).fetchone()) or (result["status"] == "needs_review" and result.get("receipt_id")):
                            # Womby rewards logging a receipt, including one that still needs a quick check.
                            self._award_xp(connection, session, job)
                            # Same check-in as Telegram; the receipt is already recorded either way.
                            check_in = coffee_check_in(connection, result["receipt_id"], capture_date or _sydney_today())
                            if check_in:
                                result["message"] += "\n\n" + check_in
                    except Exception:
                        logger.warning("One demo receipt could not be processed")
                        trace.finish("error")
                        result = self._failed_item(upload)
                    with self._lock:
                        job.results.append(result)
        except Exception:
            logger.warning("A demo job could not start or complete")
            state = "failed"
        finally:
            if finance is not None and isinstance(finance._client, _DemoGeminiClient):
                finance._client.close()
            with self._lock:
                job.state = state
                self._active -= 1
                self._sweep(self._clock())

    def _result(self, summary: IntakeSummary, upload: Upload | None) -> dict:
        if upload:
            extraction = ("fixture" if self.mode == "local" else self.mode) if summary.status != "extraction_unavailable" else None
        else:
            extraction = "manual" if summary.classification_source == "rule_heuristic" or self.mode != "gemini" else "gemini"
        return self._receipt_result(summary, name=upload.name if upload else None, extraction=extraction)

    def _receipt_result(
        self, summary: IntakeSummary, *, name: str | None = None, extraction: str | None = None, duplicate: bool = False,
    ) -> dict:
        duplicate = duplicate or bool(summary.exact_duplicate_of and summary.receipt_id)
        message = format_existing_receipt_card(summary) if duplicate else format_receipt_card(summary)
        if not duplicate and summary.status == "extraction_unavailable" and self.mode in ("fixture", "local"):
            message = ("Use a sample PDF or send a photo of the fictional receipt." if self.mode == "local" else
                       "This file does not match a downloadable fictional sample. Choose an unchanged sample or enter the vendor, date, and amount.")
        editable = summary.receipt_id > 0 and _category_editable(summary.status, summary.category, summary.flag_reason)
        category_options = [
            {
                "id": spec.key,
                "label": category_label(spec.key, None),
                "emoji": category_icon(spec.key) or CATEGORY_STREAM_ICONS.get(spec.stream, "🏠"),
            }
            for spec in self._category_catalog.values()
            if spec.stream == summary.claim_stream
            and is_category_activity_compatible(spec.key, summary.business_activity, self._category_catalog)
        ] if editable else []
        return {
            "name": name, "status": "duplicate" if duplicate else summary.status, "message": message,
            **({"receipt_status": summary.status} if duplicate else {}),
            "receipt_id": summary.receipt_id or summary.exact_duplicate_of, "vendor": summary.vendor or None,
            "date": summary.date, "total_amount": summary.total_amount, "category": summary.category,
            "extraction": None if duplicate else extraction, "claim_stream": summary.claim_stream if editable else None,
            "business_activity": summary.business_activity if editable else None,
            "business_pct": summary.business_pct if editable else None, "category_options": category_options,
        }

    @staticmethod
    def _failed_item(upload: Upload | None) -> dict:
        return {
            "name": upload.name if upload else None, "status": "error",
            "message": "Neo could not finish this receipt. Try again or enter its vendor, date, and amount.",
            "receipt_id": None, "vendor": None, "date": None, "total_amount": None, "category": None,
            "extraction": None, "claim_stream": None, "business_activity": None, "business_pct": None,
        }

    def _award_xp(self, connection, session: Session, job: Job) -> None:
        before = connection.execute("SELECT total_xp, reward_target_xp FROM companion_state WHERE id = 1").fetchone()
        total = before["total_xp"] + 10
        target = before["reward_target_xp"]
        unlocked = total // target > before["total_xp"] // target
        animation = "reward_unlocked" if unlocked else "happy"
        with connection:
            connection.execute(
                """UPDATE companion_state SET total_xp = ?, boxes_unlocked = ?, last_anim = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
                (total, total // target, animation),
            )
        with self._lock:
            session.xp = total
            job.xp_gained += 10
            if unlocked or job.animation != "reward_unlocked":
                job.animation = animation
