"""Exercise the loopback demo without touching production or external services."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from ipaddress import IPv4Network
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.agents.finance_agent import FinanceAgent, ParsedReceipt
from src.interfaces import demo_runtime
from src.interfaces import demo_server as demo_server_module
from src.interfaces.demo_runtime import DemoError, DemoRuntime, FixtureFinance, load_fixtures
from src.interfaces.demo_server import DemoServer, FIXTURES as SAMPLE_MANIFEST, _validate_public_origin


PHOTOS = [b"\xff\xd8\xff" + f"[FICTIONAL DEMO] photo {i}".encode() for i in range(11)]
PDF = b"%PDF-1.7\n[FICTIONAL DEMO] invoice\n%%EOF"
FIXTURES = {
    hashlib.sha256(data).hexdigest(): ParsedReceipt(
        vendor=f"[FICTIONAL DEMO] shop {i}", date="2024-05-01", total_amount=10 + i,
        category="dining_coffee", purpose="personal", claim_stream="personal", business_activity="none",
        business_pct=0, confidence=0.95,
    )
    for i, data in enumerate([*PHOTOS, PDF])
}


def file(name, data):
    return {"name": name, "data": base64.b64encode(data).decode()}


class DemoClient:
    def __init__(self, server, cookie=None):
        self.port = server.server_port
        self.origin = f"http://127.0.0.1:{self.port}"
        self.cookie = cookie

    def request(self, method, path, payload=None, *, origin=None, headers=None):
        with closing(http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)) as connection:
            request_headers = dict(headers or {})
            if method == "POST":
                request_headers["Origin"] = origin or self.origin
            if self.cookie:
                request_headers["Cookie"] = self.cookie
            if payload is not None:
                request_headers["Content-Type"] = "application/json"
            connection.request(method, path, None if payload is None else json.dumps(payload), request_headers)
            response = connection.getresponse()
            body = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                body = json.loads(body)
            return response.status, dict(response.getheaders()), body

    def open(self):
        status, headers, body = self.request("POST", "/demo/api/session")
        assert status == 200
        cookie = headers["Set-Cookie"]
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie
        self.cookie = cookie.split(";", 1)[0]
        return body

    def intake(self, payload):
        status, _, data = self.request("POST", "/demo/api/intake", payload)
        assert status == 202, data
        job_id = data["job"]["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, _, data = self.request("GET", "/demo/api/session")
            assert status == 200
            assert data["job"]["id"] == job_id
            if data["job"]["state"] in ("done", "failed"):
                assert data["job"]["state"] == "done"
                return data
            time.sleep(0.01)
        pytest.fail("The demo did not finish processing.")


@pytest.fixture
def demo_server():
    fixtures = {**FIXTURES, **load_fixtures(SAMPLE_MANIFEST)}
    runtime = DemoRuntime("fixture", lambda: FixtureFinance(fixtures))
    server = DemoServer(0, runtime)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
        runtime.close()


def test_demo_landing_routes_only_and_isolation_headers(demo_server):
    client = DemoClient(demo_server)
    for path in ("/", "/demo"):
        status, headers, body = client.request("GET", path)
        assert status == 200
        assert b'/demo/app.js' in body and b'/demo/chat.css' in body
        assert b'href="/dashboard"' not in body
        assert headers["Cache-Control"] == "no-store"
    for path in ("/dashboard", "/api/dashboard", "/api/export.csv", "/demo/sample_receipts.json", "/demo/api/../.env"):
        assert client.request("GET", path)[0] == 404
    assert client.request("GET", "/demo/api/session")[0] == 401
    assert client.request("GET", "/api/office/receipt-progress")[2] == {"version": 1, "events": []}
    assert client.request("POST", "/demo/api/session", origin="https://example.org")[0] == 403
    assert client.request("GET", "/", headers={"Host": "example.org"})[0] == 403
    assert client.open()["session"]["mode"] == "fixture"


def test_local_photo_requires_confirmation_and_keeps_its_original_hash():
    photo = b"\xff\xd8\xff local fictional photo"
    reads = []

    def read_photo(data):
        reads.append(data)
        return "Fictional Receipt\nMallow Fern Cafe\nDate 08/11/2024\nSubtotal $9.50\nTOTAL AUD $11.55"

    runtime = DemoRuntime(
        "local", lambda: FixtureFinance(FIXTURES),
        photo_reader=read_photo,
    )
    with DemoServer(0, runtime) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = DemoClient(server)
            client.open()
            payload = {"files": [file("photo.jpg", photo)]}
            preview = client.intake(payload)
            assert preview["session"]["xp"] == 0
            draft = preview["job"]["results"][0]
            assert draft["status"] == "photo_confirmation"
            assert (draft["vendor"], draft["date"], draft["total_amount"]) == ("Mallow Fern Cafe", "2024-11-08", 11.55)
            other_draft = client.intake(payload)["job"]["results"][0]
            assert other_draft["status"] == "photo_confirmation" and other_draft["draft_id"] != draft["draft_id"]
            confirmation = {"photo_confirmation": {
                "draft_id": draft["draft_id"], "vendor": draft["vendor"], "date": draft["date"], "total_amount": 11.65,
            }}
            another_client = DemoClient(server)
            another_client.open()
            assert another_client.request("POST", "/demo/api/intake", confirmation)[0] == 404
            posted = client.intake(confirmation)
            assert posted["session"]["xp"] == 10
            result = posted["job"]["results"][0]
            assert result["status"] == "recorded" and result["total_amount"] == 11.65
            assert client.request("POST", "/demo/api/intake", confirmation)[0] == 404
            second_confirmation = {"photo_confirmation": {
                **confirmation["photo_confirmation"], "draft_id": other_draft["draft_id"], "total_amount": 98.75,
            }}
            race = client.intake(second_confirmation)
            duplicate_confirmation = race["job"]["results"][0]
            assert duplicate_confirmation["status"] == "duplicate" and duplicate_confirmation["receipt_status"] == "recorded"
            assert duplicate_confirmation["receipt_id"] == result["receipt_id"] and duplicate_confirmation["total_amount"] == 11.65
            assert race["job"]["xp"]["gained"] == 0
            assert client.request("POST", "/demo/api/intake", second_confirmation)[0] == 404
            repeat = client.intake(payload)
            duplicate_upload = repeat["job"]["results"][0]
            assert duplicate_upload["status"] == "duplicate" and duplicate_upload["receipt_status"] == "recorded"
            assert duplicate_upload["receipt_id"] == result["receipt_id"] and duplicate_upload["category_options"]
            assert duplicate_upload["vendor"] == "Mallow Fern Cafe" and duplicate_upload["total_amount"] == 11.65
            assert "Already saved" in duplicate_upload["message"] and "$11.65 AUD" in duplicate_upload["message"]
            assert repeat["session"]["xp"] == 10
            assert reads == [photo, photo]
        finally:
            server.shutdown()
            worker.join()
            runtime.close()


def test_photo_suggestion_leaves_missing_and_ambiguous_values_blank():
    suggestion = demo_runtime.suggest_photo_receipt(
        "Wattle Cafe\nDate e7/11/2026\nSubtotal $7.95\nGrand total $8.95\nTotal paid AUD $8.50",
    )
    assert suggestion == {"vendor": "Wattle Cafe", "date": None, "total_amount": None}


@pytest.mark.parametrize("mode", ["fixture", "gemini"])
def test_private_tailnet_listener_checks_origin_and_uses_a_separate_cookie(monkeypatch, mode):
    # Simulate the private adapter on loopback without needing a Tailscale installation in CI.
    monkeypatch.setattr(demo_server_module, "_TAILNET_IPV4", IPv4Network("127.0.0.1/32"))
    runtime = DemoRuntime(mode, lambda: FixtureFinance(FIXTURES))
    with DemoServer(0, runtime, tailnet_ip="127.0.0.1") as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = DemoClient(server)
            assert client.request("POST", "/demo/api/session", origin="https://foreign.example")[0] == 403
            status, headers, snapshot = client.request("POST", "/demo/api/session")
            assert status == 200
            assert snapshot["session"]["mode"] == mode
            cookie = headers["Set-Cookie"]
            assert cookie.startswith("neo-wsti-tailnet-demo=")
            assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" not in cookie
            client.cookie = cookie.split(";", 1)[0]
            assert client.request("GET", "/demo/api/session")[0] == 200
            assert client.request("GET", "/demo", headers={"Host": f"localhost:{server.server_port}"})[0] == 403
            monkeypatch.setattr(demo_server_module, "_TAILNET_IPV4", IPv4Network("100.64.0.0/10"))
            assert client.request("GET", "/demo")[0] == 403
        finally:
            server.shutdown()
            worker.join()
            runtime.close()


def test_manual_note_posts_with_xp_and_ambiguous_date_is_held(demo_server):
    client = DemoClient(demo_server)
    client.open()
    complete = client.intake({"text": "[FICTIONAL DEMO] Cafe coffee $6.50 on 2024-05-01"})
    result = complete["job"]["results"][0]
    assert result["status"] == "recorded" and result["total_amount"] == 6.5 and result["date"] == "2024-05-01"
    assert result["extraction"] == "manual"
    assert complete["job"]["xp"] == {"gained": 10, "total": 10, "animation": "happy"}
    sydney = ZoneInfo("Australia/Sydney")
    before = datetime.now(sydney).date().isoformat()
    shorthand = client.intake({"text": "coffee 5,5"})
    after = datetime.now(sydney).date().isoformat()
    result = shorthand["job"]["results"][0]
    assert result["status"] == "recorded" and result["total_amount"] == 5.5
    assert result["date"] in {before, after}
    assert result["claim_stream"] == "personal" and result["category"] == "dining_coffee"
    assert shorthand["job"]["xp"]["gained"] == 10

    incomplete = client.intake({"text": "[FICTIONAL DEMO] Cafe coffee last Friday $6.50"})
    result = incomplete["job"]["results"][0]
    assert result["status"] == "needs_review" and result["receipt_id"] is None and result["date"] is None
    assert "date" in result["message"].lower()
    assert incomplete["job"]["xp"]["gained"] == 0


def test_upload_dedupe_xp_and_progress_are_session_scoped(demo_server, monkeypatch):
    first, second = DemoClient(demo_server), DemoClient(demo_server)
    first.open()
    second.open()
    request = {"files": [file("sample.jpg", PHOTOS[0])]}
    a = first.intake(request)
    assert a["job"]["results"][0]["status"] == "recorded"
    assert a["job"]["results"][0]["receipt_id"] == 1
    assert a["job"]["results"][0]["extraction"] == "fixture"
    assert a["session"]["xp"] == 10
    first_events = first.request("GET", "/api/office/receipt-progress")[2]["events"]
    assert {event["run_id"] for event in first_events} == set(a["job"]["run_ids"])
    assert second.request("GET", "/demo/api/session")[2]["job"] is None
    assert second.request("GET", "/api/office/receipt-progress")[2]["events"] == []
    def extraction_forbidden(*args, **kwargs):
        raise AssertionError("An exact duplicate must skip extraction.")

    with monkeypatch.context() as patch:
        patch.setattr(FixtureFinance, "parse_document", extraction_forbidden)
        duplicate = first.intake(request)
    existing = duplicate["job"]["results"][0]
    assert existing["status"] == "duplicate" and existing["receipt_status"] == "recorded"
    assert existing["receipt_id"] == 1 and existing["total_amount"] == 10
    assert existing["claim_stream"] == "personal" and existing["category"] == "dining_coffee"
    assert existing["category_options"] == a["job"]["results"][0]["category_options"]
    assert "Already saved" in existing["message"] and "$10.00 AUD" in existing["message"]
    assert existing["extraction"] is None
    assert duplicate["job"]["xp"]["gained"] == 0
    b = second.intake(request)
    assert b["job"]["results"][0]["status"] == "recorded"
    assert b["job"]["results"][0]["receipt_id"] == 1
    assert b["session"]["xp"] == 10
    assert set(a["job"]["run_ids"]).isdisjoint(b["job"]["run_ids"])
    status, _, updated = first.request("POST", "/demo/api/correct", {"receipt_id": 1, "category": "groceries"})
    assert status == 200, updated
    assert updated["result"]["status"] == "corrected" and updated["result"]["receipt_id"] == 1
    latest = first.request("GET", "/demo/api/session")[2]["job"]["results"][0]
    assert latest["status"] == "duplicate" and latest["receipt_status"] == "corrected" and latest["category"] == "groceries"
    assert "Already saved" in latest["message"] and "Groceries" in latest["message"]
    assert second.request("GET", "/demo/api/session")[2]["job"]["results"][0]["category"] == "dining_coffee"
    assert first.request("GET", "/api/office/receipt-progress")[2]["events"]
    for session in demo_server.runtime._sessions.values():
        with closing(sqlite3.connect(session.database)) as connection:
            assert connection.execute("SELECT count(*) FROM receipts").fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM journal_transactions").fetchone()[0] == 1
            assert connection.execute("SELECT total_xp FROM companion_state").fetchone()[0] == 10
            assert connection.execute("SELECT gdrive_file_id FROM receipts").fetchone()[0] is None
        assert not list(session.database.parent.glob("*.jpg"))


def test_receipt_allocation_edits_reuse_the_session_receipt_and_journal(demo_server, monkeypatch):
    first, second = DemoClient(demo_server), DemoClient(demo_server)
    first.open()
    second.open()
    route = "/demo/api/correct"
    request = {"files": [file("sample.jpg", PHOTOS[0])]}
    original = first.intake(request)["job"]["results"][0]
    assert original["claim_stream"] == "personal" and original["business_pct"] == 0
    abn = {"receipt_id": 1, "claim_stream": "abn", "business_activity": "dog_walking", "business_pct": 50}
    assert second.request("POST", route, abn)[0] == 404
    assert DemoClient(demo_server).request("POST", route, abn)[0] == 401
    assert first.request("POST", route, abn, origin="https://foreign.example")[0] == 403
    assert second.intake(request)["job"]["results"][0]["receipt_id"] == original["receipt_id"] == 1

    monkeypatch.setattr(demo_server.runtime, "finance_factory", lambda: pytest.fail("Edits must not use OCR."))
    for invalid in (
        {**abn, "claim_stream": "business"},
        {**abn, "business_activity": None},
        {**abn, "business_pct": 0},
        {**abn, "business_pct": 101},
        {**abn, "business_pct": True},
        {**abn, "business_pct": float("nan")},
        {**abn, "receipt_id": True},
        {**abn, "receipt_id": 2**63},
        {**abn, "claim_stream": "personal"},
        {"receipt_id": 1, "claim_stream": "payg"},
    ):
        assert first.request("POST", route, invalid)[0] == 400
    assert first.request("POST", route, {**abn, "extra": "a" * 1024})[0] == 413

    edits = (
        (abn, "abn", "dog_walking", 50),
        ({"receipt_id": 1, "claim_stream": "personal"}, "personal", "none", 0),
        ({"receipt_id": 1, "claim_stream": "payg", "business_pct": 30}, "payg", "payg_work", 30),
    )
    first_session, second_session = demo_server.runtime._sessions.values()
    for payload, stream, activity, pct in edits:
        status, _, response = first.request("POST", route, payload)
        assert status == 200, response
        result = response["result"]
        assert result["status"] == "corrected" and result["receipt_id"] == original["receipt_id"]
        assert result["name"] == "sample.jpg" and result["extraction"] == "fixture"
        assert (result["claim_stream"], result["business_activity"], result["business_pct"]) == (stream, activity, pct)
        assert response["session"]["xp"] == 10
        assert response["session"]["remaining"] == 19
        assert first.request("GET", "/demo/api/session")[2]["job"]["results"] == [result]
        with closing(sqlite3.connect(first_session.database)) as connection:
            assert connection.execute("SELECT claim_stream, business_activity, business_pct, claimable_amount FROM receipts").fetchone() == (stream, activity, pct, pct / 10)
            assert connection.execute("SELECT reference FROM journal_transactions").fetchall() == [("receipt:1",)]
            assert connection.execute("SELECT sum(debit), sum(credit) FROM journal_lines").fetchone() == (10, 10)
            assert connection.execute("SELECT total_xp FROM companion_state").fetchone() == (10,)
            assert connection.execute("SELECT count(*) FROM vendor_memory").fetchone() == (0,)

    with closing(sqlite3.connect(second_session.database)) as connection:
        assert connection.execute("SELECT claim_stream, business_pct FROM receipts").fetchall() == [("personal", 0)]
    assert second.request("GET", "/demo/api/session")[2]["job"]["results"] == [original]

    monkeypatch.setattr(demo_runtime, "MAX_SESSION_CORRECTIONS", len(edits))
    assert first.request("POST", route, abn)[0] == 429


def test_ten_photos_eleventh_rejected_and_pdf(demo_server):
    client = DemoClient(demo_server)
    client.open()
    photos = [file(f"sample-{i}.jpg", data) for i, data in enumerate(PHOTOS)]
    assert client.request("POST", "/demo/api/intake", {"files": photos})[0] == 400
    assert client.request("GET", "/demo/api/session")[2]["session"]["remaining"] == 20
    job = client.intake({"files": photos[:10]})
    assert job["job"]["completed"] == 10
    assert all(result["status"] == "recorded" for result in job["job"]["results"])
    assert job["job"]["xp"] == {"gained": 100, "total": 100, "animation": "reward_unlocked"}
    assert client.request("POST", "/demo/api/intake", {"files": [*photos[:1], file("sample.pdf", PDF)]})[0] == 400
    pdf_job = client.intake({"files": [file("sample.pdf", PDF)]})
    assert pdf_job["job"]["results"][0]["status"] == "recorded"
    assert pdf_job["session"]["xp"] == 110


def test_invalid_and_unrecognised_uploads_have_no_xp(demo_server):
    client = DemoClient(demo_server)
    client.open()
    assert client.request("POST", "/demo/api/intake", {"files": [file("sample.jpg", b"bad")]})[0] == 400
    result = client.intake({"files": [file("sample.jpg", PHOTOS[0] + b"modified")]})
    assert result["job"]["results"][0]["status"] == "extraction_unavailable"
    assert result["job"]["results"][0]["receipt_id"] is None
    assert result["session"]["xp"] == 0


def test_downloadable_samples_feed_the_demo_pipeline(demo_server):
    client = DemoClient(demo_server)
    client.open()
    samples = (
        ("harbour-cafe.jpg", "image/jpeg", 6.60),
        ("paper-gum.jpg", "image/jpeg", 22.00),
        ("studio-supply.pdf", "application/pdf", 44.00),
    )
    for name, mime, amount in samples:
        status, headers, body = client.request("GET", f"/demo/samples/{name}")
        assert status == 200 and headers["Content-Type"] == mime
        assert headers["Content-Disposition"] == f'attachment; filename="{name}"'
        job = client.intake({"files": [file(name, body)]})["job"]
        assert job["results"][0]["status"] == "recorded"
        assert job["results"][0]["total_amount"] == amount
        assert job["results"][0]["extraction"] == "fixture"
        assert job["xp"]["gained"] == 10


def test_gemini_mode_keeps_downloads_offline_and_sends_other_inputs_to_provider():
    factory_calls = []
    provider_calls = []
    budget = demo_runtime.GeminiRequestBudget()
    receipt = next(iter(FIXTURES.values()))

    def generate_content(**kwargs):
        provider_calls.append(kwargs["contents"])
        return SimpleNamespace(text=receipt.model_dump_json())

    def finance_factory():
        factory_calls.append(True)
        provider = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content), close=lambda: None)
        finance = FinanceAgent(api_key="")
        finance._client = demo_runtime._DemoGeminiClient(provider, budget)
        return finance

    samples = FixtureFinance(load_fixtures(SAMPLE_MANIFEST))
    runtime = DemoRuntime("gemini", finance_factory, gemini_samples=samples)
    server = DemoServer(0, runtime)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        client = DemoClient(server)
        assert client.open()["session"]["mode"] == "gemini"
        for original, renamed, total in (
            ("harbour-cafe.jpg", "renamed.jpg", 6.60),
            ("studio-supply.pdf", "renamed.pdf", 44.00),
        ):
            status, _, contents = client.request("GET", f"/demo/samples/{original}")
            assert status == 200
            job = client.intake({"files": [file(renamed, contents)]})["job"]
            result = job["results"][0]
            assert (result["status"], result["extraction"], result["total_amount"]) == ("recorded", "fixture", total)
            assert job["xp"]["gained"] == 10
        assert not factory_calls and not provider_calls

        status, _, contents = client.request("GET", "/demo/samples/paper-gum.jpg")
        assert status == 200
        job = client.intake({"files": [file("paper-gum.jpg", contents), file("harbour-cafe.jpg", PHOTOS[0])]})["job"]
        assert [result["extraction"] for result in job["results"]] == ["fixture", "gemini"]
        assert job["xp"]["gained"] == 20
        assert len(factory_calls) == len(provider_calls) == 1
        assert provider_calls[0][0].inline_data.data == PHOTOS[0]

        note = "[FICTIONAL DEMO] shop 0 $35.40 2024-05-02"
        result = client.intake({"text": note})["job"]["results"][0]
        assert (result["status"], result["extraction"], result["total_amount"]) == ("recorded", "gemini", 35.40)
        assert len(factory_calls) == len(provider_calls) == 2
        assert note in provider_calls[1]
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
        runtime.close()


def test_expiry_discards_only_the_demo_session():
    now = [10.0]
    runtime = DemoRuntime("fixture", lambda: FixtureFinance(FIXTURES), clock=lambda: now[0], session_seconds=1)
    try:
        token, _ = runtime.open_session(None)
        directory = runtime._sessions[token].database.parent
        assert directory.is_dir()
        now[0] += 2
        with pytest.raises(DemoError) as error:
            runtime.session(token)
        assert error.value.status == 401
        assert not directory.exists()
        assert runtime.open_session(token)[0] != token
    finally:
        runtime.close()


def test_concurrent_jobs_and_session_processing_limits():
    release = threading.Event()

    class BlockingFinance(FixtureFinance):
        def parse_document(self, *args, **kwargs):
            if not release.wait(5):
                raise TimeoutError
            return super().parse_document(*args, **kwargs)

    runtime = DemoRuntime("fixture", lambda: BlockingFinance(FIXTURES))
    try:
        a, b, c = (runtime.open_session(None)[0] for _ in range(3))
        request = {"files": [file(f"sample-{i}.jpg", data) for i, data in enumerate(PHOTOS[:10])]}
        runtime.submit(a, request)
        runtime.submit(b, request)
        with pytest.raises(DemoError) as busy_session:
            runtime.submit(a, request)
        assert busy_session.value.status == 409
        with pytest.raises(DemoError) as edit_during_intake:
            runtime.correct(a, {"receipt_id": 1, "claim_stream": "personal"})
        assert edit_during_intake.value.status == 409
        with pytest.raises(DemoError) as busy_server:
            runtime.submit(c, request)
        assert busy_server.value.status == 429
        assert runtime.session(c)["session"]["remaining"] == 20
        release.set()

        def wait_for_completion(token):
            deadline = time.monotonic() + 5
            while runtime.session(token)["job"]["state"] != "done":
                assert time.monotonic() < deadline
                time.sleep(0.01)

        wait_for_completion(a)
        runtime.submit(a, request)
        wait_for_completion(a)
        with pytest.raises(DemoError) as session_limit:
            runtime.submit(a, request)
        assert session_limit.value.status == 429
        assert runtime.session(a)["session"]["remaining"] == 0
    finally:
        release.set()
        runtime.close()


@pytest.mark.parametrize("origin", [
    "http://demo.example.org", "https://demo.example.org/", "https://demo.example.org?x=1",
    "https://demo.example.org#x", "https://user@demo.example.org", "https://127.0.0.1",
    "https://localhost", "https://demo.local", "https://demo.example.org:443",
])
def test_invalid_public_origins_are_rejected(origin):
    with pytest.raises(ValueError):
        _validate_public_origin(origin)


def test_approved_public_origin_requires_matching_host_and_origin_on_loopback():
    origin = "https://demo.example.org"
    runtime = DemoRuntime("fixture", lambda: FixtureFinance(FIXTURES))
    server = DemoServer(0, runtime, public_origin=origin)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        client = DemoClient(server)
        assert server.server_address[0] == "127.0.0.1"
        assert client.request("GET", "/")[0] == 403
        headers = {"Host": "demo.example.org"}
        assert client.request("POST", "/demo/api/session", headers=headers)[0] == 403
        assert client.request("POST", "/demo/api/session", origin=origin)[0] == 403
        assert client.request("POST", "/demo/api/session", origin=origin, headers=headers)[0] == 200
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
        runtime.close()


def test_gemini_facade_limits_provider_calls_and_masks_errors(monkeypatch):
    monkeypatch.setattr(demo_runtime, "GEMINI_HOURLY_CALLS", 2)
    now = [0.0]
    budget = demo_runtime.GeminiRequestBudget(clock=lambda: now[0])
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("[FICTIONAL DEMO] private upstream exception")
        return SimpleNamespace(text=next(iter(FIXTURES.values())).model_dump_json())

    provider = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content), close=lambda: None)
    client = demo_runtime._DemoGeminiClient(provider, budget)
    config = SimpleNamespace(model_copy=lambda *, update: update)
    with pytest.raises(RuntimeError, match="Gemini did not return a usable receipt") as error:
        client.generate_content(config=config)
    assert "private upstream exception" not in str(error.value)
    assert ParsedReceipt.model_validate_json(client.generate_content(config=config).text).total_amount == 10
    assert calls[-1]["config"] == {"max_output_tokens": 2048}
    with pytest.raises(RuntimeError, match="Gemini request limit"):
        client.generate_content(config=config)
    assert len(calls) == 2
    now[0] = 3600
    client.generate_content(config=config)
    assert len(calls) == 3


def test_category_edit_updates_spending_and_preserves_allocation(demo_server):
    from src.storage.db import get_connection
    from src.storage.journal import JournalManager

    client = DemoClient(demo_server)
    client.open()
    result = client.intake({"files": [file("sample.jpg", PHOTOS[0])]})["job"]["results"][0]
    receipt_id = result["receipt_id"]
    route = "/demo/api/correct"
    assert {"id": "dining_coffee", "label": "Dining & coffee", "emoji": "☕"} in result["category_options"]
    assert {"id": "groceries", "label": "Groceries", "emoji": "🛒"} in result["category_options"]
    assert "dog_walking_supplies" not in {option["id"] for option in result["category_options"]}
    assert client.request("POST", route, {"receipt_id": receipt_id, "category": "dog_walking_supplies"})[0] == 400

    session = next(iter(demo_server.runtime._sessions.values()))
    with closing(get_connection(session.database)) as connection:
        spending = JournalManager(connection)
        assert spending.get_spending_summary("2024-05")["by_category"] == [{"category": "dining_coffee", "amount": 10, "pct": 100}]
        status, _, updated = client.request("POST", route, {"receipt_id": receipt_id, "category": "groceries"})
        assert status == 200, updated
        assert updated["result"]["receipt_id"] == receipt_id
        assert updated["result"]["category"] == "groceries"
        assert updated["session"]["xp"] == 10
        assert client.request("GET", "/demo/api/session")[2]["job"]["results"] == [updated["result"]]
        assert spending.get_spending_summary("2024-05")["by_category"] == [{"category": "groceries", "amount": 10, "pct": 100}]

        abn = {"receipt_id": receipt_id, "claim_stream": "abn", "business_activity": "dog_walking", "business_pct": 50}
        status, _, updated = client.request("POST", route, abn)
        assert status == 200, updated
        ids = {option["id"] for option in updated["result"]["category_options"]}
        assert "travel_parking" in ids and "materials_3d_printing" not in ids and "groceries" not in ids
        assert client.request("POST", route, {"receipt_id": receipt_id, "category": "materials_3d_printing"})[0] == 400
        status, _, updated = client.request("POST", route, {"receipt_id": receipt_id, "category": "travel_parking"})
        assert status == 200, updated
        result = updated["result"]
        assert (result["claim_stream"], result["business_activity"], result["business_pct"]) == ("abn", "dog_walking", 50)
        assert spending.get_spending_summary("2024-05")["business_claimable"] == 5
        assert spending.get_spending_summary("2024-05")["by_category"][0]["category"] == "travel_parking"
        assert connection.execute("SELECT reference FROM journal_transactions").fetchone()[0] == f"receipt:{receipt_id}"
        assert connection.execute("SELECT count(*) FROM journal_transactions").fetchone()[0] == 1
        assert connection.execute("SELECT sum(debit), sum(credit) FROM journal_lines").fetchone()[:] == (10, 10)
        assert updated["session"]["xp"] == 10


def test_personal_category_can_confirm_classification_review(demo_server):
    client = DemoClient(demo_server)
    client.open()
    note = "[FICTIONAL DEMO] Zed Widget $12.50 on 2024-05-01"
    result = client.intake({"text": note})["job"]["results"][0]
    assert result["status"] == "needs_review" and result["receipt_id"] > 0
    assert result["category_options"]
    route = "/demo/api/correct"
    session = next(iter(demo_server.runtime._sessions.values()))
    with closing(sqlite3.connect(session.database)) as connection:
        assert connection.execute("SELECT count(*) FROM journal_transactions").fetchone()[0] == 0
        status, _, updated = client.request("POST", route, {"receipt_id": result["receipt_id"], "category": "groceries"})
        assert status == 200, updated
        assert updated["result"]["status"] == "corrected" and updated["result"]["receipt_id"] == result["receipt_id"]
        assert connection.execute("SELECT category, claim_stream, business_pct FROM receipts").fetchone() == ("groceries", "personal", 0)
        assert connection.execute("SELECT count(*) FROM journal_transactions").fetchone()[0] == 1
        assert updated["session"]["xp"] == 10  # awarded once at intake, not again on review


def test_category_edit_cannot_post_flagged_duplicate_or_missing_evidence(demo_server, monkeypatch):
    client = DemoClient(demo_server)
    client.open()
    route = "/demo/api/correct"
    digest_a, digest_b = (hashlib.sha256(data).hexdigest() for data in PHOTOS[:2])
    fixtures = {**FIXTURES, digest_b: FIXTURES[digest_a]}
    monkeypatch.setattr(demo_server.runtime, "finance_factory", lambda: FixtureFinance(fixtures))
    original = client.intake({"files": [file("a.jpg", PHOTOS[0])]})["job"]["results"][0]
    flagged = client.intake({"files": [file("b.jpg", PHOTOS[1])]})["job"]["results"][0]
    assert flagged["status"] == "flagged" and flagged["receipt_id"] != original["receipt_id"]
    assert flagged["category_options"] == []
    assert client.request("POST", route, {"receipt_id": flagged["receipt_id"], "category": "groceries"})[0] == 404
    missing = client.intake({"text": "[FICTIONAL DEMO] Coffee"})["job"]["results"][0]
    assert missing["status"] == "needs_review" and missing["receipt_id"] is None
    assert missing["category_options"] == []
    session = next(iter(demo_server.runtime._sessions.values()))
    with closing(sqlite3.connect(session.database)) as connection:
        assert connection.execute("SELECT reference FROM journal_transactions").fetchall() == [(f"receipt:{original['receipt_id']}",)]


def test_written_date_note_names_vendor_awards_xp_and_asks_work_or_personal(demo_server):
    client = DemoClient(demo_server)
    client.open()
    logged = client.intake({"text": "[FICTIONAL DEMO] Bunnings 12 Nov 2025 hammer $45.90"})
    result = logged["job"]["results"][0]
    assert result["date"] == "2025-11-12" and result["total_amount"] == 45.9
    assert result["vendor"] == "[FICTIONAL DEMO] Bunnings"
    assert result["status"] == "needs_review" and result["receipt_id"] > 0
    assert result["claim_stream"] == "personal"
    assert logged["job"]["xp"]["gained"] == 10
    status, _, updated = client.request(
        "POST", "/demo/api/correct",
        {"receipt_id": result["receipt_id"], "claim_stream": "abn", "business_activity": "dog_walking", "business_pct": 100},
    )
    assert status == 200, updated
    assert updated["result"]["status"] in ("corrected", "confirmed", "recorded")
    assert updated["result"]["claim_stream"] == "abn"
    assert updated["session"]["xp"] == 10


def test_resent_note_reopens_original_without_extra_xp(demo_server):
    client = DemoClient(demo_server)
    client.open()
    note = {"text": "[FICTIONAL DEMO] Cafe coffee $6.50 on 2024-05-01"}
    first = client.intake(note)["job"]["results"][0]
    assert first["status"] == "recorded"
    repeat = client.intake(note)
    again = repeat["job"]["results"][0]
    assert again["status"] == "duplicate" and again["receipt_status"] == "recorded"
    assert again["receipt_id"] == first["receipt_id"] and again["category_options"]
    assert repeat["job"]["xp"]["gained"] == 0 and repeat["session"]["xp"] == 10
    other = client.intake({"text": "[FICTIONAL DEMO] Cafe coffee $7.20 on 2024-05-01"})["job"]["results"][0]
    assert other["status"] == "recorded" and other["receipt_id"] != first["receipt_id"]
