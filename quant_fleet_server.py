#!/usr/bin/env python3
"""Quant Fleet Backend — Binance + SQLite + Pluggable Strategies"""

import http.server
import json
import math
import time
import os
import sys
import sqlite3
import importlib.util
import urllib.request
from datetime import datetime, timezone

# ============================================================
# CONFIG
# ============================================================
SYMBOLS = [
    "SOLUSDT","BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT",
    "ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","LINKUSDT",
    "MATICUSDT","UNIUSDT","ATOMUSDT","APTUSDT","ARBUSDT","OPUSDT"
]
SYMBOL_NAMES = {
    "SOL":"Solana","BTC":"Bitcoin","ETH":"Ethereum","BNB":"BNB",
    "XRP":"Ripple","ADA":"Cardano","DOGE":"Dogecoin","AVAX":"Avalanche",
    "DOT":"Polkadot","LINK":"Chainlink","MATIC":"Polygon","UNI":"Uniswap",
    "ATOM":"Cosmos","APT":"Aptos","ARB":"Arbitrum","OP":"Optimism"
}
BINANCE_BASE = "https://api.binance.com"
DB_PATH = "/root/quant_fleet.db"
STRATEGIES_DIR = "/root/strategies"
HTML_PATH = "/root/cyberpunk_dashboard.html"

# ============================================================
# SQLITE SETUP
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
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
            quantity REAL DEFAULT 0,
            status TEXT DEFAULT 'paper',
            strategy TEXT,
            signal_id INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            quantity REAL DEFAULT 0,
            current_price REAL,
            unrealized_pnl REAL DEFAULT 0,
            strategy TEXT,
            opened_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
        CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
        CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
        CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
    """)
    conn.commit()
    return conn

db_conn = init_db()
exec_log = []
log_lock = __import__('threading').Lock()

def add_log(ts, msg_type, html):
    with log_lock:
        exec_log.append({"ts":ts,"type":msg_type,"html":html})
        if len(exec_log)>200: exec_log.pop(0)

# ============================================================
# STRATEGY LOADER
# ============================================================
active_strategy = None
strategy_registry = {}

def load_strategies():
    global strategy_registry, active_strategy
    strategy_registry = {}
    if not os.path.isdir(STRATEGIES_DIR):
        return
    for fname in sorted(os.listdir(STRATEGIES_DIR)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        path = os.path.join(STRATEGIES_DIR, fname)
        try:
            spec = importlib.util.spec_from_file_location(fname[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            name = getattr(mod, 'NAME', fname[:-3].replace('_', ' ').title())
            desc = getattr(mod, 'DESCRIPTION', '')
            strategy_registry[fname] = {
                "filename": fname,
                "name": name,
                "description": desc,
                "module": mod
            }
            print(f"  [OK] Loaded strategy: {name} ({fname})")
        except Exception as e:
            print(f"  [FAIL] {fname}: {e}")

    # Set default active
    if not active_strategy and strategy_registry:
        first = list(strategy_registry.keys())[0]
        active_strategy = first
        print(f"  Active strategy: {strategy_registry[first]['name']}")

def get_active_strategy():
    if active_strategy and active_strategy in strategy_registry:
        return strategy_registry[active_strategy]
    return None

# ============================================================
# TECHNICAL INDICATORS
# ============================================================
def calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50.0
    gains=losses=0.0
    for i in range(1,period+1):
        diff=closes[-i]-closes[-i-1]
        if diff>0: gains+=diff
        else: losses-=diff
    avg_gain=gains/period; avg_loss=losses/period
    if avg_loss==0: return 100.0
    return 100.0-(100.0/(1.0+avg_gain/avg_loss))

def calc_sma(closes, period=20):
    if not closes: return 0
    if len(closes)<period: return sum(closes)/len(closes)
    return sum(closes[-period:])/period

def calc_ema(closes, period=12):
    if len(closes)<2: return closes[-1] if closes else 0
    mult=2.0/(period+1); ema=closes[0]
    for p in closes[1:]: ema=(p-ema)*mult+ema
    return ema

# ============================================================
# BINANCE FETCH
# ============================================================
def fetch_json(url):
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"QuantFleet/1.0"})
        with urllib.request.urlopen(req,timeout=10) as r:
            return json.loads(r.read().decode())
    except: return None

def fetch_all_data():
    tickers_raw = fetch_json(f"{BINANCE_BASE}/api/v3/ticker/24hr")
    if not tickers_raw: return None

    price_map={}
    for t in tickers_raw:
        price_map[t["symbol"]]={
            "price":float(t["lastPrice"]),"change_pct":float(t["priceChangePercent"]),
            "volume":float(t["quoteVolume"]),"high":float(t["highPrice"]),"low":float(t["lowPrice"])
        }

    strat = get_active_strategy()
    strategy_name = strat["name"] if strat else "none"
    result = {"tickers":[], "exec_log":[], "timestamp":datetime.now(timezone.utc).isoformat()}

    for symbol in SYMBOLS:
        sym = symbol.replace("USDT","")
        name = SYMBOL_NAMES.get(sym, sym)
        pm = price_map.get(symbol)
        if not pm: continue

        price = pm["price"]; change_pct = pm["change_pct"]; volume = pm["volume"]

        klines_1h = fetch_json(f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=30")
        klines_4h = fetch_json(f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval=4h&limit=30")

        closes_1h = [float(k[4]) for k in klines_1h] if klines_1h else []
        closes_4h = [float(k[4]) for k in klines_4h] if klines_4h else []

        rsi_1h = calc_rsi(closes_1h, 14)
        sma_4h = calc_sma(closes_4h, 20)
        sma_1h_20 = calc_sma(closes_1h, 20)
        ema_12 = calc_ema(closes_1h, 12) if closes_1h else price
        ema_26 = calc_ema(closes_1h, 26) if closes_1h else price
        vol_surge = volume > volume * 0.85 * 1.2  # simplified

        indicators = {
            "rsi_1h": round(rsi_1h,1), "sma_4h": sma_4h,
            "sma_1h_20": sma_1h_20, "ema_12": ema_12, "ema_26": ema_26,
            "vol_surge": vol_surge, "closes_1h": closes_1h, "closes_4h": closes_4h
        }

        signal = "HOLD"; confidence = 50
        factors_dict = {}

        if strat:
            try:
                out = strat["module"].evaluate(
                    {"id": sym, "name": name, "price": price, "volume": volume},
                    indicators
                )
                signal = out.get("signal", "HOLD")
                confidence = out.get("confidence", 50)
                factors_dict = out.get("factors", {})
            except Exception as e:
                add_log(datetime.now().strftime("%H:%M:%S"), "error",
                        f'Strategy error for {sym}: {e}')

        # Record signal to DB
        try:
            db_conn.execute(
                "INSERT INTO signals (symbol,signal,confidence,price,factors_json,strategy) VALUES (?,?,?,?,?,?)",
                (sym, signal, confidence, price, json.dumps(factors_dict), strategy_name)
            )
            db_conn.commit()
        except: pass

        sparkline = closes_1h[-18:] if len(closes_1h) >= 18 else closes_1h

        result["tickers"].append({
            "id": sym, "name": name, "price": price,
            "change_pct": change_pct, "volume_m": round(volume/1_000_000, 1),
            "signal": signal, "confidence": confidence,
            "sparkline": sparkline,
            "_rsi": round(rsi_1h,1), "_sma4h": round(sma_4h, price<1 and 4 or 2),
            "_vol_surge": vol_surge
        })

    # Log
    buys = sum(1 for t in result["tickers"] if t["signal"]=="BUY")
    sells = sum(1 for t in result["tickers"] if t["signal"]=="SELL")
    ts = datetime.now().strftime("%H:%M:%S")
    for t in result["tickers"]:
        if t["signal"] in ("BUY","SELL"):
            color="#00FF66" if t["signal"]=="BUY" else "#FF2A6D"
            result["exec_log"].append({
                "ts":ts,"type":t["signal"].lower(),
                "html":f'[{ts}] {t["id"]} → <span style="color:{color}">{t["signal"]}</span> conf={t["confidence"]}%'
            })
    result["exec_log"].insert(0,{
        "ts":ts,"type":"info",
        "html":f'[{ts}] SCAN [{strategy_name}] → BUY:{buys} SELL:{sells} | {len(result["tickers"])} pairs'
    })
    with log_lock:
        for e in result["exec_log"]: exec_log.append(e)
        while len(exec_log)>200: exec_log.pop(0)

    # Matrix / factors / KPI (unchanged)
    strategies_list = ["RSI","SMA CROSS","VOL SURGE","COMPOSITE"]
    timeframes_list = ["15m","1h","4h","1d"]
    cells = []
    for si, sn in enumerate(strategies_list):
        for ti, tf in enumerate(timeframes_list):
            active = (sn=="RSI" and tf=="1h") or (sn=="SMA CROSS" and tf=="4h") or \
                     (sn=="VOL SURGE" and tf=="1h") or sn=="COMPOSITE"
            cells.append([si, ti, "active" if active else "idle"])

    avg_rsi = sum(t["_rsi"] for t in result["tickers"])/max(len(result["tickers"]),1)
    price_above = sum(1 for t in result["tickers"] if t["price"]>t["_sma4h"])/max(len(result["tickers"]),1)
    vol_ratio = sum(1 for t in result["tickers"] if t.get("_vol_surge"))/max(len(result["tickers"]),1)

    factors = [
        {"label":"RSI(14)","value":min(1.0,(100-avg_rsi)/100)},
        {"label":"TREND","value":price_above},
        {"label":"VOLUME","value":vol_ratio},
        {"label":"MOMENTUM","value":min(1.0,buys/max(len(result["tickers"]),1)*3)},
        {"label":"BEARISH","value":min(1.0,sells/max(len(result["tickers"]),1)*3)},
        {"label":"NEUTRAL","value":min(1.0,(len(result["tickers"])-buys-sells)/max(len(result["tickers"]),1))},
    ]
    kpi = {"sharpe":round(1.5+(buys-sells)*0.1,2),"win_rate":round(55+buys*1.5,1),
           "pnl_day":(buys-sells)*120,"max_drawdown":round(3.0+sells*0.3,1),
           "aum":100000+(buys-sells)*2500}

    return {
        "tickers":result["tickers"],
        "strategy_matrix":{"strategies":strategies_list,"timeframes":timeframes_list,"cells":cells},
        "kpi":kpi,"factors":factors,
        "exec_log":list(exec_log[-50:]),
        "active_strategy": strategy_name,
        "order_flow":{}
    }

# ============================================================
# HTTP HANDLER
# ============================================================
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            data = fetch_all_data()
            if data is None:
                self._json(502, {"error":"Binance API unavailable"})
                return
            self._json(200, data)

        elif self.path == "/api/strategies":
            self._json(200, {
                "strategies": [
                    {"filename": v["filename"], "name": v["name"], "description": v["description"]}
                    for v in strategy_registry.values()
                ],
                "active": active_strategy
            })

        elif self.path == "/api/positions":
            try:
                rows = db_conn.execute(
                    "SELECT symbol,side,entry_price,quantity,current_price,unrealized_pnl,strategy,opened_at FROM positions ORDER BY opened_at DESC"
                ).fetchall()
                positions = []
                for r in rows:
                    positions.append({
                        "symbol":r[0],"side":r[1],"entry_price":r[2],"quantity":r[3],
                        "current_price":r[4],"unrealized_pnl":r[5],"strategy":r[6],"opened_at":r[7]
                    })
                self._json(200, {"positions": positions})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path == "/api/trades":
            limit = 50
            try:
                rows = db_conn.execute(
                    "SELECT symbol,side,price,quantity,status,strategy,created_at FROM trades ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
                trades = []
                for r in rows:
                    trades.append({
                        "symbol":r[0],"side":r[1],"price":r[2],"quantity":r[3],
                        "status":r[4],"strategy":r[5],"created_at":r[6]
                    })
                self._json(200, {"trades": trades})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path in ("/", "/index.html"):
            try:
                with open(HTML_PATH,"rb") as f: content=f.read()
                self.send_response(200)
                self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Content-Length",str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404)

        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/strategy/activate":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            fname = body.get("filename", "")
            if fname in strategy_registry:
                global active_strategy
                active_strategy = fname
                self._json(200, {"active": fname, "name": strategy_registry[fname]["name"]})
            else:
                self._json(400, {"error": f"Unknown strategy: {fname}"})

        elif self.path == "/api/trade/simulate":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            symbol = body.get("symbol","")
            side = body.get("side","BUY")
            price = body.get("price",0)
            qty = body.get("quantity",1)
            strategy = body.get("strategy","")
            if not symbol:
                self._json(400, {"error":"symbol required"}); return

            try:
                cur = db_conn.execute(
                    "INSERT INTO trades (symbol,side,price,quantity,strategy) VALUES (?,?,?,?,?)",
                    (symbol,side,price,qty,strategy)
                )
                trade_id = cur.lastrowid

                # Upsert position
                existing = db_conn.execute("SELECT id,quantity,entry_price FROM positions WHERE symbol=?",(symbol,)).fetchone()
                if existing:
                    new_qty = existing[1] + (qty if side=="BUY" else -qty)
                    if new_qty <= 0:
                        db_conn.execute("DELETE FROM positions WHERE symbol=?",(symbol,))
                    else:
                        avg_entry = (existing[2]*existing[1] + price*qty)/(existing[1]+qty) if side=="BUY" else existing[2]
                        db_conn.execute(
                            "UPDATE positions SET quantity=?,entry_price=?,current_price=?,unrealized_pnl=?,updated_at=datetime('now') WHERE symbol=?",
                            (new_qty, avg_entry, price, (price-avg_entry)*new_qty, symbol)
                        )
                else:
                    if side=="BUY":
                        db_conn.execute(
                            "INSERT INTO positions (symbol,side,entry_price,quantity,current_price,unrealized_pnl,strategy) VALUES (?,?,?,?,?,?,?)",
                            (symbol,side,price,qty,price,0.0,strategy)
                        )
                db_conn.commit()
                self._json(200, {"trade_id": trade_id, "status": "ok"})
            except Exception as e:
                self._json(500, {"error": str(e)})

        else:
            self.send_error(404)

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

def main():
    global db_conn
    port = 8899
    print(f"Quant Fleet server on http://localhost:{port}")
    print(f"Strategies dir: {STRATEGIES_DIR}")
    load_strategies()
    print(f"Symbols: {len(SYMBOLS)} pairs")
    server = http.server.HTTPServer(("0.0.0.0", port), Handler)
    try: server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        db_conn.close()
        server.shutdown()

if __name__ == "__main__":
    main()
