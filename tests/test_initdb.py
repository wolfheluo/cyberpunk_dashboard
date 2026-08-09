#!/usr/bin/env python3
"""D4 (C-4/N-26): init_db timestamp conversion + completeness check.

Seams: the kline CSV parse is a pure function (millisecond openTime -> date
string) and the skip decision is a pure predicate over the DB — both tested
with known-good literals (independent source of truth: Unix epoch constants).
"""
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmp = tempfile.TemporaryDirectory()
os.environ["QF_DB_PATH"] = os.path.join(_tmp.name, "t.db")

import init_db  # noqa: E402  (must import after QF_DB_PATH is set)


class ParseKlinesTests(unittest.TestCase):
    """Known-good epoch literals (independent source of truth, not recomputed)."""

    def test_ms_timestamp_converts_to_real_date(self):
        # 1735689600000 ms = 2025-01-01T00:00:00Z (known Unix epoch literal)
        rows = [["1735689600000", "1", "2", "3", "4", "5", "6"]]
        out = init_db._parse_klines_csv("BTCUSDT", rows)
        self.assertEqual(out[0][1], "2025-01-01")
        # 1767225600000 ms = 2026-01-01T00:00:00Z
        rows2 = [["1767225600000", "1", "2", "3", "4", "5", "6"]]
        out2 = init_db._parse_klines_csv("BTCUSDT", rows2)
        self.assertEqual(out2[0][1], "2026-01-01")

    def test_old_bug_would_produce_1970(self):
        # The previous `// 1_000_000` bug turned 1735689600000 into
        # 1735689 seconds = 1970-01-21 — assert we are NOT there.
        rows = [["1735689600000", "1", "2", "3", "4", "5", "6"]]
        out = init_db._parse_klines_csv("BTCUSDT", rows)
        self.assertNotEqual(out[0][1], "1970-01-21")
        self.assertTrue(out[0][1].startswith("2025"))

    def test_ohlcv_columns_preserved(self):
        rows = [["1735689600000", "100.5", "110.25", "99.1", "105.0", "1234.5", "0"]]
        out = init_db._parse_klines_csv("BTCUSDT", rows)
        self.assertEqual(out[0][2:], (100.5, 110.25, 99.1, 105.0, 1234.5))


class CompletenessTests(unittest.TestCase):
    """N-26: skip only when data actually reaches the expected range."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE historical_klines (symbol TEXT, date TEXT, open REAL,"
            " high REAL, low REAL, close REAL, volume REAL)")

    def tearDown(self):
        self.conn.close()

    def _fill(self, symbol, dates):
        self.conn.executemany(
            "INSERT INTO historical_klines VALUES (?,?,1,2,3,4,5)",
            [(symbol, d) for d in dates])

    def test_many_old_rows_but_incomplete_is_not_complete(self):
        # >300 rows ending in 2025-01 must NOT be considered complete
        dates = ["2025-01-%02d" % (d % 28 + 1) for d in range(400)]
        self._fill("BTCUSDT", dates)
        self.assertFalse(init_db._is_complete(self.conn, "BTCUSDT"))

    def test_reaching_expected_range_is_complete(self):
        self._fill("BTCUSDT", ["2026-06-28"])
        self.assertTrue(init_db._is_complete(self.conn, "BTCUSDT"))

    def test_empty_db_is_not_complete(self):
        self.assertFalse(init_db._is_complete(self.conn, "BTCUSDT"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
