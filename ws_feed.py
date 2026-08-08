#!/usr/bin/env python3
"""Binance WebSocket price feed — maintains real-time price cache."""

import asyncio
import json
import threading
import websockets
from datetime import datetime

_price_lock = threading.Lock()
_price_cache = {}

BINANCE_WS = "wss://stream.binance.com:9443/ws/!miniTicker@arr"

def get_cached_prices():
    with _price_lock:
        return dict(_price_cache)

def _update_cache(data):
    with _price_lock:
        for t in data:
            _price_cache[t["s"]] = {
                "price": float(t["c"]),
                "change_pct": float(t["P"]),
                "volume": float(t["q"]),
                "high": float(t["h"]),
                "low": float(t["l"]),
                "timestamp": datetime.now().isoformat(),
            }

async def _binance_feed():
    while True:
        try:
            async with websockets.connect(BINANCE_WS) as ws:
                print(f"[WS] Connected to Binance miniTicker ({len(_price_cache)} symbols)")
                async for msg in ws:
                    data = json.loads(msg)
                    if isinstance(data, list):
                        _update_cache(data)
        except Exception as e:
            print(f"[WS] Binance error: {e}, retry in 5s...")
            await asyncio.sleep(5)

def run_in_thread():
    loop = asyncio.new_event_loop()
    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_binance_feed())
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return 0  # non-blocking, feed starts in background
