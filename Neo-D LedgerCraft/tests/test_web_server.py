import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.interfaces.web_server import (
    DashboardHandler,
    build_dashboard_payload,
    get_default_db_path,
)
from src.storage.db import get_connection, init_db
from src.interfaces.cli import seed_accounts


class TestWebServer(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_ledger.db")
        os.environ["LEDGER_DB_PATH"] = self.db_path

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
        summary = cos.process_note("Bunnings $50.00 tools")
        rec_id = summary.receipt_id
        conn.close()

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
        handler.rfile = io.BytesIO(json.dumps({"text": "Harbour Cafe $6.50 coffee"}).encode("utf-8"))
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
            conn.close()

            # Second init with reset=True resets back cleanly to 12
            init_demo_database(demo_test_db, reset=True)
            conn = get_connection(demo_test_db)
            c2 = conn.execute("SELECT COUNT(*) as cnt FROM receipts").fetchone()["cnt"]
            self.assertEqual(c2, 12)
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
                handler.rfile = io.BytesIO(json.dumps({"text": f"Coffee {i} $5.00"}).encode("utf-8"))
                handler.headers["Content-Length"] = str(len(handler.rfile.getvalue()))
                handler.send_response = MagicMock()
                handler.send_header = MagicMock()
                handler.end_headers = MagicMock()
                handler.wfile = io.BytesIO()
                handler.do_POST()
                handler.send_response.assert_called_with(200)

            # 31st note for Client A from 203.0.113.1 (even with different fake prefix) -> 429
            handler.headers["X-Forwarded-For"] = "9.9.9.9, 203.0.113.1"
            handler.rfile = io.BytesIO(json.dumps({"text": "Coffee 31 $5.00"}).encode("utf-8"))
            handler.headers["Content-Length"] = str(len(handler.rfile.getvalue()))
            handler.send_response = MagicMock()
            handler.wfile = io.BytesIO()
            handler.do_POST()
            handler.send_response.assert_called_with(429)

            # Client B from different real IP 203.0.113.2 gets their own fresh budget (200 OK)
            handler.headers = {"X-Forwarded-For": "203.0.113.2"}
            handler.rfile = io.BytesIO(json.dumps({"text": "Client B Coffee $5.00"}).encode("utf-8"))
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
        """Finding #1: Fast intake endpoint /api/intake processes base64 photo/PDF."""
        import base64, io
        from unittest.mock import patch
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = "/api/intake"

        # Mock ChiefOfStaff.process_file
        with patch("src.interfaces.web_server.ChiefOfStaff") as MockCOS:
            mock_inst = MagicMock()
            mock_inst.process_file.return_value = MagicMock(
                receipt_id=99,
                summary_text="Processed receipt photo",
                status="recorded",
                gdrive_status="uploaded",
            )
            MockCOS.return_value = mock_inst

            sample_b64 = base64.b64encode(b"dummy image bytes").decode("utf-8")
            handler.rfile = io.BytesIO(json.dumps({
                "file_data": sample_b64,
                "filename": "receipt_test.jpg"
            }).encode("utf-8"))
            handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()

            handler.do_POST()
            handler.send_response.assert_called_with(200)
            mock_inst.process_file.assert_called_once()

        # Reject payload without text or file_data -> 400
        handler.rfile = io.BytesIO(json.dumps({}).encode("utf-8"))
        handler.headers = {"Content-Length": str(len(handler.rfile.getvalue()))}
        handler.send_response = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_POST()
        handler.send_response.assert_called_with(400)


if __name__ == "__main__":
    unittest.main()
