import csv
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from urllib.parse import urlparse
from unittest.mock import MagicMock, patch

from src.interfaces.dashboard_auth import DASHBOARD_ORIGIN, create_login_url
from src.interfaces.web_server import (
    DashboardHandler,
    build_dashboard_payload,
    get_default_db_path,
)
from src.storage.db import get_connection, init_db
from src.interfaces.cli import seed_accounts


class TestWebServer(unittest.TestCase):
    def setUp(self):
        from src.interfaces.web_server import _INTAKE_RATE_LIMIT, _GLOBAL_INTAKE_HISTORY
        _INTAKE_RATE_LIMIT.clear()
        _GLOBAL_INTAKE_HISTORY.clear()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_ledger.db")
        test_env = patch.dict(os.environ, {
            "LEDGER_DB_PATH": self.db_path,
            "GEMINI_API_KEY": "",
            "GDRIVE_RECEIPTS_FOLDER_ID": "",
            "WEB_ADMIN_TOKEN": "",
        })
        test_env.start()
        self.addCleanup(test_env.stop)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        init_db(conn)
        seed_accounts(conn)
        conn.close()

    def tearDown(self):
        if "LEDGER_DB_PATH" in os.environ:
            del os.environ["LEDGER_DB_PATH"]
        if "WEB_ADMIN_TOKEN" in os.environ:
            del os.environ["WEB_ADMIN_TOKEN"]
        self.tmp_dir.cleanup()

    def test_default_db_path_absolute(self):
        del os.environ["LEDGER_DB_PATH"]
        path = get_default_db_path()
        self.assertTrue(os.path.isabs(path))
        self.assertTrue(path.endswith("data/ledger.db") or path.endswith("data\\ledger.db"))

    def test_build_dashboard_payload(self):
        payload = build_dashboard_payload(self.db_path)
        self.assertIn("receipts", payload)
        self.assertIn("books", payload)
        self.assertIn("mytax", payload)
        self.assertIn("companion", payload)
        self.assertEqual(payload["currency"], "AUD")

    def _dashboard_request(self, method, path, *, headers=None, payload=None):
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = path
        handler.headers = dict(headers or {})
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            handler.headers.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
        handler.rfile = io.BytesIO(body)
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()
        getattr(handler, f"do_{method}")()
        return (
            handler.send_response.call_args.args[0],
            dict(call.args for call in handler.send_header.call_args_list),
            handler.wfile.getvalue(),
        )

    def _dashboard_sign_in(self):
        code = urlparse(create_login_url(self.db_path)).fragment.removeprefix("code=")
        status, headers, _ = self._dashboard_request(
            "POST", "/api/dashboard/session", headers={"Origin": DASHBOARD_ORIGIN}, payload={"code": code}
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("; Secure; HttpOnly; SameSite=Strict", headers["Set-Cookie"])
        return code, headers["Set-Cookie"].split(";", 1)[0]

    def test_dashboard_install_assets_are_public(self):
        assets = {
            "/dashboard.webmanifest": "application/manifest+json",
            "/dashboard-icon-192.png": "image/png",
            "/dashboard-icon-512.png": "image/png",
            "/dashboard-apple-touch-icon.png": "image/png",
            "/dashboard-sw.js": "text/javascript",
        }
        for path, content_type in assets.items():
            with self.subTest(path=path):
                status, headers, body = self._dashboard_request("GET", path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"].split(";")[0], content_type)
                self.assertTrue(body)
                if content_type == "image/png":
                    self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_dashboard_routes_require_owner_session(self):
        for route in ("/api/dashboard", "/api/export.csv", "/api/dashboard/intake", "/api/dashboard/reward"):
            with self.subTest(route=route):
                write = route.endswith(("/intake", "/reward"))
                status, headers, body = self._dashboard_request(
                    "POST" if write else "GET", route, headers={"Origin": DASHBOARD_ORIGIN}
                )
                self.assertEqual(status, 401)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertNotIn(b'"receipts"', body)

        code, cookie = self._dashboard_sign_in()
        status, _, _ = self._dashboard_request(
            "POST", "/api/dashboard/session", headers={"Origin": DASHBOARD_ORIGIN}, payload={"code": code}
        )
        self.assertEqual(status, 401)
        status, _, _ = self._dashboard_request(
            "POST", "/api/dashboard/reward",
            headers={"Cookie": cookie, "Origin": "https://example.test"},
            payload={"reward_name": "[FICTIONAL DEMO] Reward"},
        )
        self.assertEqual(status, 403)
        status, headers, body = self._dashboard_request(
            "POST", "/api/dashboard/reward",
            headers={"Cookie": cookie, "Origin": DASHBOARD_ORIGIN},
            payload={"reward_name": "[FICTIONAL DEMO] Reward"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(body)["companion"]["reward_name"], "[FICTIONAL DEMO] Reward")

        status, headers, _ = self._dashboard_request(
            "DELETE", "/api/dashboard/session", headers={"Cookie": cookie, "Origin": DASHBOARD_ORIGIN}
        )
        self.assertEqual(status, 204)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(
            self._dashboard_request("GET", "/api/dashboard", headers={"Cookie": cookie})[0],
            401,
        )

    def test_dashboard_and_export_use_same_financial_year(self):
        drive_id = "1FictionalDashboardReceipt99"
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.executemany(
                """
                INSERT INTO receipts (
                    vendor, date, total_amount, category, tax_label, status, gdrive_status,
                    gdrive_file_id, items_json
                ) VALUES (?, ?, ?, 'materials_3d_printing', 'Purchases and other costs', ?, ?, ?, ?)
                """,
                (
                    ("=[FICTIONAL DEMO] Formula", "2026-07-01", 55, "recorded", "uploaded", drive_id,
                     '[{"description":"[FICTIONAL DEMO] part","amount":55}]'),
                    ("[FICTIONAL DEMO] Duplicate", "2026-07-02", 55, "duplicate", "not_applicable", None, None),
                    ("[FICTIONAL DEMO] Needs review", "2026-07-03", 22, "needs_review", "not_applicable", None, None),
                    ("[FICTIONAL DEMO] Prior year", "2026-06-30", 11, "recorded", "uploaded", "https://unsafe.test", None),
                ),
            )

        _, cookie = self._dashboard_sign_in()
        headers = {"Cookie": cookie}
        with patch("src.interfaces.web_server._sydney_today", return_value=date(2026, 7, 3)):
            status, _, body = self._dashboard_request("GET", "/api/dashboard?month=2026-07", headers=headers)
        self.assertEqual(status, 200)
        dashboard = json.loads(body)
        self.assertEqual(dashboard["fy"], "FY2026-27")
        self.assertEqual(dashboard["period"], {"start": "2026-07-01", "end": "2027-06-30"})
        self.assertEqual(dashboard["available_fys"], [2026, 2025])
        self.assertEqual(len(dashboard["receipts"]), 3)
        self.assertEqual(dashboard["mytax"]["total_claimable"], 55)
        self.assertEqual(dashboard["mytax"]["pending_review_claimable"], 22)
        self.assertEqual(dashboard["mytax"]["by_mytax_label"][0]["gross_amount"], 55)
        self.assertEqual(dashboard["books"]["spending"]["total_spent"], 55)
        self.assertEqual(dashboard["books"]["spending"]["month"], "2026-07")
        self.assertNotIn("stock", dashboard)
        receipt = dashboard["receipts"][-1]
        self.assertEqual(receipt["items"], [{"description": "[FICTIONAL DEMO] part", "amount": 55}])
        self.assertEqual(receipt["gdrive_url"], f"https://drive.google.com/open?id={drive_id}")

        status, response_headers, body = self._dashboard_request("GET", "/api/export.csv?fy=2026", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers["Cache-Control"], "no-store")
        self.assertIn("FY2026-27", response_headers["Content-Disposition"])
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["Vendor"], "'=[FICTIONAL DEMO] Formula")
        self.assertEqual(rows[-1]["Amount_AUD"], "55.00")
        self.assertEqual(rows[1]["Duplicate"], "True")

        status, _, body = self._dashboard_request("GET", "/api/dashboard?fy=2025", headers=headers)
        self.assertEqual(status, 200)
        prior = json.loads(body)
        self.assertEqual(prior["mytax"]["total_claimable"], 11)
        self.assertIsNone(prior["receipts"][0]["gdrive_url"])
        self.assertEqual(self._dashboard_request("GET", "/api/dashboard?fy=2026&month=2026-06", headers=headers)[0], 400)

    def test_reward_update_validation(self):
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.headers = {}
        handler.path = "/api/reward"

        # Valid update
        import io
        handler.rfile = io.BytesIO(json.dumps({"reward_name": "Test Pizza", "reward_target_xp": 150}).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_POST()
        handler.send_response.assert_called_with(200)

        # Invalid non-numeric XP
        handler.rfile = io.BytesIO(json.dumps({"reward_name": "Test Pizza", "reward_target_xp": "not-a-number"}).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_POST()
        handler.send_response.assert_called_with(400)

    def test_admin_token_auth_protection(self):
        os.environ["WEB_ADMIN_TOKEN"] = "secret_contest_token_123"
        import io
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/reward"
        handler.rfile = io.BytesIO(b'{"reward_name": "Free Lunch"}')
        handler.headers = {"Content-Length": str(len(b'{"reward_name": "Free Lunch"}'))}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        # Missing token -> 401
        handler.do_POST()
        handler.send_response.assert_called_with(401)

        # Correct bearer token -> 200
        handler.rfile = io.BytesIO(b'{"reward_name": "Free Lunch"}')
        handler.headers = {
            "Content-Length": str(len(b'{"reward_name": "Free Lunch"}')),
            "Authorization": "Bearer secret_contest_token_123",
        }
        handler.send_response = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_POST()
        handler.send_response.assert_called_with(200)

    def test_finding_8_tax_cannot_exceed_receipt_total(self):
        """Finding #8: Tax amount cannot exceed the receipt's total amount."""
        import io
        from src.agents.chief_of_staff import ChiefOfStaff
        conn = get_connection(self.db_path)
        init_db(conn)
        seed_accounts(conn)
        cos = ChiefOfStaff(main_conn=conn)
        summary = cos.process_note("[FICTIONAL DEMO] Bunnings $50.00 tools today")
        rec_id = summary.receipt_id
        conn.close()
        self.assertGreater(rec_id, 0)

        handler = DashboardHandler.__new__(DashboardHandler)
        handler.headers = {}
        handler.path = "/api/correct"
        # Attempt to set tax_amount to 60 on a $50 receipt -> 400
        handler.rfile = io.BytesIO(json.dumps({"receipt_id": rec_id, "tax_amount": 60.0}).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_POST()
        handler.send_response.assert_called_with(400)

    def test_finding_16_non_numeric_content_length(self):
        """Finding #16: Non-numeric Content-Length header returns 400."""
        import io
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/reward"
        handler.headers = {"Content-Length": "not-a-number"}
        handler.rfile = io.BytesIO(b'{}')
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_POST()
        handler.send_response.assert_called_with(400)

    def test_finding_1_web_intake_note(self):
        """Finding #1: Fast intake endpoint /api/intake processes expense note."""
        import io
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/intake"
        handler.rfile = io.BytesIO(json.dumps({"text": "[FICTIONAL DEMO] Harbour Cafe $6.50 coffee today"}).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_POST()
        handler.send_response.assert_called_with(200)

        # Empty note text -> 400
        handler.rfile = io.BytesIO(json.dumps({"text": "   "}).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_POST()
        handler.send_response.assert_called_with(400)

        # Length > 300 characters -> 400
        handler.rfile = io.BytesIO(json.dumps({"text": "A" * 301}).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_POST()
        handler.send_response.assert_called_with(400)

    def test_finding_26_xss_protection_in_correct_endpoint(self):
        """Finding #26: /api/correct rejects HTML/script tags in text fields."""
        import io
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/correct"
        handler.rfile = io.BytesIO(json.dumps({
            "receipt_id": 1,
            "vendor": "<script>alert(1)</script>"
        }).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_POST()
        handler.send_response.assert_called_with(400)

    def test_finding_17_19_demo_database_initialization(self):
        """Findings #17 & #19: init_demo_database resets and seeds fixture receipts."""
        from src.interfaces.web_server import init_demo_database
        demo_test_db = os.path.join(self.tmp_dir.name, "demo_test.db")
        with patch.dict(os.environ, {"DEMO_MODE": "1", "LEDGER_DB_PATH": demo_test_db}):
            # First init creates and seeds 12 fictional receipts
            init_demo_database(demo_test_db, reset=True)
            conn = get_connection(demo_test_db)
            c = conn.execute("SELECT COUNT(*) as cnt FROM receipts").fetchone()["cnt"]
            self.assertEqual(c, 12, "Demo database should seed exactly 12 fictional receipts")
            first = conn.execute("SELECT date, tax_amount FROM receipts ORDER BY date LIMIT 1").fetchone()
            self.assertEqual((first["date"], first["tax_amount"]), ("2026-08-12", 4.0))
            conn.close()

            # Second init with reset=True resets back cleanly to 12
            init_demo_database(demo_test_db, reset=True)
            conn = get_connection(demo_test_db)
            c2 = conn.execute("SELECT COUNT(*) as cnt FROM receipts").fetchone()["cnt"]
            self.assertEqual(c2, 12)
            conn.close()

    def test_demo_web_start_preserves_existing_receipt(self):
        from src.interfaces.web_server import run_server

        conn = get_connection(self.db_path)
        try:
            init_db(conn)
            with conn:
                conn.execute(
                    "INSERT INTO receipts (vendor, date, total_amount, category) VALUES (?, ?, ?, ?)",
                    ("[FICTIONAL DEMO] Already Captured Cafe", "2026-08-15", 6.50, "dining_coffee"),
                )
        finally:
            conn.close()

        with (
            patch.dict(os.environ, {"DEMO_MODE": "1", "LEDGER_DB_PATH": self.db_path}),
            patch("src.interfaces.web_server.load_config"),
            patch("src.interfaces.web_server.ThreadingHTTPServer") as server,
        ):
            run_server(port=8080)
            server.return_value.serve_forever.assert_called_once()

        conn = get_connection(self.db_path)
        try:
            rows = conn.execute("SELECT vendor FROM receipts").fetchall()
            self.assertEqual([row["vendor"] for row in rows], ["[FICTIONAL DEMO] Already Captured Cafe"])
        finally:
            conn.close()

    def test_finding_27b_and_33_intake_rate_limiting_with_trust_proxy(self):
        """Finding #27b, #33, #34, #37, #38, #39: /api/intake rate limiting with TRUST_PROXY last XFF entry."""
        import io
        from src.interfaces.web_server import _INTAKE_RATE_LIMIT, _MAX_REQUESTS_PER_WINDOW, _GLOBAL_INTAKE_HISTORY
        _INTAKE_RATE_LIMIT.clear()
        _GLOBAL_INTAKE_HISTORY.clear()

        handler = DashboardHandler.__new__(DashboardHandler)
        handler.client_address = ("127.0.0.1", 54321)  # Reverse proxy address
        handler.path = "/api/intake"

        with patch.dict(os.environ, {"TRUST_PROXY": "1"}):
            # Client A: fake user-supplied header is 1.2.3.4, proxy appends real client IP 203.0.113.1
            handler.headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.1"}
            for i in range(_MAX_REQUESTS_PER_WINDOW):
                handler.rfile = io.BytesIO(json.dumps({"text": f"[FICTIONAL DEMO] Coffee {i} $5.00 today"}).encode("utf-8"))
                handler.headers["Content-Length"] = str(len(handler.rfile.getvalue()))
                handler.send_response = MagicMock()
                handler.send_header = MagicMock()
                handler.end_headers = MagicMock()
                handler.wfile = io.BytesIO()
                handler.do_POST()
                handler.send_response.assert_called_with(200)

            # 31st note for Client A from 203.0.113.1 (even with different fake prefix) -> 429
            handler.headers["X-Forwarded-For"] = "9.9.9.9, 203.0.113.1"
            handler.rfile = io.BytesIO(json.dumps({"text": "[FICTIONAL DEMO] Coffee 31 $5.00 today"}).encode("utf-8"))
            handler.headers["Content-Length"] = str(len(handler.rfile.getvalue()))
            handler.send_response = MagicMock()
            handler.wfile = io.BytesIO()
            handler.do_POST()
            handler.send_response.assert_called_with(429)

            # Client B from different real IP 203.0.113.2 gets their own fresh budget (200 OK)
            handler.headers = {"X-Forwarded-For": "203.0.113.2"}
            handler.rfile = io.BytesIO(json.dumps({"text": "[FICTIONAL DEMO] Client B Coffee $5.00 today"}).encode("utf-8"))
            handler.headers["Content-Length"] = str(len(handler.rfile.getvalue()))
            handler.send_response = MagicMock()
            handler.wfile = io.BytesIO()
            handler.do_POST()
            handler.send_response.assert_called_with(200)

    def test_finding_41_global_intake_rate_limit(self):
        """Finding #41: Global intake cap (300/hr) triggers 429 across all IPs."""
        import io, time
        from src.interfaces.web_server import _GLOBAL_INTAKE_HISTORY, _INTAKE_RATE_LIMIT
        _INTAKE_RATE_LIMIT.clear()
        now = time.time()
        # Pre-fill global history with 300 timestamps
        _GLOBAL_INTAKE_HISTORY.clear()
        _GLOBAL_INTAKE_HISTORY.extend([now - 10.0] * 300)

        handler = DashboardHandler.__new__(DashboardHandler)
        handler.client_address = ("198.51.100.5", 12345)
        handler.path = "/api/intake"
        handler.headers = {"Content-Length": "30"}
        handler.rfile = io.BytesIO(json.dumps({"text": "Global test $5"}).encode("utf-8"))
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_POST()
        handler.send_response.assert_called_with(429)
        handler.send_header.assert_any_call("Retry-After", "300")

    def test_finding_1_web_intake_file_upload(self):
        """[FICTIONAL DEMO] Web uploads use RAM and disclose the archive link only to the owner."""
        import base64, io
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/intake"
        drive_id = "1FictionalArchivedReceipt987"
        raw_bytes = b"\xff\xd8\xff\xe0[FICTIONAL DEMO] receipt bytes"
        request = json.dumps({
            "file_data": base64.b64encode(raw_bytes).decode("utf-8"),
            "filename": r"../outside\receipt_test.jpg",
        }).encode("utf-8")
        for token in ("", "fictional-admin-token"):
            with (
                self.subTest(authenticated=bool(token)),
                patch.dict(os.environ, {"WEB_ADMIN_TOKEN": token}),
                patch("src.interfaces.web_server.ChiefOfStaff") as cos,
            ):
                cos.return_value.process_file.return_value = MagicMock(
                    receipt_id=99,
                    summary_text="Processed receipt photo",
                    status="recorded",
                    gdrive_status="uploaded",
                    gdrive_file_id=drive_id,
                )
                handler.rfile = io.BytesIO(request)
                handler.headers = {"Content-Length": str(len(request))}
                if token:
                    handler.headers["Authorization"] = f"Bearer {token}"
                handler.send_response = MagicMock()
                handler.send_header = MagicMock()
                handler.end_headers = MagicMock()
                handler.wfile = io.BytesIO()

                handler.do_POST()

                handler.send_response.assert_called_with(200)
                cos.return_value.process_file.assert_called_once_with(
                    "receipt_test.jpg", user_note=None, file_bytes=raw_bytes
                )
                summary = json.loads(handler.wfile.getvalue())["intake_summary"]
                self.assertIsNone(summary["local_path"])
                if token:
                    self.assertEqual(summary["gdrive_url"], f"https://drive.google.com/open?id={drive_id}")
                else:
                    self.assertNotIn("gdrive_url", summary)
                    self.assertNotIn(drive_id.encode(), handler.wfile.getvalue())
                self.assertFalse(os.path.exists(os.path.join(self.tmp_dir.name, "receipts")))

        # Reject payload without text or file_data -> 400
        handler.rfile = io.BytesIO(json.dumps({}).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_POST()
        handler.send_response.assert_called_with(400)

    def test_finding_file_upload_validation_and_zero_byte_bypass(self):
        """
        Finding #2: file_data=!!!! and filename=x.exe must be rejected with 400.
        Strict base64 decoding, 0-byte check, allowed extensions, and magic byte verification.
        """
        import base64, io
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/intake"

        # 1. Invalid base64 "!!!!" with executable extension "x.exe" -> 400
        handler.rfile = io.BytesIO(json.dumps({
            "file_data": "!!!!",
            "filename": "x.exe"
        }).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_POST()
        handler.send_response.assert_called_with(400)

        # 2. Valid base64 but 0 bytes -> 400
        empty_b64 = base64.b64encode(b"").decode("utf-8")
        handler.rfile = io.BytesIO(json.dumps({
            "file_data": empty_b64,
            "filename": "receipt.jpg"
        }).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_POST()
        handler.send_response.assert_called_with(400)

        # 3. Disallowed extension (.exe) with valid bytes -> 400
        valid_bytes = base64.b64encode(b"\xff\xd8\xff\xe0fake_jpeg").decode("utf-8")
        handler.rfile = io.BytesIO(json.dumps({
            "file_data": valid_bytes,
            "filename": "invoice.exe"
        }).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_POST()
        handler.send_response.assert_called_with(400)

        # 4. Extension is .jpg but signature mismatch (plain text bytes) -> 400
        text_b64 = base64.b64encode(b"This is just plain text, not a JPEG").decode("utf-8")
        handler.rfile = io.BytesIO(json.dumps({
            "file_data": text_b64,
            "filename": "receipt.jpg"
        }).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_POST()
        handler.send_response.assert_called_with(400)

    def test_finding_request_body_pre_read_size_bound_413(self):
        """Finding #3: Pre-read size limit rejects requests exceeding 10 MB with HTTP 413."""
        import io
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/intake"
        # Content-Length exceeds 10 MB limit (10 * 1024 * 1024 + 100 bytes)
        handler.headers = {"Content-Length": str(10 * 1024 * 1024 + 100)}
        handler.rfile = MagicMock()  # If read is called, it would fail or consume memory
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler.do_POST()
        handler.send_response.assert_called_with(413)
        handler.rfile.read.assert_not_called()  # Verifies pre-read check prevented allocation

    def test_web_upload_cannot_post_or_save_locally_when_drive_is_unavailable(self):
        """[FICTIONAL DEMO] Archive failure produces a retryable error and leaves the ledger empty."""
        import base64, io
        from src.agents.chief_of_staff import ChiefOfStaff

        drive = MagicMock()
        drive.upload_receipt_bytes.return_value = None
        drive.last_status = "not_configured"
        finance = MagicMock()
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/intake"
        raw_bytes = b"\xff\xd8\xff\xe0[FICTIONAL DEMO] receipt bytes"
        request = json.dumps({
            "file_data": base64.b64encode(raw_bytes).decode("utf-8"),
            "filename": "receipt.jpg",
        }).encode("utf-8")
        handler.rfile = io.BytesIO(request)
        handler.headers = {"Content-Length": str(len(request))}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        with patch(
            "src.interfaces.web_server.ChiefOfStaff",
            side_effect=lambda main_conn: ChiefOfStaff(main_conn, gdrive=drive, finance_agent=finance),
        ):
            handler.do_POST()

        handler.send_response.assert_called_with(503)
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(payload["status"], "archive_unavailable")
        self.assertEqual(payload["receipt_id"], 0)
        self.assertIsNone(payload["local_path"])
        self.assertIn("Keep your copy", payload["summary_text"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir.name, "receipts")))
        self.assertEqual(drive.upload_receipt_bytes.call_args.args, (raw_bytes, "receipt.jpg"))
        finance.parse_document.assert_not_called()
        conn = get_connection(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM journal_transactions").fetchone()[0], 0)
        finally:
            conn.close()

    def test_finding_accompanying_note_preserved_and_passed_with_file(self):
        """
        UI Issue A: Accompanying text for image is passed to parse_document and persisted in receipts.notes.
        """
        import base64, io
        from unittest.mock import patch
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/intake"

        with patch("src.interfaces.web_server.ChiefOfStaff") as MockCOS:
            mock_inst = MagicMock()
            mock_inst.process_file.return_value = MagicMock(
                receipt_id=101,
                summary_text="Processed with note",
                status="recorded",
                gdrive_status="uploaded",
                local_path=None,
            )
            MockCOS.return_value = mock_inst

            raw_bytes = b"\xff\xd8\xff\xe0[FICTIONAL DEMO] image data"
            valid_jpg = base64.b64encode(raw_bytes).decode("utf-8")
            note = "[FICTIONAL DEMO] Nozzles for the 3D printer"
            handler.rfile = io.BytesIO(json.dumps({
                "file_data": valid_jpg,
                "filename": "tools.jpg",
                "text": note
            }).encode("utf-8"))
            handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()

            handler.do_POST()
            handler.send_response.assert_called_with(200)
            mock_inst.process_file.assert_called_once_with("tools.jpg", user_note=note, file_bytes=raw_bytes)

    def test_unposted_uploads_return_their_own_status_and_keep_retry_inputs(self):
        """[FICTIONAL DEMO] A held or duplicate upload cannot look like an OCR outage."""
        import base64, io

        outcomes = (
            ("extraction_unavailable", "Extraction unavailable", "Could not read the photo."),
            ("needs_review", "Receipt needs review before it can be recorded", "Please confirm the date."),
            ("duplicate", "This receipt has already been uploaded", "The original upload was retained."),
        )
        valid_jpg = base64.b64encode(b"\xff\xd8\xff\xe0[FICTIONAL DEMO]").decode("utf-8")
        request_body = json.dumps({"file_data": valid_jpg, "filename": "fictional_receipt.jpg"}).encode("utf-8")
        for status, error, summary in outcomes:
            with self.subTest(status=status), patch("src.interfaces.web_server.ChiefOfStaff") as mock_cos:
                mock_cos.return_value.process_file.return_value = MagicMock(
                    receipt_id=0,
                    summary_text=summary,
                    status=status,
                    gdrive_status="not_configured",
                    local_path=None,
                )
                handler = DashboardHandler.__new__(DashboardHandler)
                handler.path = "/api/intake"
                handler.rfile = io.BytesIO(request_body)
                handler.headers = {"Content-Length": str(len(request_body))}
                handler.send_response = MagicMock()
                handler.send_header = MagicMock()
                handler.end_headers = MagicMock()
                handler.wfile = io.BytesIO()

                handler.do_POST()

                handler.send_response.assert_called_with(422)
                self.assertEqual(json.loads(handler.wfile.getvalue()), {
                    "error": error,
                    "status": status,
                    "summary_text": summary,
                    "receipt_id": 0,
                    "local_path": None,
                    "gdrive_status": "not_configured",
                })


if __name__ == "__main__":
    unittest.main()
