#!/usr/bin/env python3
"""Database initialization + historical data download — shared across all modules."""

import os, csv, io, zipfile, urllib.request, time, sqlite3
from datetime import datetime

_BASE = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
DB_PATH = os.path.join(_BASE, "quant_fleet.db")
INITIAL_CAPITAL = 10_000.0
DEFAULT_SYMBOLS = [("BTCUSDT","Bitcoin"),("ETHUSDT","Ethereum"),("BNBUSDT","BNB"),
                   ("SOLUSDT","Solana"),("ADAUSDT","Cardano"),("HYPERUSDT","Hyperliquid"),("LINKUSDT","Chainlink")]

BINANCE_VISION = "https://data.binance.vision/data/spot/monthly/klines"
HIST_START = (2025, 1)
HIST_END = (2026, 6)
HIST_INTERVAL = "1d"
DATA_DIR = os.path.join(_BASE, "backtest_data")


def init_db():
    """Create all tables, seed defaults. Returns sqlite3.Connection."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY CHECK(id=1),
            cash REAL NOT NULL,
            initial_capital REAL NOT NULL,
            updated_at TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, signal TEXT NOT NULL, confidence INTEGER,
            price REAL, factors_json TEXT, strategy TEXT,
            created_at TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, side TEXT NOT NULL, price REAL NOT NULL,
            quantity REAL NOT NULL, notional REAL NOT NULL,
            status TEXT DEFAULT 'filled', strategy TEXT, signal_id INTEGER,
            created_at TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE, side TEXT NOT NULL,
            entry_price REAL NOT NULL, quantity REAL NOT NULL,
            current_price REAL, unrealized_pnl REAL DEFAULT 0,
            strategy TEXT, opened_at TEXT DEFAULT (datetime('now', '+8 hours')),
            updated_at TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
        CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
        CREATE TABLE IF NOT EXISTS historical_klines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            UNIQUE(symbol, date)
        );
        CREATE INDEX IF NOT EXISTS idx_klines_symbol ON historical_klines(symbol);
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            added_at TEXT DEFAULT (datetime('now', '+8 hours'))
        );
    """)
    if not conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]:
        conn.executemany("INSERT INTO watchlist (symbol,name) VALUES (?,?)", DEFAULT_SYMBOLS)
        conn.commit()
    row = conn.execute("SELECT id FROM portfolio WHERE id=1").fetchone()
    if not row:
        conn.execute("INSERT INTO portfolio (id,cash,initial_capital) VALUES (1,?,?)",
                     (INITIAL_CAPITAL, INITIAL_CAPITAL))
    conn.commit()
    return conn


def download_historical(symbol):
    """Download and store historical daily klines for one symbol."""
    conn = sqlite3.connect(DB_PATH)
    existing = conn.execute("SELECT COUNT(*) FROM historical_klines WHERE symbol=?",
                            (symbol,)).fetchone()[0]
    if existing > 300:
        print(f"  {symbol}: {existing} rows cached, skipping")
        conn.close()
        return

    all_rows = []
    for y in range(HIST_START[0], HIST_END[0] + 1):
        m_start = HIST_START[1] if y == HIST_START[0] else 1
        m_end = HIST_END[1] if y == HIST_END[0] else 12
        for m in range(m_start, m_end + 1):
            fname = f"{symbol}-{HIST_INTERVAL}-{y}-{m:02d}.zip"
            url = f"{BINANCE_VISION}/{symbol}/{HIST_INTERVAL}/{fname}"
            local = os.path.join(DATA_DIR, fname)

            if not os.path.exists(local):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "QuantFleet/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        with open(local, "wb") as f:
                            f.write(r.read())
                    print(f"    {fname} downloaded")
                except Exception as e:
                    print(f"    {fname} — {e}")
                    continue
                time.sleep(0.2)

            try:
                with zipfile.ZipFile(local) as z:
                    with z.open(z.namelist()[0]) as f:
                        for row in csv.reader(io.TextIOWrapper(f)):
                            ts = int(row[0]) // 1_000_000
                            date_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                            all_rows.append((symbol, date_str,
                                float(row[1]), float(row[2]), float(row[3]),
                                float(row[4]), float(row[5])))
            except Exception as e:
                print(f"    parse error {fname}: {e}")

    if all_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO historical_klines (symbol,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
            all_rows)
        conn.commit()
        print(f"  {symbol}: stored {len(all_rows)} rows")
    conn.close()


def download_all_historical():
    """Download historical klines for all symbols in watchlist."""
    conn = sqlite3.connect(DB_PATH)
    syms = [r[0] for r in conn.execute("SELECT symbol FROM watchlist ORDER BY id").fetchall()]
    conn.close()

    print(f"Downloading historical data for {len(syms)} symbols ({HIST_START[0]}-{HIST_START[1]:02d} → {HIST_END[0]}-{HIST_END[1]:02d})")
    for sym in syms:
        print()
        print(f"=== {sym} ===")
        download_historical(sym)
    print()
    print("Historical data download complete.")


if __name__ == "__main__":
    conn = init_db()
    rows = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    print(f"Database initialized. Watchlist: {rows} symbols")
    conn.close()
    download_all_historical()
