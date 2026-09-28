import sqlite3
import unittest

import server
from core import ValidationError


class SplitImportBodyTests(unittest.TestCase):
    def test_wrapper_with_mode_and_payload(self):
        payload = {"trades": []}
        self.assertEqual(
            server.split_import_body({"mode": "replace", "payload": payload}),
            ("replace", payload),
        )

    def test_payload_without_mode_defaults_to_append(self):
        payload = [{"code": "A"}]
        self.assertEqual(server.split_import_body({"payload": payload}), ("append", payload))

    def test_bare_export_body_is_the_payload(self):
        payload = {"trades": [{"code": "A"}]}
        self.assertEqual(server.split_import_body(payload), ("append", payload))

    def test_bare_list_is_the_payload(self):
        rows = [{"code": "A"}]
        self.assertEqual(server.split_import_body(rows), ("append", rows))

    def test_blank_mode_defaults_to_append(self):
        payload = {"trades": []}
        self.assertEqual(
            server.split_import_body({"mode": "  ", "payload": payload}), ("append", payload)
        )

    def test_mode_is_trimmed(self):
        self.assertEqual(
            server.split_import_body({"mode": " replace ", "payload": {"trades": []}})[0],
            "replace",
        )


class ImportTradesTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(server.SCHEMA)
        self.addCleanup(self.conn.close)
        self.records = [
            {"code": "A", "name": "甲", "side": "buy",
             "trade_date": "2024-01-01", "price": 10.0, "note": ""},
            {"code": "A", "name": "甲", "side": "sell",
             "trade_date": "2024-02-01", "price": 12.0, "note": ""},
        ]

    def rows(self):
        return [dict(row) for row in
                self.conn.execute("SELECT code, side, seq FROM trades ORDER BY id")]

    def test_append_keeps_existing_rows_and_continues_seq(self):
        server.import_trades(self.conn, self.records[:1], "append")
        server.import_trades(self.conn, self.records[1:], "append")
        rows = self.rows()
        self.assertEqual([r["seq"] for r in rows], [1, 2])
        self.assertEqual([r["side"] for r in rows], ["buy", "sell"])

    def test_replace_clears_rows_and_restarts_seq(self):
        server.import_trades(self.conn, self.records, "append")
        server.import_trades(self.conn, self.records[:1], "replace")
        self.assertEqual(self.rows(), [{"code": "A", "side": "buy", "seq": 1}])


class DateWarningTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(server.SCHEMA)
        self.addCleanup(self.conn.close)
        server.import_trades(self.conn, [
            {"code": "A", "name": "甲", "side": "buy",
             "trade_date": "2024-02-01", "price": 10.0, "note": ""},
        ], "append")

    def test_buy_is_never_warned(self):
        self.assertIsNone(server.date_warning(self.conn, "A", "buy", "2020-01-01"))

    def test_sell_before_first_buy_is_warned(self):
        warning = server.date_warning(self.conn, "A", "sell", "2024-01-01")
        self.assertIn("2024-02-01", warning)

    def test_sell_on_or_after_first_buy_is_ok(self):
        self.assertIsNone(server.date_warning(self.conn, "A", "sell", "2024-03-01"))

    def test_stock_without_any_buy_is_warned(self):
        warning = server.date_warning(self.conn, "B", "sell", "2024-03-01")
        self.assertIn("尚无买入记录", warning)


if __name__ == "__main__":
    unittest.main(verbosity=2)
