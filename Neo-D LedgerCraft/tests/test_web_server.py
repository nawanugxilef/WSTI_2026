import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock

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


if __name__ == "__main__":
    unittest.main()
