#!/usr/bin/env python3
"""Quant Fleet Backend — Binance + SQLite + Pluggable Strategies + Auto Paper Trading"""

import http.server
import json
import os
import re
import shutil
import sqlite3
import subprocess
import urllib.request
import threading
from datetime import datetime
from init_db import init_db, DB_PATH, INITIAL_CAPITAL

def _esc(s):
    """HTML-escape user-controlled strings before embedding in exec_log html."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def now_ts(fmt="%H:%M:%S"):
    return datetime.now().strftime(fmt)

# ============================================================
# CONFIG
# ============================================================
SYMBOLS, SYMBOL_NAMES = [], []
def reload_symbols():
    global SYMBOLS, SYMBOL_NAMES
    rows = db_conn.execute("SELECT symbol,name FROM watchlist ORDER BY id").fetchall()
    SYMBOLS = [r[0] for r in rows]
    SYMBOL_NAMES = {r[0].replace("USDT",""): r[1] for r in rows}
BINANCE_BASE = "https://api.binance.com"
_BASE = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
STRATEGIES_DIR = os.path.join(_BASE, "strategies")
HTML_PATH = os.path.join(_BASE, "dashboard", "cyberpunk_dashboard.html")
TRADE_SIZE_PCT = 0.05

# ============================================================
# SQLITE
# ============================================================
db_conn = init_db()
reload_symbols()
db_lock = threading.Lock()
exec_log = []
log_lock = threading.Lock()
active_strategy = "default.js"

def add_log(ts, msg_type, html):
    with log_lock:
        exec_log.append({"ts":ts,"type":msg_type,"html":html})
        if len(exec_log)>200: exec_log.pop(0)

# ============================================================
# JS STRATEGY HELPERS
# ============================================================
HAS_NODE = shutil.which("node") is not None

def _safe_strategy_name(fname):
    """Validate a strategy filename: basename only, [A-Za-z0-9_-]+.js. Raises ValueError."""
    base = os.path.basename(fname or "")
    if not re.fullmatch(r"[A-Za-z0-9_\-]+\.js", base):
        raise ValueError("Invalid strategy name")
    return base

def _strategy_meta(content):
    """Extract NAME/DESCRIPTION from a JS strategy file (object-literal style)."""
    name_m = re.search(r'NAME\s*[:=]\s*"([^"]+)"', content)
    desc_m = re.search(r'DESCRIPTION\s*[:=]\s*"([^"]+)"', content)
    return (name_m.group(1) if name_m else None), (desc_m.group(1) if desc_m else "")

def list_js_strategies():
    """Return [{filename, name, description}] for all .js strategy files."""
    result = []
    if os.path.isdir(STRATEGIES_DIR):
        for fname in sorted(os.listdir(STRATEGIES_DIR)):
            if not fname.endswith('.js') or fname.startswith('_'):
                continue
            path = os.path.join(STRATEGIES_DIR, fname)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            name, desc = _strategy_meta(content)
            result.append({
                "filename": fname,
                "name": name or fname.replace('.js', '').replace('_', ' ').title(),
                "description": desc or ""
            })
    return result

def run_js_strategy(strategy_file, ticker_infos):
    """Evaluate a JS strategy for all tickers via a node subprocess.

    ticker_infos: [{"id": "BTC", "ticker": {...}, "indicators": {...}}, ...]
    Returns {symbol: {"signal","confidence","factors"}} — empty dict on any failure.
    """
    if not HAS_NODE or not ticker_infos:
        return {}
    helper = os.path.join(STRATEGIES_DIR, "_run_strategy.js")
    payload = json.dumps({"strategy": strategy_file, "tickers": ticker_infos})
    try:
        proc = subprocess.run(["node", helper], input=payload, capture_output=True,
                              text=True, timeout=15)
        out = proc.stdout.strip()
        if proc.returncode != 0 or not out:
            return {}
        data = json.loads(out)
        if "error" in data:
            return {}
        return data
    except Exception:
        return {}

def run_js_backtests(strategy_file, symbols_klines):
    """Run a JS strategy against historical klines for many symbols in one node call.

    symbols_klines: {symbol: [{"date","open","high","low","close","volume"}, ...]}
    Returns {symbol: backtest_result} — empty dict on failure.
    """
    if not HAS_NODE or not symbols_klines:
        return {}
    helper = os.path.join(STRATEGIES_DIR, "_run_backtest.js")
    payload = json.dumps({"strategy": strategy_file, "symbols": symbols_klines})
    try:
        proc = subprocess.run(["node", helper], input=payload, capture_output=True,
                              text=True, timeout=120)
        out = proc.stdout.strip()
        if proc.returncode != 0 or not out:
            return {}
        data = json.loads(out)
        if isinstance(data, dict) and "error" in data:
            return {}
        return data
    except Exception:
        return {}

# ============================================================
# INDICATORS
# ============================================================
def calc_rsi(closes, period=14):
    if len(closes)<period+1: return 50.0
    gains=losses=0.0
    for i in range(1,period+1):
        diff=closes[-i]-closes[-i-1]
        if diff>0: gains+=diff
        else: losses-=diff
    if losses==0: return 100.0
    return 100.0-(100.0/(1.0+gains/losses))

def calc_sma(closes, period=20):
    if not closes: return 0
    return sum(closes[-period:])/min(len(closes),period)

def calc_ema(closes, period=12):
    if len(closes)<2: return closes[-1] if closes else 0
    mult=2.0/(period+1); ema=closes[0]
    for p in closes[1:]: ema=(p-ema)*mult+ema
    return ema

def _ema_series(closes, period):
    """Full EMA series (seed = first close)."""
    if not closes: return []
    mult = 2.0/(period+1)
    out = [closes[0]]
    for p in closes[1:]:
        out.append((p-out[-1])*mult+out[-1])
    return out

def calc_macd(closes, fast=12, slow=26, signal=9):
    """Return (macd_line, macd_signal, macd_hist) — 0s when series too short."""
    if len(closes) < slow:
        return 0.0, 0.0, 0.0
    ef = _ema_series(closes, fast)
    es = _ema_series(closes, slow)
    macd_series = [f-s for f, s in zip(ef, es)]
    sig_series = _ema_series(macd_series, signal)
    line = macd_series[-1]
    sig = sig_series[-1]
    return line, sig, line - sig

def calc_bollinger(closes, period=20, k=2.0):
    """Return (upper, middle, lower) — middle (or last close) when too short."""
    n = min(len(closes), period)
    if n < 2:
        last = closes[-1] if closes else 0
        return last, last, last
    window = closes[-n:]
    mid = sum(window)/n
    var = sum((x-mid)**2 for x in window)/n
    sd = var ** 0.5
    return mid + k*sd, mid, mid - k*sd

def calc_atr(klines, period=14):
    """ATR(period) from Binance kline arrays [[open,high,low,close,...], ...] — 0 when too short."""
    if len(klines) < period+1:
        return 0.0
    trs = []
    for i in range(1, len(klines)):
        h = float(klines[i][2]); l = float(klines[i][3]); pc = float(klines[i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-period:])/period

# ============================================================
# BINANCE
# ============================================================
def fetch_json(url):
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"QuantFleet/1.0"})
        with urllib.request.urlopen(req,timeout=10) as r:
            return json.loads(r.read().decode())
    except: return None

# ============================================================
# TRADE EXECUTION
# ============================================================
def execute_trade(symbol, side, price, strategy_name, signal_id=None):
    """Execute a paper trade: deduct cash, record trade, update position."""
    with db_lock:
        portfolio = db_conn.execute("SELECT cash FROM portfolio WHERE id=1").fetchone()
        if not portfolio: return None
        cash = portfolio[0]

        # Determine trade size
        if side == "BUY":
            notional = min(cash * TRADE_SIZE_PCT, cash)
            if notional < 10: return None  # min $10
            quantity = notional / price
        else:  # SELL
            pos = db_conn.execute("SELECT quantity FROM positions WHERE symbol=?",(symbol,)).fetchone()
            if not pos or pos[0] <= 0: return None
            quantity = pos[0]
            notional = quantity * price

        # Record trade
        cur = db_conn.execute(
            "INSERT INTO trades (symbol,side,price,quantity,notional,strategy,signal_id) VALUES (?,?,?,?,?,?,?)",
            (symbol, side, price, round(quantity, 8), round(notional, 2), strategy_name, signal_id)
        )
        trade_id = cur.lastrowid

        # Update cash
        cash_change = -notional if side == "BUY" else notional
        db_conn.execute("UPDATE portfolio SET cash=cash+?, updated_at=datetime('now', '+8 hours') WHERE id=1",
                        (cash_change,))

        # Update position
        existing = db_conn.execute("SELECT id,quantity,entry_price FROM positions WHERE symbol=?",(symbol,)).fetchone()
        if side == "BUY":
            if existing:
                new_qty = existing[1] + quantity
                avg_entry = (existing[2]*existing[1] + price*quantity) / new_qty
                db_conn.execute("UPDATE positions SET quantity=?,entry_price=?,current_price=?,unrealized_pnl=?,updated_at=datetime('now', '+8 hours') WHERE symbol=?",
                               (new_qty, avg_entry, price, (price-avg_entry)*new_qty, symbol))
            else:
                db_conn.execute("INSERT INTO positions (symbol,side,entry_price,quantity,current_price,unrealized_pnl,strategy) VALUES (?,?,?,?,?,?,?)",
                               (symbol, side, price, quantity, price, 0.0, strategy_name))
        else:  # SELL
            if existing and existing[1] - quantity <= 0.00001:
                db_conn.execute("DELETE FROM positions WHERE symbol=?",(symbol,))

        db_conn.commit()
        return {"trade_id": trade_id, "quantity": round(quantity, 8), "notional": round(notional, 2)}

def portfolio_stats(initial_capital=INITIAL_CAPITAL):
    """FIFO-based realized PnL + equity curve from trade history (real metrics, no fabricated values).

    Returns (win_rate, sharpe, max_drawdown) — each None when there is not enough data.
    """
    rows = db_conn.execute("SELECT symbol,side,price,quantity FROM trades ORDER BY id ASC").fetchall()
    if not rows:
        return None, None, None
    basis = {}  # symbol -> list of [remaining_qty, price]
    last_price = {}  # symbol -> most recent trade price (per-symbol MTM)
    cash = initial_capital
    equity = []
    wins = losses = 0
    for sym, side, price, qty in rows:
        last_price[sym] = price
        if side == "BUY":
            cash -= qty * price
            basis.setdefault(sym, []).append([qty, price])
        else:
            cash += qty * price
            remaining = qty
            realized = 0.0
            for lot in basis.get(sym, []):
                if remaining <= 0:
                    break
                take = min(lot[0], remaining)
                realized += take * (price - lot[1])
                lot[0] -= take
                remaining -= take
            basis[sym] = [l for l in basis[sym] if l[0] > 0]
            if realized > 0:
                wins += 1
            elif realized < 0:
                losses += 1
        mtm = sum(lot[0] * last_price.get(s, price) for s, lots in basis.items() for lot in lots)
        equity.append(cash + mtm)

    win_rate = (wins / (wins + losses) * 100) if (wins + losses) else None

    sharpe = max_dd = None
    if len(equity) >= 3:
        returns = [(equity[i] - equity[i-1]) / equity[i-1] for i in range(1, len(equity)) if equity[i-1] != 0]
        if len(returns) >= 2:
            mean = sum(returns) / len(returns)
            var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
            std = var ** 0.5
            if std > 0:
                sharpe = round(mean / std * (len(returns) ** 0.5), 2)
        peak = equity[0]
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100 if peak else 0
            max_dd = dd if max_dd is None or dd > max_dd else max_dd
        max_dd = round(max_dd, 1) if max_dd is not None else None
    return win_rate, sharpe, max_dd

# ============================================================
# MAIN DATA FETCH
# ============================================================
def fetch_all_data():
    tickers_raw = fetch_json(f"{BINANCE_BASE}/api/v3/ticker/24hr")
    if not tickers_raw: return None

    price_map = {}
    for t in tickers_raw:
        price_map[t["symbol"]] = {
            "price":float(t["lastPrice"]),"change_pct":float(t["priceChangePercent"]),
            "volume":float(t["quoteVolume"]),"high":float(t["highPrice"]),"low":float(t["lowPrice"])
        }

    strategy_name = active_strategy.replace(".js","").replace("_"," ").title() if active_strategy else "none"
    result = {"tickers":[], "exec_log":[], "timestamp":datetime.now().isoformat(), "rejected":0, "failed":0}

    # Get current portfolio
    with db_lock:
        pf = db_conn.execute("SELECT cash FROM portfolio WHERE id=1").fetchone()
        cash = pf[0] if pf else INITIAL_CAPITAL
        positions_map = {}
        for r in db_conn.execute("SELECT symbol,quantity,entry_price FROM positions"):
            positions_map[r[0]] = {"quantity":r[1],"entry_price":r[2]}

    # Re-mark open positions with current prices (positions would otherwise be stale)
    with db_lock:
        for r in db_conn.execute("SELECT symbol,quantity,entry_price FROM positions").fetchall():
            pm = price_map.get(r[0] + "USDT")
            if pm:
                db_conn.execute(
                    "UPDATE positions SET current_price=?, unrealized_pnl=?, updated_at=datetime('now', '+8 hours') WHERE symbol=?",
                    (pm["price"], (pm["price"] - r[2]) * r[1], r[0]))
        db_conn.commit()

    # Record prices for historical tracking (every 5 min to avoid bloat)
    with db_lock:
        last_rec = db_conn.execute("SELECT MAX(recorded_at) FROM prices").fetchone()[0]
        should_record = not last_rec or (datetime.now() - datetime.fromisoformat(last_rec)).total_seconds() > 300

    # Pass 1: build ticker + indicators for every watchlist symbol, then evaluate
    # the active JS strategy ONCE via node subprocess (no more hardcoded HOLD).
    book_map = {}
    bt_raw = fetch_json(f"{BINANCE_BASE}/api/v3/ticker/bookTicker")
    if bt_raw:
        for b in bt_raw:
            book_map[b["symbol"]] = b

    ticker_infos = []
    for symbol in SYMBOLS:
        sym = symbol.replace("USDT", "")
        pm = price_map.get(symbol)
        if not pm:
            continue
        price = pm["price"]; change_pct = pm["change_pct"]; volume = pm["volume"]
        high = pm["high"]; low = pm["low"]
        name = SYMBOL_NAMES.get(sym, sym)

        klines_1h = fetch_json(f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=100")
        klines_4h = fetch_json(f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval=4h&limit=100")
        closes_1h = [float(k[4]) for k in klines_1h] if klines_1h else []
        closes_4h = [float(k[4]) for k in klines_4h] if klines_4h else []
        if should_record and pm:
            try:
                with db_lock:
                    db_conn.execute("INSERT INTO prices (symbol, price) VALUES (?, ?)", (sym, price))
                    db_conn.commit()
            except Exception:
                pass

        vol_surge = len(closes_1h) >= 2 and volume > (sum(float(k[5]) for k in (klines_1h or [])[-10:]) / max(len(klines_1h[-10:]), 1)) * 1.5
        macd_line, macd_signal, macd_hist = calc_macd(closes_1h)
        bb_up, bb_mid, bb_low = calc_bollinger(closes_1h)

        b = book_map.get(symbol)
        if b:
            bid = float(b["bidPrice"]); ask = float(b["askPrice"])
            bq = float(b["bidQty"]); aq = float(b["askQty"])
            mid = (bid + ask) / 2
            book = {"best_bid": bid, "best_ask": ask, "bid_qty": bq, "ask_qty": aq,
                    "spread_pct": round((ask - bid) / mid * 100, 4) if mid else 0,
                    "imbalance": round((bq - aq) / (bq + aq), 4) if (bq + aq) else 0}
        else:
            book = None

        ticker_infos.append({
            "id": sym,
            "ticker": {
                "id": sym, "name": name, "price": price, "volume": volume, "change_pct": change_pct,
                "high_24h": high, "low_24h": low,
                "pct_from_high": round((price - high) / high * 100, 3) if high else 0,
                "pct_from_low": round((price - low) / low * 100, 3) if low else 0,
                "book": book
            },
            "indicators": {
                "rsi": round(calc_rsi(closes_1h, 14), 1),
                "sma20": calc_sma(closes_1h, 20),
                "sma50": calc_sma(closes_1h, 50),
                "ema12": calc_ema(closes_1h, 12) if closes_1h else price,
                "ema26": calc_ema(closes_1h, 26) if closes_1h else price,
                "ema50": calc_ema(closes_1h, 50) if closes_1h else price,
                "macd_line": round(macd_line, 6),
                "macd_signal": round(macd_signal, 6),
                "macd_hist": round(macd_hist, 6),
                "bb_upper": round(bb_up, 4),
                "bb_middle": round(bb_mid, 4),
                "bb_lower": round(bb_low, 4),
                "atr14": round(calc_atr(klines_1h or [], 14), 4),
                "rsi_4h": round(calc_rsi(closes_4h, 14), 1),
                "sma_4h": calc_sma(closes_4h, 20),
                "volSurge": vol_surge,
                "closes": closes_1h
            }
        })

    signals_map = run_js_strategy(active_strategy, ticker_infos) or {}

    # Pass 2: record signals, auto-execute trades, build ticker rows
    for info in ticker_infos:
        sym = info["id"]
        name = info["ticker"]["name"]
        pm = price_map.get(sym + "USDT")
        price = pm["price"]; change_pct = pm["change_pct"]; volume = pm["volume"]
        closes_1h = info["indicators"]["closes"]

        sig = signals_map.get(sym) or {}
        signal = sig.get("signal", "HOLD") or "HOLD"
        confidence = sig.get("confidence", 50) or 50
        factors_dict = sig.get("factors") or {}

        # Record signal (skip HOLD rows — otherwise the table grows ~120k rows/day)
        signal_id = None
        if signal != "HOLD":
            with db_lock:
                cur = db_conn.execute(
                    "INSERT INTO signals (symbol,signal,confidence,price,factors_json,strategy) VALUES (?,?,?,?,?,?)",
                    (sym, signal, confidence, price, json.dumps(factors_dict), strategy_name))
                signal_id = cur.lastrowid
                db_conn.commit()

        # Auto-execute trade on BUY/SELL
        current_pos = positions_map.get(sym)
        trade_result = None
        if signal == "BUY" and (not current_pos):
            trade_result = execute_trade(sym, "BUY", price, strategy_name, signal_id)
        elif signal == "SELL" and current_pos:
            trade_result = execute_trade(sym, "SELL", price, strategy_name, signal_id)
        if trade_result is None and signal == "BUY":
            result["rejected"] = result.get("rejected", 0) + 1
        elif trade_result is None and signal == "SELL":
            result["failed"] = result.get("failed", 0) + 1

        sparkline = closes_1h[-18:] if len(closes_1h) >= 18 else closes_1h
        result["tickers"].append({
            "id": sym, "name": name, "price": price, "change_pct": change_pct,
            "volume_m": round(volume / 1_000_000, 1), "signal": signal, "confidence": confidence,
            "sparkline": sparkline,
            "_rsi": round(info["indicators"]["rsi"], 1),
            "_sma4h": round(info["indicators"]["sma20"], price < 1 and 4 or 2),
            "_vol_surge": info["indicators"]["volSurge"]
        })

    # ---- Build logs ----
    ts = now_ts()
    buys=sells=0

    # Re-read positions & trades after execution
    with db_lock:
        updated_positions = db_conn.execute("SELECT symbol,side,quantity,entry_price FROM positions").fetchall()
        pf = db_conn.execute("SELECT cash FROM portfolio WHERE id=1").fetchone()
        cash = pf[0] if pf else INITIAL_CAPITAL

    positions_map2 = {r[0]:{"side":r[1],"qty":r[2],"entry":r[3]} for r in updated_positions}

    for t in result["tickers"]:
        if t["signal"]=="BUY": buys+=1
        elif t["signal"]=="SELL": sells+=1
        pos = positions_map2.get(t["id"])
        if t["signal"] in ("BUY","SELL"):
            color = "#00FF66" if t["signal"]=="BUY" else "#FF2A6D"
            pos_info = ""
            if pos: pos_info = f' | POS: {pos["qty"]:.4f} @${pos["entry"]:.2f}'
            result["exec_log"].append({
                "ts":ts,"type":t["signal"].lower(),
                "html":f'[{ts}] {_esc(t["id"])} → <span style="color:{color}">{t["signal"]}</span> conf={t["confidence"]}%{pos_info}'
            })

    # Portfolio summary
    pos_value = sum(pos["qty"]*(price_map.get(pos_sym+"USDT",{}).get("price",0) or 0)
                    for pos_sym, pos in positions_map2.items())
    total_equity = cash + pos_value
    pnl = total_equity - INITIAL_CAPITAL
    pnl_color = "#00FF66" if pnl>=0 else "#FF2A6D"
    pnl_sign = "+" if pnl>=0 else ""

    result["exec_log"].insert(0,{
        "ts":ts,"type":"info",
        "html":f'[{ts}] SCAN [{strategy_name}] → BUY:{buys} SELL:{sells} | Cash: ${cash:,.0f} | Equity: ${total_equity:,.0f} | PnL: <span style="color:{pnl_color}">{pnl_sign}${pnl:,.0f}</span>'
    })

    with log_lock:
        for e in result["exec_log"]: exec_log.append(e)
        while len(exec_log)>200: exec_log.pop(0)

    # ---- KPI / Factors ----
    strategy_names = [s["name"] for s in list_js_strategies()][:4]
    timeframes_list = ["15m","1h","4h","1d"]
    cells=[]
    for si,sn in enumerate(strategy_names):
        for ti,tf in enumerate(timeframes_list):
            active = (si == 0)  # first strategy active on all timeframes
            cells.append([si,ti,"active" if active else "idle"])

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

    # Real portfolio KPI (None → frontend shows "--")
    win_rate, sharpe, max_dd = portfolio_stats(INITIAL_CAPITAL)

    kpi = {
        "sharpe": sharpe,
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "pnl_day": round(pnl, 0),
        "max_drawdown": max_dd,
        "aum": round(total_equity, 0)
    }

    return {
        "tickers":result["tickers"],
        "strategy_matrix":{"strategies":strategy_names,"timeframes":timeframes_list,"cells":cells},
        "kpi":kpi,"factors":factors,
        "exec_log":list(exec_log[-50:]),
        "active_strategy":strategy_name,"order_flow":{},"rejected":result.get("rejected",0),"failed":result.get("failed",0)
    }

# ============================================================
# HTTP
# ============================================================
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path=="/api/data":
            data = fetch_all_data()
            self._json(502 if not data else 200, data or {"error":"Binance down"})
        elif self.path=="/api/strategies":
            self._json(200,{
                "strategies":list_js_strategies(),
                "active":active_strategy
            })
        elif self.path=="/api/positions":
            rows = db_conn.execute("SELECT symbol,side,entry_price,quantity,current_price,unrealized_pnl,strategy,opened_at FROM positions ORDER BY opened_at DESC").fetchall()
            self._json(200,{"positions":[{"symbol":r[0],"side":r[1],"entry_price":r[2],"quantity":r[3],"current_price":r[4],"unrealized_pnl":r[5],"strategy":r[6],"opened_at":r[7]} for r in rows]})
        elif self.path=="/api/trades":
            rows = db_conn.execute("SELECT symbol,side,price,quantity,notional,status,strategy,created_at FROM trades ORDER BY created_at DESC LIMIT 200").fetchall()
            self._json(200,{"trades":[{"symbol":r[0],"side":r[1],"price":r[2],"quantity":r[3],"notional":r[4],"status":r[5],"strategy":r[6],"created_at":r[7]} for r in rows]})
        elif self.path=="/api/portfolio":
            pf = db_conn.execute("SELECT cash,initial_capital FROM portfolio WHERE id=1").fetchone()
            pos_val = sum(r[2]*(r[0] if r[0] else r[1]) for r in db_conn.execute("SELECT current_price,entry_price,quantity FROM positions").fetchall())
            self._json(200,{"cash":pf[0],"initial_capital":pf[1],"position_value":pos_val,"total_equity":pf[0]+pos_val})
        elif self.path == "/api/symbols":
            rows = db_conn.execute("SELECT id,symbol,name FROM watchlist ORDER BY id").fetchall()
            self._json(200, {"symbols":[{"id":r[0],"symbol":r[1],"name":r[2]} for r in rows]})
        elif self.path.startswith("/dashboard/i18n/") or self.path.startswith("/i18n/"):
            fname = self.path.replace("/dashboard/i18n/", "").replace("/i18n/", "")
            path = os.path.join(_BASE, "dashboard", "i18n", fname)
            if os.path.isfile(path):
                with open(path, "rb") as f: content = f.read()
                self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(content))); self.end_headers(); self.wfile.write(content)
            else: self.send_error(404)
        elif self.path.startswith("/dashboard/"):
            fpath = os.path.join(_BASE, self.path.lstrip("/"))
            if os.path.isfile(fpath):
                ct = "text/css" if fpath.endswith(".css") else "application/javascript" if fpath.endswith(".js") else "text/plain"
                with open(fpath, "rb") as f: content = f.read()
                self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)
            else: self.send_error(404)
        elif self.path in ("/","/index.html"):
            try:
                with open(HTML_PATH,"rb") as f: content=f.read()
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(content))); self.end_headers(); self.wfile.write(content)
            except: self.send_error(404)
        elif self.path.startswith("/api/strategy/") and self.path.endswith("/code"):
            try:
                fname = _safe_strategy_name(self.path.split("/api/strategy/")[1].replace("/code", ""))
            except ValueError:
                self._json(400, {"error": "Invalid strategy name"}); return
            path = os.path.join(STRATEGIES_DIR, fname)
            if not os.path.isfile(path):
                self._json(404, {"error": "Strategy not found"})
            else:
                with open(path, encoding="utf-8") as f: content = f.read()
                name, desc = _strategy_meta(content)
                self._json(200, {"filename": fname, "code": content,
                    "name": name or fname,
                    "description": desc or ""})

        elif self.path == "/api/params/ref":
            ref = (
                "parameter,type,description,example\n"
                "ticker.id,string,Symbol (e.g. BTC),BTC\n"
                "ticker.name,string,Full name (e.g. Bitcoin),Bitcoin\n"
                "ticker.price,number,Current price in USDT,65100.50\n"
                "ticker.volume,number,24h quote volume,1500000000\n"
                "ticker.change_pct,number,24h price change %,2.35\n"
                "ticker.high_24h,number,24h high price,65500.00\n"
                "ticker.low_24h,number,24h low price,64000.00\n"
                "ticker.pct_from_high,number,% below 24h high,-0.61\n"
                "ticker.pct_from_low,number,% above 24h low,1.72\n"
                "ticker.book.best_bid,number,Best bid price,65100.10\n"
                "ticker.book.best_ask,number,Best ask price,65100.50\n"
                "ticker.book.bid_qty,number,Best bid quantity,0.85\n"
                "ticker.book.ask_qty,number,Best ask quantity,1.20\n"
                "ticker.book.spread_pct,number,Spread as % of mid,0.0006\n"
                "ticker.book.imbalance,number,(bid_qty-ask_qty)/(bid_qty+ask_qty) -1..1,0.17\n"
                "indicators.rsi,number,RSI(14) on 1h closes 0-100,45.2\n"
                "indicators.sma20,number,SMA(20) on 1h closes,65050.10\n"
                "indicators.sma50,number,SMA(50) on 1h closes,64800.30\n"
                "indicators.ema12,number,EMA(12) on 1h closes,65120.00\n"
                "indicators.ema26,number,EMA(26) on 1h closes,65080.50\n"
                "indicators.ema50,number,EMA(50) on 1h closes,64950.20\n"
                "indicators.macd_line,number,MACD line (12/26) on 1h closes,12.35\n"
                "indicators.macd_signal,number,MACD signal line (EMA9 of MACD),10.10\n"
                "indicators.macd_hist,number,MACD histogram (line-signal),2.25\n"
                "indicators.bb_upper,number,Bollinger upper (20,2σ),65800.00\n"
                "indicators.bb_middle,number,Bollinger middle (SMA20),65050.10\n"
                "indicators.bb_lower,number,Bollinger lower (20,2σ),64300.20\n"
                "indicators.atr14,number,ATR(14) on 1h closes,420.5\n"
                "indicators.rsi_4h,number,RSI(14) on 4h closes,52.1\n"
                "indicators.sma_4h,number,SMA(20) on 4h closes,64980.00\n"
                "indicators.volSurge,boolean,Volume > 1.5x recent 1h average,true\n"
                "indicators.closes,array[number],Last 100 1h close prices,[65100,65050,...]\n"
                "return.signal,string,BUY|SELL|HOLD|WAIT,BUY\n"
                "return.confidence,number,0-100,82\n"
                "return.factors,object,Optional factor values for logging,{rsi:45.2}\n"
            )
            body = ref.encode()
            self.send_response(200)
            self.send_header("Content-Type","text/csv; charset=utf-8")
            self.send_header("Content-Disposition","attachment; filename=strategy_params.csv")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path=="/api/strategy/activate":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            try:
                fname = _safe_strategy_name(body.get("filename",""))
            except ValueError:
                self._json(400, {"error": "Invalid strategy name"}); return
            path = os.path.join(STRATEGIES_DIR, fname)
            if os.path.isfile(path):
                global active_strategy; active_strategy=fname
                with open(path, encoding="utf-8") as f:
                    name, _ = _strategy_meta(f.read())
                self._json(200,{"active":fname,"name":name or fname})
            else: self._json(400,{"error":f"Unknown: {fname}"})
        elif self.path=="/api/trade/simulate":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            sym=body.get("symbol",""); side=body.get("side","BUY"); price=body.get("price",0)
            if not sym: self._json(400,{"error":"symbol required"}); return
            r = execute_trade(sym,side,price,body.get("strategy","manual"))
            self._json(200,r if r else {"error":"Insufficient funds or no position"})
        elif self.path=="/api/reset":
            with db_lock:
                db_conn.execute("DELETE FROM trades");
                db_conn.execute("DELETE FROM positions");
                db_conn.execute("DELETE FROM signals");
                db_conn.execute("UPDATE portfolio SET cash=?,updated_at=datetime('now', '+8 hours') WHERE id=1", (INITIAL_CAPITAL,))
                db_conn.commit()
            with log_lock: exec_log.clear()
            self._json(200,{"status":"reset","capital":INITIAL_CAPITAL})
        elif self.path == "/api/strategy/create":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            try:
                fname = _safe_strategy_name(body.get("filename","").strip() or "new_strategy.js")
            except ValueError:
                self._json(400, {"error": "Strategy name must be [A-Za-z0-9_-].js"}); return
            path = os.path.join(STRATEGIES_DIR, fname)
            if os.path.isfile(path):
                self._json(400, {"error":"Strategy already exists"})
            else:
                template = body.get("code",
                    '({\n  NAME: "New Strategy",\n  DESCRIPTION: "",\n  evaluate: function(ticker, indicators) {\n    return {signal: "HOLD", confidence: 50};\n  }\n})')
                with open(path, "w", encoding="utf-8") as f: f.write(template)
                name, _ = _strategy_meta(template)
                self._json(200, {"status":"created","filename":fname,"name":name or fname})
        elif self.path.startswith("/api/strategy/") and self.path.endswith("/delete"):
            try:
                fname = _safe_strategy_name(self.path.split("/api/strategy/")[1].replace("/delete", ""))
            except ValueError:
                self._json(400, {"error": "Invalid strategy name"}); return
            path = os.path.join(STRATEGIES_DIR, fname)
            if not os.path.isfile(path):
                self._json(404, {"error":"Strategy not found"})
            else:
                os.remove(path)
                if active_strategy == fname:
                    active_strategy = "default.js"
                self._json(200, {"status":"deleted","filename":fname})
        elif self.path.startswith("/api/strategy/") and self.path.endswith("/save"):
            try:
                fname = _safe_strategy_name(self.path.split("/api/strategy/")[1].replace("/save", ""))
            except ValueError:
                self._json(400, {"error": "Invalid strategy name"}); return
            path = os.path.join(STRATEGIES_DIR, fname)
            if not os.path.isfile(path):
                self._json(404, {"error": "Strategy not found"})
            else:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                new_code = body.get("code", "")
                if new_code:
                    with open(path, "w", encoding="utf-8") as f: f.write(new_code)
                    name, _ = _strategy_meta(new_code)
                    self._json(200, {"name": name or fname, "filename": fname})
                else:
                    self._json(400, {"error": "No code provided"})

        elif self.path == "/api/backtest/run":
            # Run backtest on-the-fly: evaluate all JS strategies against all
            # symbols' historical klines via node subprocess (one call per strategy).
            symbols_in_db = db_conn.execute("SELECT DISTINCT symbol FROM historical_klines").fetchall()
            if not symbols_in_db:
                self._json(400, {"error": "No historical data. Run: python3 init_db.py"})
                return
            if not HAS_NODE:
                self._json(400, {"error": "Node.js not found — required for JS backtest engine"})
                return

            symbols_klines = {}
            for (sym,) in symbols_in_db:
                rows = db_conn.execute("SELECT date,open,high,low,close,volume FROM historical_klines WHERE symbol=? ORDER BY date", (sym,)).fetchall()
                if not rows:
                    continue
                symbols_klines[sym] = [{"date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in rows]

            results = []
            for strat_info in list_js_strategies():
                bt_map = run_js_backtests(strat_info["filename"], symbols_klines)
                for sym, bt in bt_map.items():
                    bt = dict(bt)
                    bt.update({"symbol": sym, "strategy": strat_info["name"]})
                    results.append(bt)

            self._json(200, {"backtests": results, "count": len(results)})

        elif self.path == "/api/symbols/add":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            sym = body.get("symbol","").upper().strip()
            name = body.get("name", sym.replace("USDT",""))
            if not sym or not sym.endswith("USDT"):
                self._json(400, {"error":"Symbol must end with USDT (e.g. DOGEUSDT)"}); return
            db_conn.execute("INSERT OR IGNORE INTO watchlist (symbol,name) VALUES (?,?)", (sym,name))
            db_conn.commit(); reload_symbols()
            self._json(200, {"status":"added","symbol":sym,"name":name})
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/symbols/"):
            sym = self.path.split("/api/symbols/")[1].upper()
            if not re.fullmatch(r"[A-Z0-9]+USDT", sym):
                self._json(400, {"error": "Invalid symbol"}); return
            db_conn.execute("DELETE FROM watchlist WHERE symbol=?", (sym,))
            db_conn.commit(); reload_symbols()
            self._json(200, {"status":"deleted","symbol":sym})
        else:
            self.send_error(405)

    def _json(self,code,data):
        body=json.dumps(data).encode()
        self.send_response(code); self.send_header("Content-Type","application/json"); self.send_header("Access-Control-Allow-Origin","*"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,f,*a): pass

def main():
    port=8899
    print(f"Quant Fleet on http://localhost:{port}")
    print(f"Initial capital: ${INITIAL_CAPITAL:,.0f}")
    if HAS_NODE:
        print(f"Node.js: OK (strategy eval + backtest engine)")
    else:
        print(f"WARNING: Node.js not found — strategies will stay HOLD and backtest is disabled")
    server=http.server.HTTPServer(("0.0.0.0",port),Handler)
    try: server.serve_forever()
    except KeyboardInterrupt: db_conn.close(); server.shutdown()

if __name__=="__main__": main()
