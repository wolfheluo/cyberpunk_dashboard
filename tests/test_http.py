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


if __name__ == "__main__":
    unittest.main(verbosity=2)
