#!/usr/bin/env python3
"""Download historical daily klines from Binance Vision and store in SQLite."""

import os, sys, csv, io, zipfile, urllib.request, time, sqlite3
from datetime import datetime

BINANCE_VISION = "https://data.binance.vision/data/spot/monthly/klines"
SYMBOLS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","ADAUSDT","HYPERUSDT","LINKUSDT"]
START = (2025, 1)
END = (2026, 6)
INTERVAL = "1d"
DB_PATH = "/root/quant_fleet.db"
DATA_DIR = "/root/backtest_data"

os.makedirs(DATA_DIR, exist_ok=True)

def download_symbol(symbol):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS historical_klines (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL, UNIQUE(symbol, date))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_klines_symbol ON historical_klines(symbol)")
    conn.commit()

    existing = conn.execute("SELECT COUNT(*) FROM historical_klines WHERE symbol=?", (symbol,)).fetchone()[0]
    if existing > 300:
        print(f"  {symbol}: {existing} rows already in DB, skipping download")
        conn.close()
        return

    all_rows = []
    for y in range(START[0], END[0] + 1):
        m_start = START[1] if y == START[0] else 1
        m_end = END[1] if y == END[0] else 12
        for m in range(m_start, m_end + 1):
            fname = f"{symbol}-{INTERVAL}-{y}-{m:02d}.zip"
            url = f"{BINANCE_VISION}/{symbol}/{INTERVAL}/{fname}"
            local = os.path.join(DATA_DIR, fname)

            if not os.path.exists(local):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "QuantFleet/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        with open(local, "wb") as f:
                            f.write(r.read())
                    print(f"  {fname} downloaded")
                except Exception as e:
                    print(f"  ERROR: {fname} — {e}")
                    continue
                time.sleep(0.2)

            try:
                with zipfile.ZipFile(local) as z:
                    csv_name = z.namelist()[0]
                    with z.open(csv_name) as f:
                        reader = csv.reader(io.TextIOWrapper(f))
                        for row in reader:
                            ts = int(row[0]) // 1_000_000
                            date_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                            all_rows.append((symbol, date_str, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])))
            except Exception as e:
                print(f"  Parse error {fname}: {e}")

    if all_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO historical_klines (symbol,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
            all_rows
        )
        conn.commit()
        print(f"  {symbol}: stored {len(all_rows)} rows")

    conn.close()

def main():
    print(f"Downloading {len(SYMBOLS)} symbols: {', '.join(SYMBOLS)}")
    for sym in SYMBOLS:
        print(f"\n=== {sym} ===")
        download_symbol(sym)
    print("\nDone!")

if __name__ == "__main__":
    main()
