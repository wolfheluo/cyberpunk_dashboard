#!/usr/bin/env python3
"""HTTP-seam tests for Quant Fleet server — spec/remediation.md Testing Decisions.

Seams (pre-agreed in spec): public HTTP API behaviour, verified with raw
requests that preserve the path verbatim (no client-side normalization), so
traversal payloads reach the server exactly as an attacker would send them.
"""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
import socket
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
        body = r.read()
        c.close()
        return r.status, body

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.tmp.cleanup()


class TraversalTests(unittest.TestCase):
    """D1 (C-1/C-2): static routes must not serve files outside their directory."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_dashboard_db_traversal_blocked(self):
        st, body = self.srv.request("GET", "/dashboard/../quant_fleet.db")
        self.assertNotEqual(st, 200)
        self.assertNotIn(b"SQLite format 3", body)

    def test_dashboard_passwd_traversal_blocked(self):
        st, _ = self.srv.request("GET", "/dashboard/../../../etc/passwd")
        self.assertNotEqual(st, 200)

    def test_i18n_db_traversal_blocked(self):
        st, body = self.srv.request("GET", "/i18n/../../quant_fleet.db")
        self.assertNotEqual(st, 200)
        self.assertNotIn(b"SQLite format 3", body)

    def test_i18n_dashboard_passwd_traversal_blocked(self):
        # 4 levels: _BASE/dashboard/i18n/../../../../ -> / — reads /etc/passwd if unguarded
        st, body = self.srv.request("GET", "/dashboard/i18n/../../../../etc/passwd")
        self.assertNotEqual(st, 200)
        self.assertNotIn(b"root:", body)

    def test_dashboard_encoded_dotdot_blocked(self):
        # %2e%2e = ".." — decodes server-side in the path join
        st, _ = self.srv.request("GET", "/dashboard/%2e%2e/quant_fleet.db")
        self.assertNotEqual(st, 200)

    def test_legit_static_files_still_serve(self):
        for p in ("/dashboard/js/app.js",
                  "/dashboard/css/style.css",
                  "/dashboard/i18n/en.json",
                  "/dashboard/i18n/zh.json"):
            st, _ = self.srv.request("GET", p)
            self.assertEqual(st, 200, p)


class SymbolValidationTests(unittest.TestCase):
    """D18: watchlist add must reject HTML-bearing symbol/name (stored-XSS source)."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _add(self, payload):
        return self.srv.request("POST", "/api/symbols/add",
                                json.dumps(payload).encode())

    def test_symbol_with_html_rejected(self):
        st, body = self._add({"symbol": "X<img src=x onerror=alert(1)>USDT", "name": "x"})
        self.assertEqual(st, 400, body)

    def test_name_with_html_rejected(self):
        st, body = self._add({"symbol": "DOGEUSDT", "name": "<script>alert(1)</script>"})
        self.assertEqual(st, 400, body)

    def test_name_with_quotes_rejected(self):
        st, body = self._add({"symbol": "DOGEUSDT", "name": 'x" onmouseover="alert(1)'})
        self.assertEqual(st, 400, body)

    def test_symbol_not_usdt_rejected(self):
        st, body = self._add({"symbol": "DOGE", "name": "Dogecoin"})
        self.assertEqual(st, 400, body)

    def test_legit_symbol_name_accepted(self):
        st, body = self._add({"symbol": "DOGEUSDT", "name": "Dogecoin"})
        self.assertEqual(st, 200, body)

    def test_legit_unicode_name_accepted(self):
        st, body = self._add({"symbol": "ETHUSDT", "name": "以太坊"})
        self.assertEqual(st, 200, body)


class SimulateValidationTests(unittest.TestCase):
    """M-5: /api/trade/simulate must reject invalid symbol/side/price.

    Was: any symbol (not in watchlist), any side (falls through to opening a
    short), any price — unauthenticated fake-trade injection into account stats.
    """

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _sim(self, payload):
        return self.srv.request("POST", "/api/trade/simulate",
                                json.dumps(payload).encode())

    def test_symbol_not_in_watchlist_rejected(self):
        st, body = self._sim({"symbol": "NOPEUSDT", "side": "BUY", "price": 1.0})
        self.assertEqual(st, 400, body)

    def test_invalid_side_rejected(self):
        st, body = self._sim({"symbol": "BTCUSDT", "side": "HOLD", "price": 1.0})
        self.assertEqual(st, 400, body)

    def test_zero_price_rejected(self):
        st, body = self._sim({"symbol": "BTCUSDT", "side": "BUY", "price": 0})
        self.assertEqual(st, 400, body)

    def test_negative_price_rejected(self):
        st, body = self._sim({"symbol": "BTCUSDT", "side": "SELL", "price": -5})
        self.assertEqual(st, 400, body)

    def test_legit_simulate_still_works(self):
        st, body = self._sim({"symbol": "BTCUSDT", "side": "BUY", "price": 100.0})
        self.assertEqual(st, 200, body)


class ConcurrencyTests(unittest.TestCase):
    """D2 (M-4): ThreadingHTTPServer must serve concurrent DB-touching
    requests without sqlite cross-thread errors (each thread gets its own
    connection via get_db())."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_mixed_concurrent_requests_all_ok(self):
        endpoints = ["/api/portfolio", "/api/trades", "/api/symbols",
                     "/api/strategies", "/dashboard/js/app.js",
                     "/dashboard/i18n/en.json"]
        bad = []

        def hit(path):
            for _ in range(4):
                try:
                    st, _ = self.srv.request("GET", path)
                    if st >= 500:
                        bad.append((path, st))
                except Exception as e:  # noqa: BLE001
                    bad.append((path, repr(e)))

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(hit, p) for p in endpoints for _ in range(4)]
            for f in futures:
                f.result(timeout=30)
        self.assertEqual(bad, [])


class SaveSyntaxCheckTests(unittest.TestCase):
    """D17 (M-10): /api/strategy/save must reject syntax-broken code with 400
    (node --check) instead of persisting it and silently killing the poll loop."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ServerHarness()
        st, _ = cls.srv.request("POST", "/api/strategy/create",
                                json.dumps({"filename": "t_syn.js"}).encode())
        assert st == 200

    @classmethod
    def tearDownClass(cls):
        cls.srv.request("POST", "/api/strategy/t_syn.js/delete")
        cls.srv.stop()

    def _save(self, code):
        return self.srv.request("POST", "/api/strategy/t_syn.js/save",
                                json.dumps({"code": code}).encode())

    def test_syntax_error_rejected(self):
        st, body = self._save("function { this is not javascript")
        self.assertEqual(st, 400, body)

    def test_broken_object_literal_rejected(self):
        st, body = self._save("({evaluate:function(){")
        self.assertEqual(st, 400, body)

    def test_valid_code_accepted(self):
        st, body = self._save('NAME="t_syn";DESCRIPTION="x";'
                              '({evaluate:function(){return{signal:"HOLD"};}})')
        self.assertEqual(st, 200, body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
