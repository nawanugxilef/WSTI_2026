import sqlite3
import unittest
from src.storage.db import init_db
from src.storage.memory import VendorMemoryManager


class TestVendorMemory(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.vm = VendorMemoryManager(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_vendor_learning(self):
        # 1. Initially unknown fictional vendor
        rule = self.vm.lookup("Fictional 3D Maker Supplies AU")
        self.assertIsNone(rule)

        # 2. Record correction for 3D printing ABN business category
        self.vm.record_correction(
            raw_vendor="Fictional 3D Maker Supplies AU",
            category="materials_3d_printing",
            purpose="business",
            tax_label="Cost of sales",
        )

        # 3. Lookup should now match with confidence 1.0
        rule = self.vm.lookup("fictional 3d maker supplies")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.default_category, "materials_3d_printing")
        self.assertEqual(rule.default_purpose, "business")
        self.assertEqual(rule.confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
