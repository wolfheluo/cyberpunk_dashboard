#!/usr/bin/env python3
"""Module-level fake tests for Quant Fleet server internals.

Seam (pre-agreed in spec issue #1): import the server module against a temp
DB (QF_DB_PATH set before import), monkeypatch the Binance dependencies
(fetch_json / fetch_klines_cached / fetch_book_cached / run_js_strategy) and
observe public behaviour (return values, DB state) — never implementation
internals.

Convention (from the remediation batches): monkeypatched module functions are
captured and restored in setUp/tearDown so no fake leaks into later test
classes (classes run in definition order).
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmp = tempfile.TemporaryDirectory()
os.environ["QF_DB_PATH"] = os.path.join(_tmp.name, "t.db")

import quant_fleet_server as srv  # noqa: E402


def _fake_klines(symbol, interval, limit=100, ttl=60):
    """Synthetic 1h klines: open=100, high=101, low=99, close=100, vol=1e6."""
    base = 1_700_000_000_000
    return [[base + i * 3_600_000, "100", "101", "99", "100", "1000000",
             base + i * 3_600_000 + 1] for i in range(limit)]


def _fake_fetch_json(url):
    if "bookTicker" in url:
        return [{"symbol": "BTCUSDT", "bidPrice": "99.9", "askPrice": "100.1",
                 "bidQty": "1", "askQty": "2"}]
    if "24hr" in url:
        return [{"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "1",
                 "quoteVolume": "1000000", "highPrice": "101", "lowPrice": "99"}]
    return None


class FakeSeamSmokeTests(unittest.TestCase):
    """T-00: the module-fake seam works — fakes installed, behaviour observed."""

    @classmethod
    def setUpClass(cls):
        srv._orig = (srv.fetch_json, srv.fetch_klines_cached)

    @classmethod
    def tearDownClass(cls):
        srv.fetch_json, srv.fetch_klines_cached = srv._orig

    def setUp(self):
        srv.fetch_json = _fake_fetch_json
        srv.fetch_klines_cached = _fake_klines
        # fetch_book_cached() internally calls fetch_json -> fake bookTicker

    def test_fake_book_mid_price(self):
        # bookTicker fake: BTCUSDT bid 99.9 / ask 100.1 -> mid 100.0
        raw = srv.fetch_book_cached()
        self.assertEqual(raw[0]["symbol"], "BTCUSDT")
        mid = (float(raw[0]["bidPrice"]) + float(raw[0]["askPrice"])) / 2
        self.assertEqual(mid, 100.0)

    def test_fake_ticker24_last_price(self):
        raw = srv.fetch_ticker24_cached()
        self.assertEqual(float(raw[0]["lastPrice"]), 100.0)

    def test_position_value_now_returns_zero_with_no_positions(self):
        # empty positions -> valuation is 0 regardless of fakes
        with srv.db_lock:
            srv.get_db().execute("DELETE FROM positions")
            srv.get_db().commit()
        self.assertEqual(srv._position_value_now(), 0.0)

    def test_fetch_all_data_builds_ticker_row(self):
        srv.active_strategy = ""
        srv._last_signal = {}
        d = srv.fetch_all_data()
        self.assertIsNotNone(d)
        ids = [t["id"] for t in d["tickers"]]
        self.assertIn("BTC", ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
