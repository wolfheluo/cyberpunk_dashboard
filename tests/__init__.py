"""Quant Fleet test suite — T-00 (rebuilt harness, 2026-08-09).

Two pre-agreed seams (spec issue #1, ticket #2):
  1. HTTP harness  — isolated server instance (QF_DB_PATH/QF_PORT env seams)
     exercised with raw http.client requests (paths sent verbatim, no
     client-side normalization).
  2. Module-level fake — import the server module against a temp DB, monkeypatch
     the Binance dependencies (fetch_json/fetch_klines_cached/fetch_book_cached)
     and observe public behaviour (return values, DB state).

Run:  ./venv/bin/python -m unittest discover -s tests -v
Convention (from the remediation batches): run `node --check` on every JS edit
and `py_compile` on every Python edit before committing.
"""
