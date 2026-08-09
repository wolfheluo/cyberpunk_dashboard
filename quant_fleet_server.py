#!/usr/bin/env python3
"""Quant Fleet Backend — Binance + SQLite + Pluggable Strategies + Auto Paper Trading"""

import http.server
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import threading
from datetime import datetime
from init_db import init_db, DB_PATH, INITIAL_CAPITAL

def now_ts(fmt="%H:%M:%S"):
    return datetime.now().strftime(fmt)

# ============================================================
# CONFIG
# ============================================================
SYMBOLS, SYMBOL_NAMES = [], []
def reload_symbols():
    global SYMBOLS, SYMBOL_NAMES
    rows = get_db().execute("SELECT symbol,name FROM watchlist ORDER BY id").fetchall()
    SYMBOLS = [r[0] for r in rows]
    SYMBOL_NAMES = {r[0].replace("USDT",""): r[1] for r in rows}
BINANCE_BASE = "https://api.binance.com"
_BASE = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
STRATEGIES_DIR = os.path.join(_BASE, "strategies")
HTML_PATH = os.path.join(_BASE, "dashboard", "cyberpunk_dashboard.html")
TRADE_SIZE_PCT = 0.05
MIN_CASH = 1000.0  # new positions rejected below this cash balance

# ============================================================
# SQLITE
# ============================================================
# D2 (M-4): one connection per thread via threading.local — the module is
# served by ThreadingHTTPServer (one thread per request), and sqlite3
# connections are not safe to share across threads. init_db() is idempotent
# (guarded seeds), so each worker thread lazily gets its own WAL connection.
_thread_local = threading.local()
db_conn = init_db()  # main-thread connection (module load, reload_symbols)

def get_db():
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = init_db()
        _thread_local.conn = conn
    return conn

reload_symbols()
db_lock = threading.Lock()
exec_log = []
log_lock = threading.Lock()
active_strategy = ""  # no built-in strategy (D3/C-3): user creates one
_last_signal = {}  # symbol → last signal; decision log only records changes


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

def _safe_static_path(base, rel):
    """Join `rel` under `base`; return None if the resolved path escapes base.

    Traversal guard (D1): realpath both sides and require the result to stay
    inside base. A path like /dashboard/../quant_fleet.db resolves outside the
    dashboard dir and is rejected before any open() call.
    """
    try:
        base_real = os.path.realpath(base)
        full = os.path.realpath(os.path.join(base, rel))
    except (ValueError, OSError):
        return None
    if full != base_real and not full.startswith(base_real + os.sep):
        return None
    return full

def _strategy_meta(content):
    """Extract NAME/DESCRIPTION from a JS strategy file (object-literal style)."""
    # N-14: accept both single- and double-quoted strings
    name_m = re.search(r"""NAME\s*[:=]\s*["']([^"']+)["']""", content)
    desc_m = re.search(r"""DESCRIPTION\s*[:=]\s*["']([^"']+)["']""", content)
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

# Strategy state persisted server-side between polls (node processes are stateless)
_strategy_state = {}
_strategy_mtime = {}  # N-23: last-seen mtime per strategy file — file change => state invalid

def _log_warn(msg):
    """Append a warning to the exec log (visible in the dashboard) with a cap."""
    with log_lock:
        exec_log.append({"ts": now_ts(), "html": f'<span style="color:#FFCC00">[warn]</span> {msg}'})
        if len(exec_log) > 300:
            del exec_log[:100]

def run_js_strategy(strategy_file, ticker_infos):
    """Evaluate a JS strategy for all tickers via a node subprocess.

    ticker_infos: [{"id": "BTC", "ticker": {...}, "indicators": {...}}, ...]
    Strategy state (e.g. priceHistory) is persisted server-side across polls.
    Returns {symbol: {"signal","confidence","factors"}} — empty dict on any failure.
    """
    if not HAS_NODE or not ticker_infos:
        return {}
    # N-23: strategy file changed on disk (edit/pull) — stale state must not
    # leak into the new code's semantics; reset it before handing off to node.
    try:
        mtime = os.path.getmtime(os.path.join(STRATEGIES_DIR, strategy_file))
    except OSError:
        mtime = None
    if _strategy_mtime.get(strategy_file) != mtime:
        _strategy_state.pop(strategy_file, None)
        _strategy_mtime[strategy_file] = mtime
    helper = os.path.join(STRATEGIES_DIR, "_run_strategy.js")
    payload = json.dumps({"strategy": strategy_file,
                          "state": _strategy_state.get(strategy_file, {}),
                          "tickers": ticker_infos})
    try:
        proc = subprocess.run(["node", helper], input=payload, capture_output=True,
                              text=True, timeout=15)
        out = proc.stdout.strip()
        if proc.returncode != 0 or not out:
            # D17/M-10: node failure must be visible (was silent -> platform
            # looked fine while strategies stayed HOLD)
            _log_warn(f"strategy {strategy_file}: node exit={proc.returncode} "
                      f"stderr={proc.stderr.strip()[:200] or 'empty'}")
            return {}
        data = json.loads(out)
        if "error" in data:
            _log_warn(f"strategy {strategy_file}: {data['error']}")
            return {}
        if data.get("state") is not None:
            _strategy_state[strategy_file] = data["state"]
        return data.get("signals", {})
    except FileNotFoundError:
        _log_warn(f"strategy {strategy_file}: file not found")
        return {}
    except Exception as e:
        _log_warn(f"strategy {strategy_file}: {e!r}")
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
            _log_warn(f"backtest {strategy_file}: node exit={proc.returncode} "
                      f"stderr={proc.stderr.strip()[:200] or 'empty'}")
            return {}
        data = json.loads(out)
        if isinstance(data, dict) and "error" in data:
            _log_warn(f"backtest {strategy_file}: {data['error']}")
            return {}
        return data
    except Exception as e:
        _log_warn(f"backtest {strategy_file}: {e!r}")
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
    except Exception as e:  # D17/N-9: failures must be observable, not swallowed
        print(f"[fetch_json] {url}: {e!r}", file=sys.stderr)
        return None

# 24hr ticker is weight 40 per call — cache it too (1s polls would otherwise
# blow the 1200 weight/min limit: 40*60 = 2400).
_ticker24_cache = None
_ticker24_ts = 0.0
def fetch_ticker24_cached(ttl=60):
    global _ticker24_cache, _ticker24_ts
    now = time.time()
    if _ticker24_cache is not None and now - _ticker24_ts < ttl:
        return _ticker24_cache
    data = fetch_json(f"{BINANCE_BASE}/api/v3/ticker/24hr")
    if data is not None:
        _ticker24_cache = data
        _ticker24_ts = now
    # D10/N-2: on failure fall back to the last good snapshot (stale > nothing)
    return data if data is not None else _ticker24_cache

# Cache klines so fast polls don't hammer the rate limit (indicators refresh slowly)
_klines_cache = {}
_klines_cache_ts = {}
def fetch_klines_cached(symbol, interval, limit=100, ttl=60):
    key = (symbol, interval)
    now = time.time()
    if key in _klines_cache and now - _klines_cache_ts.get(key, 0) < ttl:
        return _klines_cache[key]
    data = fetch_json(f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
    if data is not None:
        _klines_cache[key] = data
        _klines_cache_ts[key] = now
    # D10/N-2: failure falls back to stale klines (indicators degrade gracefully)
    return data if data is not None else _klines_cache.get(key)

# D11/N-3: bookTicker is fetched every poll today (weight 4/call, 240/min with
# multiple tabs) — short TTL so the order book stays fresh without hammering.
_book_cache = None
_book_cache_ts = 0.0
def fetch_book_cached(ttl=3):
    global _book_cache, _book_cache_ts
    now = time.time()
    if _book_cache is not None and now - _book_cache_ts < ttl:
        return _book_cache
    data = fetch_json(f"{BINANCE_BASE}/api/v3/ticker/bookTicker")
    if data is not None:
        _book_cache = data
        _book_cache_ts = now
    return data if data is not None else _book_cache

# ============================================================
# TRADE EXECUTION
# ============================================================
def execute_trade(symbol, side, price, strategy_name, signal_id=None, close_pct=1.0, size_pct=None):
    """Execute a paper trade.

    BUY  — opens/adds a long position, or covers an existing short.
    SELL — closes an existing long (close_pct of it, default 100%), or opens
           a short position when flat.

    Returns one of:
      {"status":"filled", "trade_id":..., "quantity":..., "notional":..., "realized_pnl":...}
      {"status":"rejected", "reason":"insufficient_funds"}   — cash < MIN_CASH or notional < $10
      {"status":"failed", "reason":"db_error"}               — trade could not be written to DB
    """
    with db_lock:
        try:
            portfolio = get_db().execute("SELECT cash FROM portfolio WHERE id=1").fetchone()
            if not portfolio:
                return {"status": "failed", "reason": "db_error"}
            cash = portfolio[0]
            pos = get_db().execute("SELECT side,quantity,entry_price FROM positions WHERE symbol=?", (symbol,)).fetchone()
            realized = 0.0
            trade_side = side

            if side == "BUY" and pos and pos[0] == "SELL":
                # ---- Cover short: buy back close_pct of it (partial covers for grids) ----
                close_pct = min(max(float(close_pct or 1.0), 0.01), 1.0)
                # D9/N-1: reject when cash cannot cover the full requested close —
                # a silent partial fill drifts grid state away from expectations.
                want_qty = pos[1] * close_pct
                qty = min(want_qty, cash / price) if price else 0
                if qty <= 0 or qty < want_qty - 1e-9:
                    return {"status": "rejected", "reason": "insufficient_funds"}
                notional = qty * price
                realized = (pos[2] - price) * qty
                cash_change = -notional
                remain = pos[1] - qty
                if remain <= 0.00001:
                    get_db().execute("DELETE FROM positions WHERE symbol=?", (symbol,))
                else:
                    get_db().execute("UPDATE positions SET quantity=?,updated_at=datetime('now', '+8 hours') WHERE symbol=?",
                                    (round(remain, 8), symbol))
            elif side == "BUY":
                # ---- Open / add long (size_pct lets grid strategies scale lot size) ----
                if cash < MIN_CASH:
                    return {"status": "rejected", "reason": "insufficient_funds"}
                size = min(max(float(size_pct or TRADE_SIZE_PCT), 0.001), 0.5)
                notional = min(cash * size, cash)
                if notional < 10:
                    return {"status": "rejected", "reason": "insufficient_funds"}
                qty = notional / price
                cash_change = -notional
                if pos:  # add to existing long
                    new_qty = pos[1] + qty
                    avg_entry = (pos[2]*pos[1] + price*qty) / new_qty
                    get_db().execute("UPDATE positions SET quantity=?,entry_price=?,current_price=?,unrealized_pnl=?,updated_at=datetime('now', '+8 hours') WHERE symbol=?",
                                    (round(new_qty, 8), round(avg_entry, 8), price, round((price-avg_entry)*new_qty, 8), symbol))
                else:
                    get_db().execute("INSERT INTO positions (symbol,side,entry_price,quantity,current_price,unrealized_pnl,strategy) VALUES (?,?,?,?,?,?,?)",
                                    (symbol, "BUY", price, round(qty, 8), price, 0.0, strategy_name))
            elif side == "SELL" and pos and pos[0] == "BUY":
                # ---- Close long: close close_pct of the position (grid trading) ----
                close_pct = min(max(float(close_pct or 1.0), 0.01), 1.0)
                qty = pos[1] * close_pct
                notional = qty * price
                realized = (price - pos[2]) * qty
                cash_change = notional
                remain = pos[1] - qty
                if remain <= 0.00001:
                    get_db().execute("DELETE FROM positions WHERE symbol=?", (symbol,))
                else:
                    get_db().execute("UPDATE positions SET quantity=?,current_price=?,unrealized_pnl=?,updated_at=datetime('now', '+8 hours') WHERE symbol=?",
                                    (round(remain, 8), price, round((price - pos[2]) * remain, 8), symbol))
            else:
                # ---- Open / add short ----
                if cash < MIN_CASH:
                    return {"status": "rejected", "reason": "insufficient_funds"}
                size = min(max(float(size_pct or TRADE_SIZE_PCT), 0.001), 0.5)
                notional = min(cash * size, cash)
                if notional < 10:
                    return {"status": "rejected", "reason": "insufficient_funds"}
                qty = notional / price
                cash_change = notional
                if pos:  # add to existing short
                    new_qty = pos[1] + qty
                    avg_entry = (pos[2]*pos[1] + price*qty) / new_qty
                    get_db().execute("UPDATE positions SET quantity=?,entry_price=?,current_price=?,unrealized_pnl=?,updated_at=datetime('now', '+8 hours') WHERE symbol=?",
                                    (round(new_qty, 8), round(avg_entry, 8), price, round((avg_entry-price)*new_qty, 8), symbol))
                else:
                    get_db().execute("INSERT INTO positions (symbol,side,entry_price,quantity,current_price,unrealized_pnl,strategy) VALUES (?,?,?,?,?,?,?)",
                                    (symbol, "SELL", price, round(qty, 8), price, 0.0, strategy_name))

            # Record trade
            cur = get_db().execute(
                "INSERT INTO trades (symbol,side,price,quantity,notional,strategy,signal_id) VALUES (?,?,?,?,?,?,?)",
                (symbol, trade_side, price, round(qty, 8), round(notional, 2), strategy_name, signal_id))
            trade_id = cur.lastrowid

            # Update cash
            get_db().execute("UPDATE portfolio SET cash=cash+?, updated_at=datetime('now', '+8 hours') WHERE id=1",
                            (cash_change,))
            get_db().commit()
            return {"status": "filled", "trade_id": trade_id, "quantity": round(qty, 8),
                    "notional": round(notional, 2), "realized_pnl": round(realized, 2)}
        except Exception:
            try:
                get_db().rollback()
            except Exception:
                pass
            return {"status": "failed", "reason": "db_error"}

def equity_curve():
    """Full-history equity curve (average-cost MTM), correct starting point
    ($10,000) regardless of how many trades exist — unlike the frontend which
    rebuilt from the last 200 trades and drifted.
    Returns [[ts, equity], ...] from initial capital to now (incl. open MTM).
    """
    rows = get_db().execute(
        "SELECT symbol,side,price,quantity,created_at FROM trades ORDER BY id ASC").fetchall()
    cash = float(INITIAL_CAPITAL)
    pos = {}  # sym -> {"side","qty","entry"}
    last_price = {}
    curve = [[rows[0][4] if rows else "", cash]]
    for sym, side, price, qty, ts in rows:
        last_price[sym] = price
        p = pos.get(sym)
        if side == "BUY":
            cash -= qty * price
            if p and p["side"] == "SELL":
                realized = (p["entry"] - price) * qty
                remain = p["qty"] - qty
                if remain > 0.00001:
                    pos[sym] = {"side": "SELL", "qty": remain, "entry": p["entry"]}
                elif remain < -0.00001:
                    pos[sym] = {"side": "BUY", "qty": -remain, "entry": price}
                else:
                    del pos[sym]
            elif p:
                new_qty = p["qty"] + qty
                pos[sym] = {"side": "BUY", "qty": new_qty,
                            "entry": (p["entry"] * p["qty"] + price * qty) / new_qty}
            else:
                pos[sym] = {"side": "BUY", "qty": qty, "entry": price}
        else:
            cash += qty * price
            if p and p["side"] == "BUY":
                remain = p["qty"] - qty
                if remain > 0.00001:
                    pos[sym] = {"side": "BUY", "qty": remain, "entry": p["entry"]}
                elif remain < -0.00001:
                    pos[sym] = {"side": "SELL", "qty": -remain, "entry": price}
                else:
                    del pos[sym]
            elif p:
                new_qty = p["qty"] + qty
                pos[sym] = {"side": "SELL", "qty": new_qty,
                            "entry": (p["entry"] * p["qty"] + price * qty) / new_qty}
            else:
                pos[sym] = {"side": "SELL", "qty": qty, "entry": price}
        mtm = sum(v["qty"] * last_price.get(s, price) * (1 if v["side"] == "BUY" else -1)
                  for s, v in pos.items())
        curve.append([ts, round(cash + mtm, 2)])
    return curve


def rebuild_cycles():
    """Rebuild position lifecycles from trade history (average-cost).

    Each row = one position cycle: open time, symbol, side, average open
    price (across ALL opens/adds), average close price (across all partial
    closes), total opened quantity, unrealized (open cycles only, marked at
    the last re-marked price), realized PnL, strategy.
    """
    rows = get_db().execute(
        "SELECT symbol,side,price,quantity,strategy,created_at FROM trades ORDER BY id ASC").fetchall()
    symbols = []
    for r in rows:
        if r[0] not in symbols:
            symbols.append(r[0])

    cur_price = {}
    for r in get_db().execute("SELECT symbol,current_price FROM positions").fetchall():
        if r[1]:
            cur_price[r[0]] = r[1]

    cycles = []
    for sym in symbols:
        pos = None
        for side, price, qty, strategy, ts in [r[1:] for r in rows if r[0] == sym]:
            if pos is None:
                pos = {"symbol": sym, "open_time": ts, "side": side, "open_qty_total": 0.0, "open_cost_total": 0.0,
                       "remaining": 0.0, "remaining_cost": 0.0,
                       "close_qty": 0.0, "close_value": 0.0, "realized": 0.0, "strategy": strategy}
                cycles.append(pos)
            if side == pos["side"]:
                # open or add
                pos["open_qty_total"] += qty
                pos["open_cost_total"] += qty * price
                pos["remaining"] += qty
                pos["remaining_cost"] += qty * price
            else:
                # close / cover (partial or full)
                avg_entry = pos["remaining_cost"] / pos["remaining"] if pos["remaining"] else price
                close_qty = min(qty, pos["remaining"])
                if pos["side"] == "BUY":
                    realized = (price - avg_entry) * close_qty
                else:
                    realized = (avg_entry - price) * close_qty
                pos["realized"] += realized
                pos["close_qty"] += close_qty
                pos["close_value"] += close_qty * price
                pos["remaining"] -= close_qty
                pos["remaining_cost"] -= close_qty * avg_entry
                if pos["remaining"] <= 0.00001:
                    pos = None

    out = []
    for p in cycles:
        open_avg = p["open_cost_total"] / p["open_qty_total"] if p["open_qty_total"] else 0
        close_avg = p["close_value"] / p["close_qty"] if p["close_qty"] else None
        cur = cur_price.get(p["symbol"])
        if p["remaining"] > 0.00001 and cur:
            if p["side"] == "BUY":
                unrealized = (cur - open_avg) * p["remaining"]
            else:
                unrealized = (open_avg - cur) * p["remaining"]
        else:
            unrealized = 0.0
        out.append({
            "open_time": p["open_time"],
            "symbol": p["symbol"],
            "side": p["side"],
            "open_avg": round(open_avg, 6),
            "close_avg": round(close_avg, 6) if close_avg is not None else None,
            "quantity": round(p["open_qty_total"], 8),
            "unrealized": round(unrealized, 2),
            "realized": round(p["realized"], 2),
            "strategy": p["strategy"],
            "closed": p["remaining"] <= 0.00001
        })
    out.reverse()  # newest first
    return out


def portfolio_stats(initial_capital=INITIAL_CAPITAL):
    """Average-cost based realized PnL + equity curve from trade history.
    Supports long AND short positions (SELL opens short, BUY covers).
    Returns (win_rate, sharpe, max_drawdown) — each None when there is not enough data.
    """
    rows = get_db().execute("SELECT symbol,side,price,quantity FROM trades ORDER BY id ASC").fetchall()
    if not rows:
        return None, None, None
    cash = initial_capital
    pos = {}  # symbol -> {"side": "BUY"|"SELL", "qty": float, "entry": float}
    last_price = {}
    equity = []
    wins = losses = 0
    for sym, side, price, qty in rows:
        last_price[sym] = price
        p = pos.get(sym)
        if side == "BUY":
            cash -= qty * price
            if p and p["side"] == "SELL":
                # cover short (may flip to long if over-covered)
                realized = (p["entry"] - price) * qty
                if realized > 0: wins += 1
                elif realized < 0: losses += 1
                remain = p["qty"] - qty
                if remain > 0.00001:
                    pos[sym] = {"side": "SELL", "qty": remain, "entry": p["entry"]}
                elif remain < -0.00001:
                    pos[sym] = {"side": "BUY", "qty": -remain, "entry": price}
                else:
                    del pos[sym]
            elif p:
                new_qty = p["qty"] + qty
                pos[sym] = {"side": "BUY", "qty": new_qty,
                            "entry": (p["entry"] * p["qty"] + price * qty) / new_qty}
            else:
                pos[sym] = {"side": "BUY", "qty": qty, "entry": price}
        else:  # SELL
            cash += qty * price
            if p and p["side"] == "BUY":
                # close long (may flip to short if over-sold)
                realized = (price - p["entry"]) * qty
                if realized > 0: wins += 1
                elif realized < 0: losses += 1
                remain = p["qty"] - qty
                if remain > 0.00001:
                    pos[sym] = {"side": "BUY", "qty": remain, "entry": p["entry"]}
                elif remain < -0.00001:
                    pos[sym] = {"side": "SELL", "qty": -remain, "entry": price}
                else:
                    del pos[sym]
            elif p:
                new_qty = p["qty"] + qty
                pos[sym] = {"side": "SELL", "qty": new_qty,
                            "entry": (p["entry"] * p["qty"] + price * qty) / new_qty}
            else:
                pos[sym] = {"side": "SELL", "qty": qty, "entry": price}
        # Mark to market: long = +qty*price, short = -qty*price (liability)
        mtm = sum(v["qty"] * last_price.get(s, price) * (1 if v["side"] == "BUY" else -1) for s, v in pos.items())
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
    tickers_raw = fetch_ticker24_cached()
    if not tickers_raw: return None

    price_map = {}
    for t in tickers_raw:
        price_map[t["symbol"]] = {
            "price":float(t["lastPrice"]),"change_pct":float(t["priceChangePercent"]),
            "volume":float(t["quoteVolume"]),"high":float(t["highPrice"]),"low":float(t["lowPrice"])
        }

    strategy_name = active_strategy.replace(".js","").replace("_"," ").title() if active_strategy else "none"
    result = {"tickers":[], "exec_log":[], "timestamp":datetime.now().isoformat(),
              "executed":[], "rejected":[], "failed":[]}

    # Get current portfolio
    with db_lock:
        pf = get_db().execute("SELECT cash FROM portfolio WHERE id=1").fetchone()
        cash = pf[0] if pf else INITIAL_CAPITAL
        positions_map = {}
        for r in get_db().execute("SELECT symbol,side,quantity,entry_price FROM positions"):
            positions_map[r[0]] = {"side":r[1],"quantity":r[2],"entry_price":r[3]}

    # Re-mark open positions with current prices (positions would otherwise be stale)
    with db_lock:
        for r in get_db().execute("SELECT symbol,side,quantity,entry_price FROM positions").fetchall():
            pm = price_map.get(r[0] + "USDT")
            if pm:
                # long: (cur-entry)*qty ; short: (entry-cur)*qty
                upnl = (pm["price"] - r[3]) * r[2] if r[1] == "BUY" else (r[3] - pm["price"]) * r[2]
                get_db().execute(
                    "UPDATE positions SET current_price=?, unrealized_pnl=?, updated_at=datetime('now', '+8 hours') WHERE symbol=?",
                    (pm["price"], upnl, r[0]))
        get_db().commit()

    # Record prices for historical tracking (every 5 min to avoid bloat)
    with db_lock:
        last_rec = get_db().execute("SELECT MAX(recorded_at) FROM prices").fetchone()[0]
        # D8/M-2: compare against the same base the INSERT writes (UTC).
        # The old mix (stored UTC+8 vs datetime.now() UTC) never throttled.
        should_record = not last_rec or (datetime.utcnow() - datetime.fromisoformat(last_rec)).total_seconds() > 300

    # Pass 1: build ticker + indicators for every watchlist symbol, then evaluate
    # the active JS strategy ONCE via node subprocess (no more hardcoded HOLD).
    book_map = {}
    bt_raw = fetch_book_cached()  # D11/N-3: TTL-cached, not per-poll
    if bt_raw:
        for b in bt_raw:
            book_map[b["symbol"]] = b

    # Portfolio snapshot for strategy params (position + available cash + equity)
    with db_lock:
        pf_row = get_db().execute("SELECT cash FROM portfolio WHERE id=1").fetchone()
        portfolio_cash = pf_row[0] if pf_row else INITIAL_CAPITAL
        pos_rows = get_db().execute("SELECT symbol,side,quantity,entry_price FROM positions").fetchall()
    pos_map = {r[0]: {"side": r[1], "quantity": r[2], "entry_price": r[3]} for r in pos_rows}
    pos_value = sum(v["quantity"] * (price_map.get(s + "USDT", {}).get("price", 0) or 0)
                    * (1 if v["side"] == "BUY" else -1) for s, v in pos_map.items())
    portfolio_equity = portfolio_cash + pos_value

    ticker_infos = []
    for symbol in SYMBOLS:
        sym = symbol.replace("USDT", "")
        pm = price_map.get(symbol)
        if not pm:
            continue
        price = pm["price"]; change_pct = pm["change_pct"]; volume = pm["volume"]
        high = pm["high"]; low = pm["low"]
        name = SYMBOL_NAMES.get(sym, sym)

        klines_1h = fetch_klines_cached(symbol, "1h", 100)
        klines_4h = fetch_klines_cached(symbol, "4h", 100)
        closes_1h = [float(k[4]) for k in klines_1h] if klines_1h else []
        closes_4h = [float(k[4]) for k in klines_4h] if klines_4h else []
        if should_record and pm:
            try:
                with db_lock:
                    get_db().execute("INSERT INTO prices (symbol, price, recorded_at) VALUES (?, ?, datetime('now'))", (sym, price))
                    get_db().commit()
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
                "book": book,
                "position": pos_map.get(sym),  # {side,quantity,entry_price} or null
                "portfolio": {"cash": portfolio_cash, "total_equity": portfolio_equity}
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

    # No active strategy after a reset — signals stay HOLD until the user picks one.
    if active_strategy:
        signals_map = run_js_strategy(active_strategy, ticker_infos) or {}
    else:
        signals_map = {}

    # Pass 2: record signals, auto-execute trades, build ticker rows
    scan_ts = now_ts()
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

        # Record signal (skip HOLD/WAIT rows — otherwise the table grows ~120k rows/day)
        signal_id = None
        if signal not in ("HOLD", "WAIT"):
            with db_lock:
                cur = get_db().execute(
                    "INSERT INTO signals (symbol,signal,confidence,price,factors_json,strategy) VALUES (?,?,?,?,?,?)",
                    (sym, signal, confidence, price, json.dumps(factors_dict), strategy_name))
                signal_id = cur.lastrowid
                get_db().commit()

        # Decision log: record ONLY signal transitions (HOLD→SELL etc.), never
        # repeated states — the log should show what changed, not the same
        # position over and over.
        prev_sig = _last_signal.get(sym)
        if prev_sig is None or prev_sig == "WAIT":
            # First sighting or warm-up WAIT (accumulating) is not a decision.
            _last_signal[sym] = signal
        elif signal != prev_sig:
            _last_signal[sym] = signal
            rsi = info["indicators"].get("rsi")
            book = info["ticker"].get("book") or {}
            obi = book.get("imbalance")
            detail = f"RSI {rsi}" if rsi is not None else "—"
            if obi is not None:
                detail += f", OBI {obi}"
            result["exec_log"].append({
                "ts": scan_ts, "kind": "decision",
                "sym": sym, "prev": prev_sig, "signal": signal,
                "confidence": confidence, "detail": detail
            })

        # Auto-execute trade on BUY/SELL — events drive the pipeline orb:
        #   filled   → exec orb (SIGNAL→RISK→ORDER→FILL→DONE)
        #   rejected → reject orb (→REJECT): only insufficient funds — a
        #              repeated signal on an already-held position is a no-op
        #              (no event → orb takes the WAIT path), not a rejection.
        #   failed   → fail orb (→FAIL): trade could not be written to DB
        current_pos = positions_map.get(sym)
        want_add = bool(sig.get("add"))
        trade_result = None
        if signal == "BUY":
            if current_pos and current_pos["side"] == "BUY" and not want_add:
                pass  # repeated signal on a long — no-op unless the strategy asks to add
            elif (current_pos and current_pos["side"] == "BUY") or (not current_pos):
                trade_result = execute_trade(sym, "BUY", price, strategy_name, signal_id,
                                             size_pct=sig.get("size_pct"))  # open or add long
            elif current_pos and current_pos["side"] == "SELL":
                trade_result = execute_trade(sym, "BUY", price, strategy_name, signal_id,
                                             size_pct=sig.get("size_pct"),
                                             close_pct=sig.get("close_pct", 1.0))  # cover short (partial for grids)
        elif signal == "SELL":
            if current_pos and current_pos["side"] == "SELL" and not want_add:
                pass  # repeated signal on a short — no-op unless add requested
            elif (current_pos and current_pos["side"] == "SELL") or (not current_pos):
                trade_result = execute_trade(sym, "SELL", price, strategy_name, signal_id,
                                             size_pct=sig.get("size_pct"))  # open or add short
            elif current_pos and current_pos["side"] == "BUY":
                trade_result = execute_trade(sym, "SELL", price, strategy_name, signal_id,
                                             close_pct=sig.get("close_pct", 1.0))  # close long (partial for grids)
        if trade_result:
            st = trade_result.get("status")
            event = {"symbol": sym, "side": signal, "price": price,
                     "reason": trade_result.get("reason")}
            if st == "filled":
                result["executed"].append(event)
                if current_pos:
                    if current_pos["side"] == signal:
                        action = "add"
                    elif signal == "BUY":
                        action = "cover"
                    else:
                        action = "close"
                else:
                    action = "open"
                rp = trade_result.get("realized_pnl") or 0
                result["exec_log"].append({
                    "ts": scan_ts, "kind": "filled",
                    "sym": sym, "side": signal,
                    "qty": trade_result.get("quantity", 0),
                    "price": price, "notional": trade_result.get("notional", 0),
                    "realized": rp, "action": action
                })
            elif st == "rejected":
                result["rejected"].append(event)
                result["exec_log"].append({
                    "ts": scan_ts, "kind": "rejected",
                    "sym": sym, "side": signal,
                    "reason": trade_result.get("reason") or "unknown"
                })
            elif st == "failed":
                result["failed"].append(event)
                result["exec_log"].append({
                    "ts": scan_ts, "kind": "failed",
                    "sym": sym, "side": signal,
                    "reason": "db_error"
                })

        sparkline = closes_1h[-18:] if len(closes_1h) >= 18 else closes_1h
        result["tickers"].append({
            "id": sym, "name": name, "price": price, "change_pct": change_pct,
            "volume_m": round(volume / 1_000_000, 1), "signal": signal, "confidence": confidence,
            "book": info["ticker"]["book"],
            "position": info["ticker"]["position"],
            "portfolio": info["ticker"]["portfolio"],
            "sparkline": sparkline,
            "_rsi": round(info["indicators"]["rsi"], 1),
            "_sma4h": round(info["indicators"]["sma20"], price < 1 and 4 or 2),
            "_vol_surge": info["indicators"]["volSurge"]
        })

    # ---- Build logs (decision/event lines were recorded during Pass 2) ----
    buys=sells=0

    # Re-read positions & trades after execution
    with db_lock:
        updated_positions = get_db().execute("SELECT symbol,side,quantity,entry_price FROM positions").fetchall()
        pf = get_db().execute("SELECT cash FROM portfolio WHERE id=1").fetchone()
        cash = pf[0] if pf else INITIAL_CAPITAL

    positions_map2 = {r[0]:{"side":r[1],"qty":r[2],"entry":r[3]} for r in updated_positions}

    for t in result["tickers"]:
        if t["signal"]=="BUY": buys+=1
        elif t["signal"]=="SELL": sells+=1

    # Portfolio summary
    pos_value = sum(pos["qty"]*(price_map.get(pos_sym+"USDT",{}).get("price",0) or 0)
                    * (1 if pos["side"] == "BUY" else -1)
                    for pos_sym, pos in positions_map2.items())
    total_equity = cash + pos_value
    pnl = total_equity - INITIAL_CAPITAL

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
    try:
        win_rate, sharpe, max_dd = portfolio_stats(INITIAL_CAPITAL)
    except Exception:
        win_rate = sharpe = max_dd = None

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
        "active_strategy":strategy_name,"order_flow":{},
        "executed":result.get("executed",[]),"rejected":result.get("rejected",[]),"failed":result.get("failed",[])
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
            rows = get_db().execute("SELECT symbol,side,entry_price,quantity,current_price,unrealized_pnl,strategy,opened_at FROM positions ORDER BY opened_at DESC").fetchall()
            self._json(200,{"positions":[{"symbol":r[0],"side":r[1],"entry_price":r[2],"quantity":r[3],"current_price":r[4],"unrealized_pnl":r[5],"strategy":r[6],"opened_at":r[7]} for r in rows]})
        elif self.path=="/api/trades":
            rows = get_db().execute("SELECT symbol,side,price,quantity,notional,status,strategy,created_at FROM trades ORDER BY created_at DESC LIMIT 200").fetchall()
            self._json(200,{"trades":[{"symbol":r[0],"side":r[1],"price":r[2],"quantity":r[3],"notional":r[4],"status":r[5],"strategy":r[6],"created_at":r[7]} for r in rows]})
        elif self.path=="/api/portfolio":
            pf = get_db().execute("SELECT cash,initial_capital FROM portfolio WHERE id=1").fetchone()
            pos_val = sum(r[3]*(r[0] if r[0] else r[1]) * (1 if r[2] == "BUY" else -1)
                           for r in get_db().execute("SELECT current_price,entry_price,side,quantity FROM positions").fetchall())
            self._json(200,{"cash":pf[0],"initial_capital":pf[1],"position_value":pos_val,
                            "total_equity":pf[0]+pos_val, "cycles": rebuild_cycles(),
                            "equity_curve": equity_curve()})
        elif self.path == "/api/symbols":
            rows = get_db().execute("SELECT id,symbol,name FROM watchlist ORDER BY id").fetchall()
            self._json(200, {"symbols":[{"id":r[0],"symbol":r[1],"name":r[2]} for r in rows]})
        elif self.path.startswith("/dashboard/i18n/") or self.path.startswith("/i18n/"):
            fname = self.path.replace("/dashboard/i18n/", "").replace("/i18n/", "")
            path = _safe_static_path(os.path.join(_BASE, "dashboard", "i18n"), fname)
            if path and os.path.isfile(path):
                with open(path, "rb") as f: content = f.read()
                self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(content))); self.end_headers(); self.wfile.write(content)
            else: self.send_error(404)
        elif self.path.startswith("/dashboard/"):
            rel = self.path[len("/dashboard/"):]
            fpath = _safe_static_path(os.path.join(_BASE, "dashboard"), rel)
            if fpath and fpath.endswith((".html", ".js", ".css", ".json")) and os.path.isfile(fpath):
                ct = "text/css" if fpath.endswith(".css") else "application/javascript" if fpath.endswith(".js") else "text/plain"
                with open(fpath, "rb") as f: content = f.read()
                self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)
            else: self.send_error(404)
        elif self.path in ("/","/index.html"):
            try:
                with open(HTML_PATH,"rb") as f: content=f.read()
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(content))); self.end_headers(); self.wfile.write(content)
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
                "ticker.position.side,string,Position side BUY|SELL or null,SELL\n"
                "ticker.position.quantity,number,Position quantity or null,6.55\n"
                "ticker.position.entry_price,number,Position avg entry price or null,76.20\n"
                "ticker.portfolio.cash,number,Available cash balance,10500.00\n"
                "ticker.portfolio.total_equity,number,Cash + marked position value,10000.80\n"
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
                _last_signal.clear()  # fresh decision log for the new strategy
                _strategy_state.pop(fname, None)  # D13/N-5: stale grid state must not leak in
                with open(path, encoding="utf-8") as f:
                    name, _ = _strategy_meta(f.read())
                self._json(200,{"active":fname,"name":name or fname})
            else: self._json(400,{"error":f"Unknown: {fname}"})
        elif self.path=="/api/trade/simulate":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            sym=body.get("symbol",""); side=body.get("side","BUY"); price=body.get("price",0)
            # M-5: validate before touching the account — was: any symbol/side/price
            # could inject fake trades (unauthenticated, LAN-reachable).
            if sym not in SYMBOLS:
                self._json(400,{"error":"symbol not in watchlist"}); return
            if side not in ("BUY","SELL"):
                self._json(400,{"error":"side must be BUY or SELL"}); return
            try: price = float(price)
            except (TypeError, ValueError):
                self._json(400,{"error":"price must be a number > 0"}); return
            if price <= 0:
                self._json(400,{"error":"price must be > 0"}); return
            r = execute_trade(sym,side,price,body.get("strategy","manual"))
            self._json(200,r if r else {"error":"Insufficient funds or no position"})
            with db_lock:
                get_db().execute("DELETE FROM trades");
                get_db().execute("DELETE FROM positions");
                get_db().execute("DELETE FROM signals");
                get_db().execute("UPDATE portfolio SET cash=?,updated_at=datetime('now', '+8 hours') WHERE id=1", (INITIAL_CAPITAL,))
                get_db().commit()
            with log_lock: exec_log.clear()
            active_strategy = ""  # no strategy after reset — user picks one when ready
            self._json(200,{"status":"reset","capital":INITIAL_CAPITAL,"active_strategy":""})
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
                    active_strategy = ""  # deleted the active strategy — go flat (D3/C-3)
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
                    # D17/M-10: syntax-check before persisting — a broken file
                    # silently kills the poll loop (node eval fails every second)
                    if HAS_NODE:
                        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
                            tf.write(new_code)
                            tmp_path = tf.name
                        try:
                            chk = subprocess.run(["node", "--check", tmp_path],
                                                 capture_output=True, text=True, timeout=10)
                        finally:
                            os.unlink(tmp_path)
                        if chk.returncode != 0:
                            self._json(400, {"error": "Syntax error: " +
                                             (chk.stderr.strip()[:300] or "invalid JS")})
                            return
                    with open(path, "w", encoding="utf-8") as f: f.write(new_code)
                    _strategy_state.pop(fname, None)  # strategy rewritten — drop stale state
                    name, _ = _strategy_meta(new_code)
                    self._json(200, {"name": name or fname, "filename": fname})
                else:
                    self._json(400, {"error": "No code provided"})

        elif self.path == "/api/backtest/run":
            # Run backtest on-the-fly: evaluate all JS strategies against all
            # symbols' historical klines via node subprocess (one call per strategy).
            symbols_in_db = get_db().execute("SELECT DISTINCT symbol FROM historical_klines").fetchall()
            if not symbols_in_db:
                self._json(400, {"error": "No historical data. Run: python3 init_db.py"})
                return
            if not HAS_NODE:
                self._json(400, {"error": "Node.js not found — required for JS backtest engine"})
                return

            symbols_klines = {}
            for (sym,) in symbols_in_db:
                rows = get_db().execute("SELECT date,open,high,low,close,volume FROM historical_klines WHERE symbol=? ORDER BY date", (sym,)).fetchall()
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
            # D18: charset allowlist — rejects HTML/attribute-breakout payloads
            # that would otherwise become stored XSS when rendered unescaped.
            if not re.fullmatch(r"[A-Z0-9]{1,20}USDT", sym):
                self._json(400, {"error":"Symbol must be [A-Z0-9]+USDT (e.g. DOGEUSDT)"}); return
            if (not (1 <= len(name) <= 64) or any(ch in name for ch in '<>"\'&')
                    or any(ord(ch) < 32 for ch in name)):
                self._json(400, {"error":"Invalid name — no HTML special characters"}); return
            get_db().execute("INSERT OR IGNORE INTO watchlist (symbol,name) VALUES (?,?)", (sym,name))
            get_db().commit(); reload_symbols()
            self._json(200, {"status":"added","symbol":sym,"name":name})
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/symbols/"):
            sym = self.path.split("/api/symbols/")[1].upper()
            if not re.fullmatch(r"[A-Z0-9]+USDT", sym):
                self._json(400, {"error": "Invalid symbol"}); return
            get_db().execute("DELETE FROM watchlist WHERE symbol=?", (sym,))
            get_db().commit(); reload_symbols()
            self._json(200, {"status":"deleted","symbol":sym})
        else:
            self.send_error(405)

    def _json(self,code,data):
        body=json.dumps(data).encode()
        self.send_response(code); self.send_header("Content-Type","application/json"); self.send_header("Access-Control-Allow-Origin","*"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,f,*a): pass

def main():
    # QF_PORT override: test seam — the HTTP test harness runs an isolated instance
    port = int(os.environ.get("QF_PORT", 8899))
    print(f"Quant Fleet on http://localhost:{port}")
    print(f"Initial capital: ${INITIAL_CAPITAL:,.0f}")
    if not active_strategy:
        print("No active strategy — create one from the STRATEGIES page (D3/C-3)")
    if HAS_NODE:
        print(f"Node.js: OK (strategy eval + backtest engine)")
    else:
        print(f"WARNING: Node.js not found — strategies will stay HOLD and backtest is disabled")
    server=http.server.ThreadingHTTPServer(("0.0.0.0",port),Handler)
    try: server.serve_forever()
    except KeyboardInterrupt:
        conn = getattr(_thread_local, "conn", None)
        if conn: conn.close()
        server.shutdown()

if __name__=="__main__": main()
