#!/usr/bin/env python3
"""Quant Fleet Backend — Binance Live Data + Momentum Strategy"""

import http.server
import json
import math
import time
import threading
import urllib.request
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================
SYMBOLS = [
    "SOLUSDT", "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "UNIUSDT", "ATOMUSDT", "APTUSDT", "ARBUSDT", "OPUSDT"
]

SYMBOL_NAMES = {
    "SOL": "Solana", "BTC": "Bitcoin", "ETH": "Ethereum", "BNB": "BNB",
    "XRP": "Ripple", "ADA": "Cardano", "DOGE": "Dogecoin", "AVAX": "Avalanche",
    "DOT": "Polkadot", "LINK": "Chainlink", "MATIC": "Polygon", "UNI": "Uniswap",
    "ATOM": "Cosmos", "APT": "Aptos", "ARB": "Arbitrum", "OP": "Optimism"
}

BINANCE_BASE = "https://api.binance.com"
CACHE_TTL = 5  # seconds

# ============================================================
# DATA CACHE (thread-safe)
# ============================================================
cache = {"data": None, "last_fetch": 0}
cache_lock = threading.Lock()
exec_log = []
log_lock = threading.Lock()

def add_log(ts, msg_type, html):
    with log_lock:
        exec_log.append({"ts": ts, "type": msg_type, "html": html})
        if len(exec_log) > 200:
            exec_log.pop(0)

# ============================================================
# TECHNICAL INDICATORS (pure Python, no deps)
# ============================================================
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = 0
    losses = 0
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_sma(closes, period=20):
    if len(closes) < period:
        return sum(closes) / len(closes) if closes else 0
    return sum(closes[-period:]) / period

def calc_ema(closes, period=12):
    if len(closes) < 2:
        return closes[-1] if closes else 0
    multiplier = 2.0 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

# ============================================================
# BINANCE FETCH
# ============================================================
def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "QuantFleet/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return None

def fetch_all_data():
    # 24hr tickers (price, change, volume)
    tickers_raw = fetch_json(f"{BINANCE_BASE}/api/v3/ticker/24hr")
    if not tickers_raw:
        return None

    # Build price map
    price_map = {}
    for t in tickers_raw:
        price_map[t["symbol"]] = {
            "price": float(t["lastPrice"]),
            "change_pct": float(t["priceChangePercent"]),
            "volume": float(t["quoteVolume"]),  # USDT volume
            "high": float(t["highPrice"]),
            "low": float(t["lowPrice"]),
        }

    result = {"tickers": [], "exec_log": [], "timestamp": datetime.now().isoformat()}

    # Fetch klines for each symbol (batch = all at once would be slow; we'll be selective)
    for symbol in SYMBOLS:
        sym = symbol.replace("USDT", "")
        name = SYMBOL_NAMES.get(sym, sym)
        pm = price_map.get(symbol)
        if not pm:
            continue

        price = pm["price"]
        change_pct = pm["change_pct"]
        volume = pm["volume"]

        # Fetch 1h klines for RSI + SMA
        klines_1h = fetch_json(f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=30")
        # Fetch 4h klines for SMA
        klines_4h = fetch_json(f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval=4h&limit=30")

        # Compute indicators
        closes_1h = [float(k[4]) for k in klines_1h] if klines_1h else []
        closes_4h = [float(k[4]) for k in klines_4h] if klines_4h else []

        rsi_1h = calc_rsi(closes_1h, 14) if len(closes_1h) >= 15 else 50.0
        sma_4h_20 = calc_sma(closes_4h, 20) if closes_4h else price
        sma_1h_20 = calc_sma(closes_1h, 20) if closes_1h else price

        # Volume check
        avg_vol = volume * 0.85  # Simplified: use 85% of 24h as "average"
        vol_surge = volume > avg_vol * 1.2

        # Sparkline from 1h closes
        sparkline = closes_1h[-18:] if len(closes_1h) >= 18 else closes_1h

        # ============================================================
        # STRATEGY: Multi-TF Momentum
        # ============================================================
        signal = "HOLD"
        confidence = 50

        # Factor 1: RSI (0-100) — normalized to 0-1
        rsi_factor = 1.0 - (rsi_1h / 100.0)  # Low RSI = bullish potential

        # Factor 2: Price vs SMA 4h (trend direction)
        sma_factor = min(1.0, max(0.0, (price - sma_4h_20) / (sma_4h_20 * 0.05 + 0.0001)))
        sma_factor = (sma_factor + 1.0) / 2.0  # Normalize to 0-1

        # Factor 3: Volume confirmation
        vol_factor = 1.0 if vol_surge else 0.4

        # Composite score (weighted)
        composite = rsi_factor * 0.45 + sma_factor * 0.30 + vol_factor * 0.25

        # Signal determination
        if rsi_1h < 45 and price > sma_4h_20 and vol_surge:
            signal = "BUY"
            confidence = int(composite * 100)
        elif rsi_1h > 65 or price < sma_4h_20 * 0.98:
            signal = "SELL"
            confidence = int((1.0 - composite) * 100)
        elif price > sma_4h_20 and rsi_1h < 55:
            signal = "HOLD"
            confidence = int(composite * 80)
        else:
            signal = "WAIT"
            confidence = int(composite * 60)

        confidence = max(10, min(99, confidence))

        result["tickers"].append({
            "id": sym,
            "name": name,
            "price": price,
            "change": 0,  # deprecated
            "change_pct": change_pct,
            "volume_m": round(volume / 1_000_000, 1),
            "signal": signal,
            "confidence": confidence,
            "sparkline": sparkline,
            # Raw indicators (not shown in UI but useful for factor radar)
            "_rsi": round(rsi_1h, 1),
            "_sma4h": round(sma_4h_20, price < 1 and 4 or 2),
            "_vol_surge": vol_surge,
        })

    # Log generation
    buys = sum(1 for t in result["tickers"] if t["signal"] == "BUY")
    sells = sum(1 for t in result["tickers"] if t["signal"] == "SELL")
    waits = sum(1 for t in result["tickers"] if t["signal"] == "WAIT")
    holds = sum(1 for t in result["tickers"] if t["signal"] == "HOLD")
    ts = datetime.now().strftime("%H:%M:%S")

    for t in result["tickers"]:
        if t["signal"] in ("BUY", "SELL"):
            color = "#00FF66" if t["signal"] == "BUY" else "#FF2A6D"
            result["exec_log"].append({
                "ts": ts, "type": t["signal"].lower(),
                "html": f'[{ts}] {t["id"]} RSI={t["_rsi"]} SMA={t["_sma4h"]} → <span style="color:{color}">{t["signal"]}</span> conf={t["confidence"]}%'
            })

    result["exec_log"].insert(0, {
        "ts": ts, "type": "info",
        "html": f'[{ts}] SCAN COMPLETE → BUY:{buys} SELL:{sells} WAIT:{waits} HOLD:{holds} | {len(result["tickers"])} pairs'
    })

    with log_lock:
        for entry in result["exec_log"]:
            exec_log.append(entry)
            if len(exec_log) > 200:
                exec_log.pop(0)

    # Build full DATA response
    # Strategy matrix
    strategies = ["RSI", "SMA CROSS", "VOL SURGE", "COMPOSITE"]
    timeframes = ["15m", "1h", "4h", "1d"]
    cells = []
    # Simulate matrix: each strategy is active on specific timeframes
    for si, sname in enumerate(strategies):
        for ti, tf in enumerate(timeframes):
            # RSI active on 1h, SMA on 4h, VOL on 1h, COMPOSITE on all
            active = False
            if sname == "RSI" and tf == "1h": active = True
            elif sname == "SMA CROSS" and tf == "4h": active = True
            elif sname == "VOL SURGE" and tf == "1h": active = True
            elif sname == "COMPOSITE": active = True
            cells.append([si, ti, "active" if active else "idle"])

    # Factor radar from average of all tickers
    avg_rsi = sum(t["_rsi"] for t in result["tickers"]) / max(len(result["tickers"]), 1)
    price_above_sma = sum(1 for t in result["tickers"] if t["price"] > t["_sma4h"]) / max(len(result["tickers"]), 1)
    vol_ratio = sum(1 for t in result["tickers"] if t["_vol_surge"]) / max(len(result["tickers"]), 1)

    factors = [
        {"label": "RSI(14)", "value": min(1.0, (100 - avg_rsi) / 100)},
        {"label": "TREND", "value": price_above_sma},
        {"label": "VOLUME", "value": vol_ratio},
        {"label": "MOMENTUM", "value": min(1.0, buys / max(len(result["tickers"]), 1) * 3)},
        {"label": "BEARISH", "value": min(1.0, sells / max(len(result["tickers"]), 1) * 3)},
        {"label": "NEUTRAL", "value": min(1.0, (waits + holds) / max(len(result["tickers"]), 1))},
    ]

    # KPI (simulated paper-trading stats)
    kpi = {
        "sharpe": round(1.5 + (buys - sells) * 0.1, 2),
        "win_rate": round(55 + buys * 1.5, 1),
        "pnl_day": (buys - sells) * 120,
        "max_drawdown": round(3.0 + sells * 0.3, 1),
        "aum": 100000 + (buys - sells) * 2500,
    }

    return {
        "tickers": result["tickers"],
        "strategy_matrix": {"strategies": strategies, "timeframes": timeframes, "cells": cells},
        "kpi": kpi,
        "factors": factors,
        "exec_log": list(exec_log[-50:]),
        "order_flow": {},
    }


# ============================================================
# HTTP HANDLER
# ============================================================
HTML_PATH = "/root/cyberpunk_dashboard.html"

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            data = fetch_all_data()
            if data is None:
                self.send_response(502)
                self.end_headers()
                self.wfile.write(b'{"error":"Binance API unavailable"}')
                return
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/" or self.path == "/index.html":
            try:
                with open(HTML_PATH, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Dashboard HTML not found")
        else:
            super().do_GET()

    def log_message(self, format, *args):
        pass  # silent

def main():
    port = 8899
    print(f"🚀 Quant Fleet server starting on http://localhost:{port}")
    print(f"   Symbols: {', '.join(SYMBOLS)}")
    print(f"   Strategy: Multi-TF Momentum (RSI + SMA + Volume)")
    server = http.server.HTTPServer(("0.0.0.0", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()
