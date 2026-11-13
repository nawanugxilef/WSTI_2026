import datetime
import sqlite3
import unittest

from src.agents.wellbeing import COFFEE_CHECK_IN, coffee_check_in

TODAY = datetime.date(2026, 11, 13)


class CoffeeCheckInTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE receipts (id INTEGER PRIMARY KEY, vendor TEXT, date TEXT, items_json TEXT, status TEXT)"
        )

    def add(self, vendor, date=TODAY, status="recorded", items=None):
        cur = self.conn.execute(
            "INSERT INTO receipts (vendor, date, items_json, status) VALUES (?, ?, ?, ?)",
            (vendor, date.isoformat(), items, status),
        )
        return cur.lastrowid

    def test_third_coffee_today_gets_one_check_in(self):
        self.assertIsNone(coffee_check_in(self.conn, self.add("[FICTIONAL DEMO] Harbour Cafe"), TODAY))
        self.add("[FICTIONAL DEMO] Bunnings hammer")
        self.assertIsNone(coffee_check_in(self.conn, self.add("flat white"), TODAY))
        third = self.add("[FICTIONAL DEMO] Corner shop", items='[{"description": "Iced latte"}]')
        self.assertEqual(coffee_check_in(self.conn, third, TODAY), COFFEE_CHECK_IN)
        self.assertIsNone(coffee_check_in(self.conn, self.add("kopi susu"), TODAY))

    def test_catching_up_old_coffees_and_duplicates_never_trigger(self):
        yesterday = TODAY - datetime.timedelta(days=1)
        for _ in range(3):
            last = self.add("coffee", date=yesterday)
        self.assertIsNone(coffee_check_in(self.conn, last, TODAY))
        self.add("coffee")
        self.add("coffee", status="duplicate")
        self.assertIsNone(coffee_check_in(self.conn, self.add("coffee"), TODAY))


if __name__ == "__main__":
    unittest.main()
