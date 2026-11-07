import sqlite3
import unittest
from src.agents.chief_of_staff import ChiefOfStaff
from src.storage.db import init_db
from src.storage.journal import JournalManager


class TestChiefOfStaffPipeline(unittest.TestCase):
    def setUp(self):
        self.main_conn = sqlite3.connect(":memory:")
        self.main_conn.row_factory = sqlite3.Row
        init_db(self.main_conn)

        # Seed standard accounts (including 3D Printing & Dog Walking ABN accounts)
        with self.main_conn:
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('1000', 'Cash in Bank - Everyday', 'bank')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('4000', '3D Printing Sales', 'business_revenue')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('4100', 'Dog Walking Services Revenue', 'business_revenue')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('5100', 'Materials & Consumables', 'expense')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('5400', 'Dog Walking Supplies', 'expense')")
            self.main_conn.execute("INSERT INTO accounts (code, name, type) VALUES ('5900', 'Personal Living Expenses', 'personal_drawings')")

        self.cos = ChiefOfStaff(main_conn=self.main_conn)
        self.jm = JournalManager(self.main_conn)

    def tearDown(self):
        self.main_conn.close()

    def test_pipeline_personal_note(self):
        summary = self.cos.process_note("Fictional Demo Cafe coffee 6.50 today")
        self.assertIn("6.50", summary.summary_text)
        self.assertEqual(summary.purpose, "personal")

        tb = self.jm.get_trial_balance()
        tot_deb = sum(r["total_debit"] for r in tb)
        tot_crd = sum(r["total_credit"] for r in tb)
        self.assertEqual(tot_deb, tot_crd)
        self.assertEqual(tot_deb, 6.50)

    def test_pipeline_business_3d_printing_note(self):
        summary = self.cos.process_note("Fictional 3D Maker Supply 3D print nozzle kit 32.99")
        self.assertIn("32.99", summary.summary_text)
        self.assertEqual(summary.purpose, "business")
        self.assertEqual(summary.category, "materials_3d_printing")

        tb = self.jm.get_trial_balance()
        tot_deb = sum(r["total_debit"] for r in tb)
        tot_crd = sum(r["total_credit"] for r in tb)
        self.assertEqual(tot_deb, tot_crd)
        self.assertEqual(tot_deb, 32.99)


if __name__ == "__main__":
    unittest.main()
