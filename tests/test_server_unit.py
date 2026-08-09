#!/usr/bin/env python3
"""M-1 (D7): SELL open/add must forward size_pct like the BUY path does.

Seam: module-level monkeypatch of the network + strategy boundaries, then
observe what execute_trade receives (public trade-execution contract).
"""
import json
import os
import subprocess
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
_real_execute_trade = srv.execute_trade  # captured before any test fakes it


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
        self._saved = (srv.fetch_json, srv.fetch_klines_cached,
                       srv.run_js_strategy, srv.execute_trade)
        srv.fetch_json = _fake_fetch_json
        srv.fetch_klines_cached = _fake_klines
        srv.run_js_strategy = lambda *a, **k: {"BTC": {"signal": "HOLD"}}
        srv.execute_trade = lambda *a, **k: {"status": "filled"}
        with srv.db_lock:
            srv.get_db().execute("DELETE FROM prices")
            srv.get_db().commit()

    def tearDown(self):
        srv.fetch_json, srv.fetch_klines_cached, srv.run_js_strategy, srv.execute_trade = self._saved

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


class CoverCashTests(unittest.TestCase):
    """D9 (N-1): cover short with insufficient cash must be rejected,
    not silently partially filled (which drifts grid state)."""

    def setUp(self):
        with srv.db_lock:
            db = srv.get_db()
            db.execute("DELETE FROM trades")
            db.execute("DELETE FROM positions")
            db.execute("DELETE FROM signals")
            db.execute("UPDATE portfolio SET cash=100 WHERE id=1")
            db.execute("INSERT INTO positions (symbol,side,entry_price,quantity,"
                       "current_price,unrealized_pnl,strategy) "
                       "VALUES ('BTCUSDT','SELL',100,10,100,0,'t')")
            db.commit()

    def tearDown(self):
        with srv.db_lock:
            db = srv.get_db()
            db.execute("DELETE FROM trades")
            db.execute("DELETE FROM positions")
            db.execute("DELETE FROM signals")
            db.execute("UPDATE portfolio SET cash=10000 WHERE id=1")
            db.commit()

    def test_insufficient_cash_partial_cover_rejected(self):
        # want 5 * 30 = 150 > cash 100 -> rejected (was: partial 3.33 filled)
        r = srv.execute_trade("BTCUSDT", "BUY", 30, "t", close_pct=0.5)
        self.assertEqual(r["status"], "rejected", r)

    def test_sufficient_cash_cover_filled(self):
        # 5 * 15 = 75 <= 100 -> full requested close still fills
        r = srv.execute_trade("BTCUSDT", "BUY", 15, "t", close_pct=0.5)
        self.assertEqual(r["status"], "filled", r)


class SignalsGrowthTests(unittest.TestCase):
    """D12 (N-4): WAIT signals must not be written to the signals table."""

    def setUp(self):
        srv.active_strategy = "fake.js"
        srv._last_signal = {}
        self._saved = (srv.fetch_json, srv.fetch_klines_cached,
                       srv.run_js_strategy, srv.execute_trade)
        srv.fetch_json = _fake_fetch_json
        srv.fetch_klines_cached = _fake_klines
        srv.execute_trade = lambda *a, **k: {"status": "filled"}
        with srv.db_lock:
            srv.get_db().execute("DELETE FROM signals")
            srv.get_db().commit()

    def tearDown(self):
        srv.fetch_json, srv.fetch_klines_cached, srv.run_js_strategy, srv.execute_trade = self._saved

    def test_wait_signal_not_written(self):
        srv.run_js_strategy = lambda *a, **k: {"BTC": {"signal": "WAIT"}}
        srv.fetch_all_data()
        with srv.db_lock:
            n = srv.get_db().execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        self.assertEqual(n, 0, "WAIT rows must be skipped (was: ~1 row/poll/symbol)")


class StaticRemediationTests(unittest.TestCase):
    """Static seams (spec allows static checks): D13/D16 structural guards."""

    def _server_src(self):
        with open(os.path.join(ROOT, "quant_fleet_server.py"), encoding="utf-8") as f:
            return f.read()

    def test_activate_clears_strategy_state(self):
        src = self._server_src()
        i = src.index('if self.path=="/api/strategy/activate":')
        self.assertIn("_strategy_state.pop(fname", src[i:i + 900])

    def test_dead_code_removed(self):
        src = self._server_src()
        for token in ("def _esc(", "def add_log(", "_trading_paused_until"):
            self.assertNotIn(token, src)


class StaleCacheTests(unittest.TestCase):
    """D10/N-2 + D11/N-3: fetch failures fall back to stale cache; bookTicker
    is TTL-cached so 1s polls stop hammering Binance."""

    def setUp(self):
        srv._ticker24_cache = None
        srv._ticker24_ts = 0.0
        srv._klines_cache = {}
        srv._klines_cache_ts = {}
        srv._book_cache = None
        srv._book_cache_ts = 0.0
        self._saved_fetch = srv.fetch_json

    def tearDown(self):
        srv.fetch_json = self._saved_fetch

    def test_ticker24_stale_fallback(self):
        calls = {"n": 0}

        def fake(url):
            calls["n"] += 1
            return [{"symbol": "BTCUSDT", "lastPrice": "100"}] if calls["n"] == 1 else None

        srv.fetch_json = fake
        first = srv.fetch_ticker24_cached()
        second = srv.fetch_ticker24_cached(ttl=0)  # force re-fetch, which fails
        self.assertIsNotNone(first)
        self.assertEqual(second, first, "failure must fall back to stale cache, not None")

    def test_klines_stale_fallback(self):
        calls = {"n": 0}

        def fake(url):
            calls["n"] += 1
            return [["t", "1", "2", "3", "4", "5"]] if calls["n"] == 1 else None

        srv.fetch_json = fake
        first = srv.fetch_klines_cached("BTCUSDT", "1h", ttl=0)
        second = srv.fetch_klines_cached("BTCUSDT", "1h", ttl=0)
        self.assertIsNotNone(first)
        self.assertEqual(second, first)

    def test_book_ttl_cached(self):
        calls = {"n": 0}
        srv.fetch_json = lambda url: calls.__setitem__("n", calls["n"] + 1) or [{"symbol": "BTCUSDT"}]
        srv.fetch_book_cached()
        srv.fetch_book_cached()
        self.assertEqual(calls["n"], 1, "second call within TTL must not hit Binance")


class RoundingTests(unittest.TestCase):
    """N-11: positions quantity must be rounded like trades (no float drift)."""

    def setUp(self):
        self._saved_exec = srv.execute_trade
        srv.execute_trade = _real_execute_trade  # ensure we always call the real one
        with srv.db_lock:
            db = srv.get_db()
            db.execute("DELETE FROM trades")
            db.execute("DELETE FROM positions")
            db.execute("UPDATE portfolio SET cash=10000 WHERE id=1")
            db.commit()

    def tearDown(self):
        srv.execute_trade = self._saved_exec
        with srv.db_lock:
            db = srv.get_db()
            db.execute("DELETE FROM trades")
            db.execute("DELETE FROM positions")
            db.commit()

    def test_open_position_qty_rounded_to_8(self):
        r = srv.execute_trade("BTCUSDT", "BUY", 3, "t")  # 5% of 10k / 3 = 166.666...
        self.assertEqual(r["status"], "filled", r)
        with srv.db_lock:
            qty = srv.get_db().execute(
                "SELECT quantity FROM positions WHERE symbol='BTCUSDT'").fetchone()[0]
        self.assertEqual(qty, round(500 / 3, 8), qty)


class MetaRegexTests(unittest.TestCase):
    """N-14: strategy NAME/DESCRIPTION extraction must accept single quotes."""

    def test_single_quote_name_and_desc(self):
        self.assertEqual(srv._strategy_meta("NAME: 'My Strat';\nDESCRIPTION: 'desc here';"),
                         ("My Strat", "desc here"))

    def test_double_quotes_still_work(self):
        self.assertEqual(srv._strategy_meta('NAME: "X"'), ("X", ""))


class StateMtimeTests(unittest.TestCase):
    """N-23: strategy state must be dropped when the file changes on disk.

    Behavioural: run the real node helper twice with a stubbed grid state; a
    file mtime change between runs must invalidate the in-memory state so the
    node process restarts from a clean slate.
    """

    def setUp(self):
        self.target = os.path.join(ROOT, "strategies", "grid.js")
        self.orig_mtime = os.path.getmtime(self.target)
        srv.HAS_NODE = True
        srv._strategy_state.clear()
        srv._strategy_mtime.clear()

    def tearDown(self):
        os.utime(self.target, (self.orig_mtime, self.orig_mtime))
        srv._strategy_state.clear()
        srv._strategy_mtime.clear()

    def test_mtime_change_resets_state(self):
        # First run establishes state with a fresh marker (real node execution)
        srv._strategy_state["grid.js"] = {"grids": {"BTC": {"FAKE": "stale"}}}
        srv.run_js_strategy("grid.js", [{"id": "BTC", "ticker": {"id": "BTC", "name": "BTC",
                                                                 "price": 100, "volume": 1,
                                                                 "change_pct": 0, "high_24h": 101,
                                                                 "low_24h": 99, "pct_from_high": 0,
                                                                 "pct_from_low": 0, "book": None,
                                                                 "position": None, "portfolio": None},
                                         "indicators": {"closes": [100] * 60}}])
        self.assertNotIn("FAKE", json.dumps(srv._strategy_state.get("grid.js", {})),
                         "mtime baseline must already clear the stale marker")
        # Touch the file — the next run must NOT carry over the previous state
        srv._strategy_state["grid.js"] = {"grids": {"BTC": {"FAKE2": "stale2"}}}
        os.utime(self.target, (self.orig_mtime + 5, self.orig_mtime + 5))
        srv.run_js_strategy("grid.js", [{"id": "BTC", "ticker": {"id": "BTC", "name": "BTC",
                                                                 "price": 100, "volume": 1,
                                                                 "change_pct": 0, "high_24h": 101,
                                                                 "low_24h": 99, "pct_from_high": 0,
                                                                 "pct_from_low": 0, "book": None,
                                                                 "position": None, "portfolio": None},
                                         "indicators": {"closes": [100] * 60}}])
        self.assertNotIn("FAKE2", json.dumps(srv._strategy_state.get("grid.js", {})),
                         "state must be reset when the strategy file changes")


class PortfolioConsistencyTests(unittest.TestCase):
    """N-6 + N-7: /api/portfolio valuation uses live prices (same base as
    /api/data); portfolio_stats is cached by trades count (N-7 perf)."""

    def setUp(self):
        srv.active_strategy = "fake.js"
        srv._last_signal = {}
        srv._stats_cache = {"n": -1, "result": None}
        self._saved = (srv.fetch_json, srv.fetch_klines_cached, srv.execute_trade)
        srv.fetch_json = _fake_fetch_json
        srv.fetch_klines_cached = _fake_klines
        srv.execute_trade = lambda *a, **k: {"status": "filled"}
        with srv.db_lock:
            db = srv.get_db()
            db.execute("DELETE FROM trades")
            db.execute("DELETE FROM positions")
            db.execute("UPDATE portfolio SET cash=10000 WHERE id=1")
            db.execute("INSERT INTO positions (symbol,side,entry_price,quantity,"
                       "current_price,unrealized_pnl,strategy) "
                       "VALUES ('BTCUSDT','BUY',50,2,50,0,'t')")
            db.commit()

    def tearDown(self):
        with srv.db_lock:
            db = srv.get_db()
            db.execute("DELETE FROM trades")
            db.execute("DELETE FROM positions")
            db.commit()
        srv.fetch_json, srv.fetch_klines_cached, srv.execute_trade = self._saved

    def test_position_value_uses_live_price(self):
        # fake 24hr says BTCUSDT lastPrice=100 (entry was 50) -> pos value 2*100=200
        v = srv._position_value_now()
        self.assertEqual(v, 200.0, v)

    def test_stats_cached_by_trade_count(self):
        calls = {"n": 0}
        real = srv.portfolio_stats
        srv.portfolio_stats = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or (50.0, 1.2, -0.1))
        try:
            srv.portfolio_stats_cached()
            srv.portfolio_stats_cached()
        finally:
            srv.portfolio_stats = real
        self.assertEqual(calls["n"], 1, "same trade count must reuse the cache")


class MatrixAndLabelTests(unittest.TestCase):
    """N-13: strategy_matrix active cell must match the real active strategy;
    N-12: _sma4h must carry the true 4h SMA."""

    def setUp(self):
        srv.active_strategy = "grid.js"
        srv._last_signal = {}
        self._saved = (srv.fetch_json, srv.fetch_klines_cached,
                       srv.run_js_strategy, srv.execute_trade)
        srv.fetch_json = _fake_fetch_json
        srv.fetch_klines_cached = _fake_klines
        srv.execute_trade = lambda *a, **k: {"status": "filled"}
        with srv.db_lock:
            srv.get_db().execute("DELETE FROM trades")
            srv.get_db().commit()

    def tearDown(self):
        srv.fetch_json, srv.fetch_klines_cached, srv.run_js_strategy, srv.execute_trade = self._saved
        with srv.db_lock:
            srv.get_db().execute("DELETE FROM trades")
            srv.get_db().commit()

    def test_matrix_active_matches_active_strategy(self):
        srv.run_js_strategy = lambda *a, **k: {"BTC": {"signal": "HOLD"}}
        d = srv.fetch_all_data()
        cells = d["strategy_matrix"]["cells"]
        actives = {c[0] for c in cells if c[2] == "active"}
        # grid.js is the active strategy; its name must be the active row
        self.assertEqual(len(actives), 1, cells)
        self.assertEqual(d["strategy_matrix"]["strategies"][list(actives)[0]], "Grid Trading")

    def test_sma4h_is_real_4h_sma(self):
        srv.run_js_strategy = lambda *a, **k: {"BTC": {"signal": "HOLD"}}
        srv.fetch_klines_cached = lambda symbol, interval, limit=100, ttl=60: (
            [[1_700_000_000_000 + i * 3_600_000, "100", "101", "99", "100", "1000000", 0]
             for i in range(limit)] if interval == "1h" else
            [[1_700_000_000_000 + i * 14_400_000, "200", "201", "199", "200", "1000000", 0]
             for i in range(limit)])
        d = srv.fetch_all_data()
        self.assertEqual(d["tickers"][0]["_sma4h"], 200.0, d["tickers"][0]["_sma4h"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
