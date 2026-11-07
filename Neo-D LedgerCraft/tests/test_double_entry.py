import sqlite3
import unittest
from src.storage.db import init_db
from src.storage.journal import JournalError, JournalLeg, JournalManager


class TestDoubleEntry(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        with self.conn:
            self.conn.execute("INSERT INTO accounts (code, name, type) VALUES ('1000', 'Cash', 'asset')")
            self.conn.execute("INSERT INTO accounts (code, name, type) VALUES ('5100', 'Expenses', 'expense')")
        self.jm = JournalManager(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_balanced_transaction_posts_successfully(self):
        legs = [
            JournalLeg(account_code="5100", debit=50.0, credit=0.0),
            JournalLeg(account_code="1000", debit=0.0, credit=50.0),
        ]
        tx_id = self.jm.post_transaction("2026-10-15", "Test purchase", legs)
        self.assertGreater(tx_id, 0)

        tb = self.jm.get_trial_balance()
        total_debit = sum(r["total_debit"] for r in tb)
        total_credit = sum(r["total_credit"] for r in tb)
        self.assertEqual(total_debit, total_credit)
        self.assertEqual(total_debit, 50.0)

    def test_unbalanced_transaction_raises_error(self):
        legs = [
            JournalLeg(account_code="5100", debit=50.0, credit=0.0),
            JournalLeg(account_code="1000", debit=0.0, credit=45.0), # Unbalanced!
        ]
        with self.assertRaises(JournalError):
            self.jm.post_transaction("2026-10-15", "Unbalanced", legs)


if __name__ == "__main__":
    unittest.main()
