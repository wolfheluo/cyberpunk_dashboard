#!/usr/bin/env python3
"""Backtest Engine — download Binance daily klines, run all strategies, store equity curves."""

import os, sys, json, sqlite3, csv, io, zipfile, urllib.request, time, importlib.util
from datetime import datetime

BINANCE_VISION = "https://data.binance.vision/data/spot/monthly/klines"
SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT",
           "AVAXUSDT","DOTUSDT","LINKUSDT","MATICUSDT","UNIUSDT","ATOMUSDT","APTUSDT","ARBUSDT","OPUSDT"]
START_YEAR, START_MONTH = 2025, 1
END_YEAR, END_MONTH = 2026, 6
INTERVAL = "1d"
INITIAL_CAPITAL = 10_000.0
TRADE_SIZE_PCT = 0.05
DB_PATH = "/root/quant_fleet.db"
STRATEGIES_DIR = "/root/strategies"
DATA_DIR = "/root/backtest_data"

os.makedirs(DATA_DIR, exist_ok=True)

def download_klines(symbol):
    """Download monthly kline CSVs for a symbol, return merged closes dict {date: close}."""
    all_klines = {}
    for y in range(START_YEAR, END_YEAR + 1):
        m_start = START_MONTH if y == START_YEAR else 1
        m_end = END_MONTH if y == END_YEAR else 12
        for m in range(m_start, m_end + 1):
            fname = f"{symbol}-{INTERVAL}-{y}-{m:02d}.zip"
            url = f"{BINANCE_VISION}/{symbol}/{INTERVAL}/{fname}"
            local = os.path.join(DATA_DIR, fname)
            
            if not os.path.exists(local):
                try:
                    print(f"  Downloading {fname}...", end=" ", flush=True)
                    req = urllib.request.Request(url, headers={"User-Agent": "QuantFleet/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        with open(local, "wb") as f:
                            f.write(r.read())
                    print("OK")
                except Exception as e:
                    print(f"FAIL ({e})")
                    continue
                time.sleep(0.3)
            
            try:
                with zipfile.ZipFile(local) as z:
                    csv_name = z.namelist()[0]
                    with z.open(csv_name) as f:
                        reader = csv.reader(io.TextIOWrapper(f))
                        for row in reader:
                            # [open_time, open, high, low, close, volume, close_time, quote_vol, trades, taker_buy_vol, taker_buy_quote_vol, ignore]
                            ts = int(row[0]) // 1000
                            date_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                            all_klines[date_str] = {
                                "open": float(row[1]), "high": float(row[2]),
                                "low": float(row[3]), "close": float(row[4]),
                                "volume": float(row[5])
                            }
            except Exception as e:
                print(f"  Parse error {fname}: {e}")
    return all_klines


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        if diff > 0: gains += diff
        else: losses -= diff
    if losses == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + gains / losses))

def calc_sma(closes, period=20):
    if not closes: return 0
    return sum(closes[-min(len(closes), period):]) / min(len(closes), period)

def calc_ema(closes, period=12):
    if len(closes) < 2: return closes[-1] if closes else 0
    mult = 2.0 / (period + 1)
    ema = closes[0]
    for p in closes[1:]: ema = (p - ema) * mult + ema
    return ema


def load_strategies():
    reg = {}
    if not os.path.isdir(STRATEGIES_DIR): return reg
    for fname in sorted(os.listdir(STRATEGIES_DIR)):
        if not fname.endswith('.py') or fname.startswith('_'): continue
        path = os.path.join(STRATEGIES_DIR, fname)
        try:
            spec = importlib.util.spec_from_file_location(fname[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            name = getattr(mod, 'NAME', fname[:-3].replace('_', ' ').title())
            reg[fname] = {"filename": fname, "name": name, "module": mod}
        except Exception as e:
            print(f"  [SKIP] {fname}: {e}")
    return reg


def run_backtest(klines_data, strategy_mod):
    """Run strategy against daily klines. Returns {dates:[], equity:[], trades:[], final_equity:float}."""
    dates = sorted(klines_data.keys())
    closes_history = []
    equity_curve = []
    cash = INITIAL_CAPITAL
    positions = {}  # {symbol: {"qty": float, "entry": float}}
    trade_log = []
    warmup = 30  # days to skip for indicator warmup

    for i, date in enumerate(dates):
        k = klines_data[date]
        price = k["close"]
        closes_history.append(price)
        
        if i < warmup:
            equity_curve.append(cash)
            continue

        # Compute indicators from closes_history
        rsi = calc_rsi(closes_history, 14)
        sma20 = calc_sma(closes_history, 20)
        ema12 = calc_ema(closes_history, 12)
        ema26 = calc_ema(closes_history, 26)
        vol_surge = k["volume"] > 0  # simplified

        indicators = {
            "rsi_1h": rsi, "sma_4h": sma20, "sma_1h_20": sma20,
            "ema_12": ema12, "ema_26": ema26,
            "vol_surge": vol_surge, "closes_1h": closes_history[-30:],
            "closes_4h": closes_history[-30:]
        }

        ticker = {"id": "ASSET", "name": "Asset", "price": price, "volume": k["volume"]}

        try:
            out = strategy_mod.evaluate(ticker, indicators)
            signal = out.get("signal", "HOLD")
        except:
            signal = "HOLD"

        # Execute trades
        if signal == "BUY":
            notional = min(cash * TRADE_SIZE_PCT, cash)
            if notional >= 10:
                qty = notional / price
                cash -= notional
                if "ASSET" in positions:
                    p = positions["ASSET"]
                    new_qty = p["qty"] + qty
                    p["entry"] = (p["entry"] * p["qty"] + price * qty) / new_qty
                    p["qty"] = new_qty
                else:
                    positions["ASSET"] = {"qty": qty, "entry": price}
                trade_log.append({"date": date, "side": "BUY", "price": price, "qty": qty, "notional": notional})

        elif signal == "SELL" and "ASSET" in positions:
            p = positions["ASSET"]
            notional = p["qty"] * price
            cash += notional
            trade_log.append({"date": date, "side": "SELL", "price": price, "qty": p["qty"], "notional": notional})
            del positions["ASSET"]

        # Mark-to-market
        pos_value = sum(p["qty"] * price for p in positions.values())
        equity_curve.append(cash + pos_value)

    # Close any open position at last price
    if "ASSET" in positions and dates:
        last_price = klines_data[dates[-1]]["close"]
        p = positions["ASSET"]
        cash += p["qty"] * last_price
        del positions["ASSET"]

    final_equity = cash
    total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    
    # Sub-sample equity curve to ~200 points
    step = max(1, len(equity_curve) // 200)
    sampled = equity_curve[::step]
    sampled_dates = dates[warmup::step][:len(sampled)]

    return {
        "dates": sampled_dates,
        "equity": sampled,
        "trades": len(trade_log),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return, 2),
        "buy_count": sum(1 for t in trade_log if t["side"] == "BUY"),
        "sell_count": sum(1 for t in trade_log if t["side"] == "SELL"),
    }


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS backtests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            final_equity REAL,
            total_return_pct REAL,
            trades_count INTEGER,
            buy_count INTEGER,
            sell_count INTEGER,
            equity_curve TEXT,
            dates_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(symbol, strategy)
        );
    """)
    conn.commit()

    strategies = load_strategies()
    print(f"Strategies: {list(strategies.keys())}")
    print(f"Symbols: {len(SYMBOLS)}")

    for symbol in SYMBOLS[:3]:  # Start with top 3 for speed
        sym = symbol.replace("USDT", "")
        print()
        print(f"=== {symbol} ===")
        klines = download_klines(symbol)
        if not klines:
            print(f"  No data for {symbol}, skipping")
            continue
        print(f"  {len(klines)} days loaded")

        for fname, strat in strategies.items():
            print(f"  Backtesting {strat['name']}...", end=" ", flush=True)
            result = run_backtest(klines, strat["module"])
            print(f"Return: {result['total_return_pct']:+.1f}%  Equity: ${result['final_equity']:,.0f}  Trades: {result['trades']}")

            conn.execute(
                """INSERT OR REPLACE INTO backtests 
                   (symbol, strategy, final_equity, total_return_pct, trades_count, buy_count, sell_count, equity_curve, dates_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (sym, strat["name"], result["final_equity"], result["total_return_pct"],
                 result["trades"], result["buy_count"], result["sell_count"],
                 json.dumps(result["equity"]), json.dumps(result["dates"]))
            )
            conn.commit()

    conn.close()
    print()
    print("Done!")


if __name__ == "__main__":
    main()
