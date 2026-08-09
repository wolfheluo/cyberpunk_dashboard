#!/usr/bin/env python3
"""HTTP-seam tests for Quant Fleet server — spec/remediation.md Testing Decisions.

Seams (pre-agreed in spec): public HTTP API behaviour, verified with raw
requests that preserve the path verbatim (no client-side normalization), so
traversal payloads reach the server exactly as an attacker would send them.
"""
import http.client
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

    def request(self, method, path):
        """Raw request — the path is sent verbatim (keeps ../ intact)."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest(method, path, skip_accept_encoding=True)
        c.endheaders()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
