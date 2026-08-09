#!/usr/bin/env python3
"""M-1 (D7): SELL open/add must forward size_pct like the BUY path does.

Seam: module-level monkeypatch of the network + strategy boundaries, then
observe what execute_trade receives (public trade-execution contract).
"""
import os
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmp = tempfile.TemporaryDirectory()
os.environ["QF_DB_PATH"] = os.path.join(_tmp.name, "t.db")

import quant_fleet_server as srv  # noqa: E402


def _fake_klines(symbol, interval, limit=100, ttl=60):
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


class SellSizePctTests(unittest.TestCase):
    """SELL open short must receive size_pct from the strategy signal."""

    @classmethod
    def setUpClass(cls):
        with srv.db_lock:
            srv.db_conn.execute(
                "INSERT OR IGNORE INTO watchlist (symbol,name) VALUES ('BTCUSDT','Bitcoin')")
            srv.db_conn.commit()
        srv.reload_symbols()
        srv._orig = (srv.fetch_json, srv.fetch_klines_cached, srv.run_js_strategy, srv.execute_trade)

    @classmethod
    def tearDownClass(cls):
        srv.fetch_json, srv.fetch_klines_cached, srv.run_js_strategy, srv.execute_trade = srv._orig

    def setUp(self):
        self.calls = []
        srv.active_strategy = "fake.js"
        srv._last_signal = {}
        srv.fetch_json = _fake_fetch_json
        srv.fetch_klines_cached = _fake_klines
        srv.run_js_strategy = lambda *a, **k: {"BTC": {"signal": "SELL", "size_pct": 0.1}}

        def spy(symbol, side, price, strategy_name, signal_id=None, close_pct=1.0, size_pct=None):
            self.calls.append({"symbol": symbol, "side": side, "size_pct": size_pct,
                               "close_pct": close_pct})
            return {"status": "filled"}
        srv.execute_trade = spy

    def test_sell_open_forwards_size_pct(self):
        srv.fetch_all_data()
        sells = [c for c in self.calls if c["side"] == "SELL"]
        self.assertTrue(sells, "SELL open should have been attempted")
        self.assertEqual(sells[0]["size_pct"], 0.1,
                         "SELL open/add must forward size_pct (was: None, fixed 5%)")

    def test_buy_path_still_forwards_size_pct(self):
        srv.run_js_strategy = lambda *a, **k: {"BTC": {"signal": "BUY", "size_pct": 0.2}}
        srv.fetch_all_data()
        buys = [c for c in self.calls if c["side"] == "BUY"]
        self.assertTrue(buys)
        self.assertEqual(buys[0]["size_pct"], 0.2)


class ThreadSafetyTests(unittest.TestCase):
    """D2 (M-4): DB access must work from concurrent threads.

    Today the module shares one sqlite3 connection created on the main thread;
    any worker thread touching it raises ProgrammingError. After the fix each
    thread gets its own connection via get_db() (threading.local).
    """

    def test_concurrent_db_reads_no_cross_thread_error(self):
        errors = []

        def worker():
            try:
                for _ in range(20):
                    srv.rebuild_cycles()
                    srv.equity_curve()
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
