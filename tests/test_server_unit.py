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
from datetime import datetime

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


class ErrorVisibilityTests(unittest.TestCase):
    """D17 (N-9/M-10): failures must be observable — fetch_json logs to
    stderr, strategy/node failures append a warning to exec_log."""

    def test_fetch_json_failure_writes_stderr(self):
        import io as _io
        from contextlib import redirect_stderr
        buf = _io.StringIO()
        # fetch_json is the original implementation here (import state or after
        # SellSizePctTests.tearDownClass restored it) — it calls urlopen.
        original_urlopen = srv.urllib.request.urlopen
        srv.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
        try:
            with redirect_stderr(buf):
                srv.fetch_json("http://x/api/v3/ticker/24hr")
        finally:
            srv.urllib.request.urlopen = original_urlopen
        self.assertIn("boom", buf.getvalue())

    def test_strategy_node_failure_appends_exec_log_warning(self):
        import subprocess as sp
        before = len(srv.exec_log)
        real_run = srv.subprocess.run
        srv.subprocess.run = lambda *a, **k: sp.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="SyntaxError: boom")
        srv.HAS_NODE = True
        try:
            srv.run_js_strategy("fake.js", [{"id": "BTC"}])
        finally:
            srv.subprocess.run = real_run
        self.assertGreater(len(srv.exec_log), before)
        self.assertIn("fake.js", srv.exec_log[-1].get("html", ""))


class PriceThrottleTests(unittest.TestCase):
    """D8 (M-2): prices table must throttle to ~1 row per 5 minutes.

    The bug: recorded_at stored as UTC+8 while the comparison used UTC — the
    check always looked >5min old, so every poll inserted a row.
    """

    def setUp(self):
        self.calls = []
        srv.active_strategy = "fake.js"
        srv._last_signal = {}
        srv.fetch_json = _fake_fetch_json
        srv.fetch_klines_cached = _fake_klines
        srv.run_js_strategy = lambda *a, **k: {"BTC": {"signal": "HOLD"}}
        srv.execute_trade = lambda *a, **k: {"status": "filled"}
        with srv.db_lock:
            srv.get_db().execute("DELETE FROM prices")
            srv.get_db().commit()

    def _count_prices(self):
        with srv.db_lock:
            return srv.get_db().execute("SELECT COUNT(*) FROM prices").fetchone()[0]

    def test_first_record_is_utc_and_second_throttled(self):
        # Empty table -> first poll records. After the fix the stored time is
        # UTC (the same base the throttle compares against); the old code
        # stored UTC+8, so the stored value was 8h ahead of datetime.now() and
        # the comparison could never mean "fresh".
        srv.fetch_all_data()
        with srv.db_lock:
            rows = srv.get_db().execute("SELECT recorded_at FROM prices").fetchall()
        self.assertEqual(len(rows), 1)
        rec = datetime.fromisoformat(rows[0][0])
        self.assertLess(abs((datetime.utcnow() - rec).total_seconds()), 300,
                        f"recorded_at must be UTC-ish, got {rows[0][0]!r}")
        # Second poll right after: fresh record must suppress the insert
        srv.fetch_all_data()
        self.assertEqual(self._count_prices(), 1)

    def test_stale_record_still_records(self):
        with srv.db_lock:
            srv.get_db().execute(
                "INSERT INTO prices (symbol,price,recorded_at) VALUES "
                "('BTCUSDT',100,datetime('now','-6 minutes'))")
            srv.get_db().commit()
        srv.fetch_all_data()
        self.assertEqual(self._count_prices(), 2, "record older than 5min must be written")


if __name__ == "__main__":
    unittest.main(verbosity=2)
