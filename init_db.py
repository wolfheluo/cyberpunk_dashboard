#!/usr/bin/env python3
"""Database initialization — shared across server, backtest runner, and tools."""

import sqlite3

DB_PATH = "/root/quant_fleet.db"
INITIAL_CAPITAL = 10_000.0
DEFAULT_SYMBOLS = [("BTCUSDT","Bitcoin"),("ETHUSDT","Ethereum"),("BNBUSDT","BNB"),
                   ("SOLUSDT","Solana"),("ADAUSDT","Cardano"),("HYPERUSDT","Hyperliquid"),("LINKUSDT","Chainlink")]

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY CHECK(id=1),
            cash REAL NOT NULL,
            initial_capital REAL NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            confidence INTEGER,
            price REAL,
            factors_json TEXT,
            strategy TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            notional REAL NOT NULL,
            status TEXT DEFAULT 'filled',
            strategy TEXT,
            signal_id INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            quantity REAL NOT NULL,
            current_price REAL,
            unrealized_pnl REAL DEFAULT 0,
            strategy TEXT,
            opened_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
        CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
        CREATE TABLE IF NOT EXISTS historical_klines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            UNIQUE(symbol, date)
        );
        CREATE INDEX IF NOT EXISTS idx_klines_symbol ON historical_klines(symbol);
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            added_at TEXT DEFAULT (datetime('now'))
        );
    """)
    if not conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]:
        conn.executemany("INSERT INTO watchlist (symbol,name) VALUES (?,?)", DEFAULT_SYMBOLS)
        conn.commit()
    # Init portfolio if empty
    row = conn.execute("SELECT id FROM portfolio WHERE id=1").fetchone()
    if not row:
        conn.execute("INSERT INTO portfolio (id,cash,initial_capital) VALUES (1,?,?)",
                     (INITIAL_CAPITAL, INITIAL_CAPITAL))
    conn.commit()
    return conn

if __name__ == "__main__":
    conn = init_db()
    print("Database initialized.")
    rows = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    print(f"Watchlist: {rows} symbols")
    conn.close()
