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


class PositionValueTests(unittest.TestCase):
    """T-02 (M-1): _position_value_now() must look up live prices by the FULL
    symbol (USDT suffix). positions.symbol stores 'BTC'; the ticker24 price_map
    is keyed 'BTCUSDT'. The bug dropped the suffix -> always fell back to
    entry_price (200 instead of 240 for 2 BTC @ entry 100, price 120)."""

    def setUp(self):
        srv._ticker24_cache = None
        srv._ticker24_ts = 0.0
        with srv.db_lock:
            db = srv.get_db()
            db.execute("DELETE FROM trades")
            db.execute("DELETE FROM positions")
            db.execute("DELETE FROM signals")
            db.execute("UPDATE portfolio SET cash=10000 WHERE id=1")
            db.execute("INSERT INTO positions (symbol,side,entry_price,quantity,"
                       "current_price,unrealized_pnl,strategy) "
                       "VALUES ('BTC','BUY',100,2,120,40,'t')")
            db.commit()
        self._saved = srv.fetch_json
        srv.fetch_json = lambda url: (
            [{"symbol": "BTCUSDT", "lastPrice": "120", "priceChangePercent": "1",
              "quoteVolume": "1000000", "highPrice": "121", "lowPrice": "99"}]
            if "24hr" in url else None)

    def tearDown(self):
        srv.fetch_json = self._saved
        srv._ticker24_cache = None
        srv._ticker24_ts = 0.0
        with srv.db_lock:
            db = srv.get_db()
            db.execute("DELETE FROM trades")
            db.execute("DELETE FROM positions")
            db.execute("DELETE FROM signals")
            db.execute("UPDATE portfolio SET cash=10000 WHERE id=1")
            db.commit()

    def test_position_value_uses_live_price(self):
        # 2 BTC @ live 120 -> 240 (the bug returned 200 = entry-price fallback)
        self.assertEqual(srv._position_value_now(), 240.0)

    def test_position_value_falls_back_when_ticker_missing(self):
        srv.fetch_json = lambda url: None  # Binance down -> no prices
        srv._ticker24_cache = None
        srv._ticker24_ts = 0.0
        # no live price -> entry-price fallback (2 * 100 = 200), no crash
        self.assertEqual(srv._position_value_now(), 200.0)


class VolSurgeTests(unittest.TestCase):
    """T-04 (M-2): vol_surge must compare the LAST 1h kline volume against the
    previous 10 bars' average — the bug compared the 24h quoteVolume (ticker24)
    against the 10-bar hourly average, which is ~always true (24h >> 1h)."""

    def setUp(self):
        srv.active_strategy = ""
        srv._last_signal = {}
        srv._ticker24_cache = None
        srv._ticker24_ts = 0.0
        srv._klines_cache = {}
        srv._klines_cache_ts = {}
        srv._book_cache = None
        srv._book_cache_ts = 0.0
        with srv.db_lock:
            db = srv.get_db()
            db.execute("DELETE FROM trades")
            db.execute("DELETE FROM positions")
            db.execute("DELETE FROM signals")
            db.commit()
        self._saved = (srv.fetch_json, srv.fetch_klines_cached)

    def tearDown(self):
        srv.fetch_json, srv.fetch_klines_cached = self._saved

    def _vols(self, vols):
        # 11 bars: first 10 = 1e6 volume, last = vols[-1]
        base = 1_700_000_000_000
        klines = [[base + i * 3_600_000, "100", "101", "99", "100", str(v), 0]
                  for i, v in enumerate(vols)]
        srv.fetch_klines_cached = lambda symbol, interval, limit=100, ttl=60: klines
        srv.fetch_json = lambda url: (
            [{"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "1",
              "quoteVolume": "1000000", "highPrice": "101", "lowPrice": "99"}]
            if "24hr" in url else
            [{"symbol": "BTCUSDT", "bidPrice": "99.9", "askPrice": "100.1",
              "bidQty": "1", "askQty": "2"}] if "bookTicker" in url else None)
        d = srv.fetch_all_data()
        btc = next(t for t in d["tickers"] if t["id"] == "BTC")
        return btc["_vol_surge"]

    def test_last_bar_spike_is_surge(self):
        # last bar 10x the previous average -> true surge
        self.assertTrue(self._vols([1e6] * 10 + [1e7]))

    def test_flat_volume_is_not_surge(self):
        # flat 1e6 bars -> no surge (buggy 24h-vs-1h comparison was ~always true)
        self.assertFalse(self._vols([1e6] * 11))

    def test_insufficient_data_is_not_surge(self):
        srv.fetch_klines_cached = lambda symbol, interval, limit=100, ttl=60: (
            [[1_700_000_000_000 + i * 3_600_000, "100", "101", "99", "100", "1000000", 0]
             for i in range(2)])
        d = srv.fetch_all_data()
        self.assertFalse(any(t["_vol_surge"] for t in d["tickers"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
