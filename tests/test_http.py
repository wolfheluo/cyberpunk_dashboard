#!/usr/bin/env python3
"""HTTP-seam tests for the Quant Fleet server.

Seam (pre-agreed in spec issue #1): the public HTTP API, verified through an
isolated server instance. Requests are sent with raw http.client so paths are
preserved verbatim (no client-side normalization).
"""
import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ServerHarness:
    """Boot an isolated Quant Fleet instance (temp DB + free port)."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        env = {**os.environ, "QF_DB_PATH": self.db, "QF_PORT": str(self.port)}
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "quant_fleet_server.py"],
            cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
                c.request("GET", "/")
                c.getresponse().read()
                c.close()
                return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("server did not become ready")

    def request(self, method, path, body=None):
        """Raw request — the path is sent verbatim (keeps ../ intact)."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest(method, path, skip_accept_encoding=True)
        if body is not None:
            c.putheader("Content-Type", "application/json")
            c.putheader("Content-Length", str(len(body)))
        c.endheaders()
        if body is not None:
            c.send(body)
        r = c.getresponse()
        resp_body = r.read()
        c.close()
        return r.status, resp_body

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.tmp.cleanup()


class HarnessSmokeTests(unittest.TestCase):
    """T-00: the harness boots a real isolated instance and serves the API."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_root_serves_dashboard(self):
        status, body = self.srv.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"QUANT FLEET", body)

    def test_strategies_endpoint_lists_grid(self):
        status, body = self.srv.request("GET", "/api/strategies")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["active"], "")
        self.assertEqual([s["filename"] for s in data["strategies"]], ["grid.js"])


class ResetAndSimulateTests(unittest.TestCase):
    """T-01 (C-1/C-2): /api/reset works; simulate executes ONE trade and does
    NOT wipe the account (regression from commit 360ffe2, where the reset
    branch header was deleted and its body merged into the simulate branch)."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        # clean account state directly (pre-fix /api/reset is broken, so the
        # test cannot rely on it for setup)
        conn = sqlite3.connect(self.srv.db)
        conn.execute("DELETE FROM trades")
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM signals")
        conn.execute("UPDATE portfolio SET cash=10000 WHERE id=1")
        conn.commit()
        conn.close()

    def _simulate(self, side="BUY", price=100):
        body = json.dumps({"symbol": "BTCUSDT", "side": side, "price": price}).encode()
        return self.srv.request("POST", "/api/trade/simulate", body)

    def test_reset_returns_200_and_clears_account(self):
        self._simulate()
        status, body = self.srv.request("POST", "/api/reset")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["status"], "reset")
        self.assertEqual(d["capital"], 10000)
        self.assertEqual(d["active_strategy"], "")
        _, pos = self.srv.request("GET", "/api/positions")
        self.assertEqual(json.loads(pos)["positions"], [])
        _, trades = self.srv.request("GET", "/api/trades")
        self.assertEqual(json.loads(trades)["trades"], [])
        _, pf = self.srv.request("GET", "/api/portfolio")
        self.assertEqual(json.loads(pf)["cash"], 10000)

    def test_simulate_keeps_the_trade(self):
        # the 360ffe2 bug wiped trades/positions right after filling
        status, body = self._simulate()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "filled")
        _, trades = self.srv.request("GET", "/api/trades")
        self.assertEqual(len(json.loads(trades)["trades"]), 1)
        _, pos = self.srv.request("GET", "/api/positions")
        self.assertEqual(len(json.loads(pos)["positions"]), 1)
        _, pf = self.srv.request("GET", "/api/portfolio")
        self.assertLess(json.loads(pf)["cash"], 10000)

    def test_simulate_single_http_response(self):
        # raw socket: exactly ONE HTTP response for one request — the bug
        # wrote a second {"status":"reset"} response after the filled one.
        body = json.dumps({"symbol": "BTCUSDT", "side": "SELL", "price": 100}).encode()
        s = socket.create_connection(("127.0.0.1", self.srv.port), timeout=5)
        req = (f"POST /api/trade/simulate HTTP/1.0\r\nHost: t\r\n"
               f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body
        s.sendall(req)
        time.sleep(0.3)  # let the server write everything before we read
        s.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        self.assertEqual(data.count(b"HTTP/1.0"), 1,
                         f"expected exactly one HTTP response, got:\n{data[:400]}")


class BacktestAtrLookaheadTests(unittest.TestCase):
    """T-03 (M-4): backtest atr14 must only use bars up to the current point.

    Dataset: 60 low-volatility bars (ATR ~1) then 40 high-volatility bars
    (ATR ~20). An ATR Probe strategy BUYs while atr14 < 5 and SELLs while
    atr14 > 5. With lookahead (the bug: calcATR on the WHOLE array), atr14 is
    constant ~20 from bar 0 -> only SELLs, buy_count == 0. Fixed: early bars
    atr14 ~1 -> BUY, late bars -> SELL.
    """

    STRAT_CODE = """({
  NAME: "ATR Probe",
  DESCRIPTION: "atr14 lookahead probe",
  evaluate: function (ticker, indicators) {
    if (indicators.atr14 < 5) return {signal: "BUY", confidence: 80};
    if (indicators.atr14 > 5) return {signal: "SELL", confidence: 80};
    return {signal: "HOLD", confidence: 50};
  }
})
"""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()
        # seed historical_klines: 60 low-vol + 40 high-vol daily bars
        conn = sqlite3.connect(cls.srv.db)
        conn.execute("DELETE FROM historical_klines")
        rows = []
        for i in range(100):
            amp = 0.5 if i < 60 else 10.0
            close = 100 + amp
            rows.append(("BTCUSDT", f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                         100, close + amp, close - amp, close, 1000000))
        conn.executemany("INSERT OR REPLACE INTO historical_klines "
                         "(symbol,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()
        # create the probe strategy via the API (real path)
        status, _ = cls.srv.request("POST", "/api/strategy/create",
                                    json.dumps({"filename": "atr_probe.js"}).encode())
        assert status == 200, status
        status, body = cls.srv.request("POST", "/api/strategy/atr_probe.js/save",
                                       json.dumps({"code": cls.STRAT_CODE}).encode())
        assert status == 200, (status, body)

    @classmethod
    def tearDownClass(cls):
        cls.srv.request("POST", "/api/strategy/atr_probe.js/delete")
        cls.srv.stop()

    def test_atr_probe_buys_early_and_sells_late(self):
        status, body = self.srv.request("POST", "/api/backtest/run")
        self.assertEqual(status, 200, body[:200])
        data = json.loads(body)
        bt = next(b for b in data["backtests"]
                  if b["strategy"] == "ATR Probe" and b["symbol"] == "BTCUSDT")
        self.assertGreaterEqual(bt["buy_count"], 1,
                                f"early low-vol bars must BUY — atr14 has lookahead? {bt}")
        self.assertGreaterEqual(bt["sell_count"], 1, bt)


class StrategyFilenameTests(unittest.TestCase):
    """T-06 (M-5): list_js_strategies must skip filenames that fail the
    [A-Za-z0-9_-]+.js whitelist. The write routes already validate; the LIST
    route did not — a maliciously named .js file dropped into strategies/
    flowed unescaped into frontend inline onclick (stored-XSS edge)."""

    WEIRD_NAME = 'a".js'

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()
        cls.strategies_dir = os.path.join(ROOT, "strategies")
        cls.weird_path = os.path.join(cls.strategies_dir, cls.WEIRD_NAME)
        with open(cls.weird_path, "w", encoding="utf-8") as f:
            f.write('({ NAME: "Weird", evaluate: function() { return {signal:"HOLD"}; } })\n')

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.weird_path):
            os.remove(cls.weird_path)
        cls.srv.stop()

    def test_weird_filename_not_listed(self):
        status, body = self.srv.request("GET", "/api/strategies")
        self.assertEqual(status, 200)
        data = json.loads(body)
        names = [s["filename"] for s in data["strategies"]]
        self.assertNotIn(self.WEIRD_NAME, names, names)
        self.assertIn("grid.js", names)

    def test_weird_filename_cannot_be_activated(self):
        status, _ = self.srv.request("POST", "/api/strategy/activate",
                                     json.dumps({"filename": self.WEIRD_NAME}).encode())
        self.assertEqual(status, 400)


class HeadRequestTests(unittest.TestCase):
    """T-07 (M-6): do_HEAD must mirror do_GET (same headers, no body). The
    default SimpleHTTPRequestHandler.do_HEAD served directory listings of the
    project root over the LAN — a file-tree leak."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _head(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        c.putrequest("HEAD", path, skip_accept_encoding=True)
        c.endheaders()
        r = c.getresponse()
        body = r.read()  # HEAD: no body expected
        headers = dict(r.getheaders())
        c.close()
        return r.status, body, headers

    def test_head_root_matches_get(self):
        g_status, g_body = self.srv.request("GET", "/")
        status, body, headers = self._head("/")
        self.assertEqual(status, g_status)
        self.assertEqual(body, b"", "HEAD must not carry a body")
        self.assertEqual(headers.get("Content-Length"), str(len(g_body)),
                         "HEAD / must advertise the dashboard length, not a directory listing")

    def test_head_api_matches_get(self):
        g_status, g_body = self.srv.request("GET", "/api/strategies")
        status, body, headers = self._head("/api/strategies")
        self.assertEqual(status, g_status)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Content-Length"), str(len(g_body)))


class MalformedJsonTests(unittest.TestCase):
    """T-08 (M-7): POST endpoints must answer 400 on malformed/missing JSON
    instead of crashing the handler (json.loads raised -> dead connection)."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_activate_malformed_json_400(self):
        status, body = self.srv.request("POST", "/api/strategy/activate", b"not-json{")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "Invalid JSON"})

    def test_create_empty_body_400(self):
        status, body = self.srv.request("POST", "/api/strategy/create", b"")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "Invalid JSON"})

    def test_simulate_malformed_json_400(self):
        status, body = self.srv.request("POST", "/api/trade/simulate", b"[[[")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "Invalid JSON"})

    def test_valid_json_still_works(self):
        # regression guard: valid activate still 200
        status, _ = self.srv.request("POST", "/api/strategy/activate",
                                     json.dumps({"filename": "grid.js"}).encode())
        self.assertEqual(status, 200)


class HeadNoSideEffectTests(unittest.TestCase):
    """Code-review finding (Point 1): HEAD /api/data must NOT trigger the
    trading pipeline (do_HEAD delegated to do_GET, which called fetch_all_data
    and auto-executed trades). A HEAD request is a probe — it must be side-
    effect free even when an always-BUY strategy is active."""

    ALWAYS_BUY = """({
  NAME: "Always Buy",
  DESCRIPTION: "code-review HEAD side-effect probe",
  evaluate: function (ticker, indicators) {
    return {signal: "BUY", confidence: 99};
  }
})
"""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()
        cls.srv.request("POST", "/api/strategy/create",
                        json.dumps({"filename": "head_probe.js"}).encode())
        cls.srv.request("POST", "/api/strategy/head_probe.js/save",
                        json.dumps({"code": cls.ALWAYS_BUY}).encode())
        cls.srv.request("POST", "/api/strategy/activate",
                        json.dumps({"filename": "head_probe.js"}).encode())

    @classmethod
    def tearDownClass(cls):
        cls.srv.request("POST", "/api/strategy/head_probe.js/delete")
        cls.srv.stop()

    def _trade_count(self):
        _, body = self.srv.request("GET", "/api/trades")
        return len(json.loads(body)["trades"])

    def test_head_api_data_does_not_execute_trades(self):
        # active always-BUY strategy: a GET would trade; a HEAD must not.
        # Reset first so the count is deterministic.
        self.srv.request("POST", "/api/reset")
        self.srv.request("POST", "/api/strategy/activate",
                         json.dumps({"filename": "head_probe.js"}).encode())
        before = self._trade_count()
        c = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        c.putrequest("HEAD", "/api/data", skip_accept_encoding=True)
        c.endheaders()
        r = c.getresponse()
        r.read()
        c.close()
        self.assertEqual(r.status, 200, "HEAD /api/data must answer 200")
        after = self._trade_count()
        self.assertEqual(after, before,
                         f"HEAD must not execute trades (before={before}, after={after})")


if __name__ == "__main__":
    unittest.main(verbosity=2)
