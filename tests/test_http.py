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


if __name__ == "__main__":
    unittest.main(verbosity=2)
