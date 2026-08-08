#!/usr/bin/env python3
"""WebSocket server that pushes dashboard data to frontend clients."""

import asyncio
import json
import threading
import websockets

_connected = set()
_push_interval = 1.0  # seconds

def get_connected_count():
    return len(_connected)

async def _handler(websocket):
    _connected.add(websocket)
    try:
        async for _ in websocket:
            pass  # client messages ignored
    finally:
        _connected.discard(websocket)

async def _broadcast_loop(fetch_fn):
    """Every _push_interval seconds, call fetch_fn() and broadcast to all clients."""
    while True:
        if _connected:
            data = fetch_fn()
            if data:
                msg = json.dumps(data)
                # Broadcast to all connected clients
                disconnected = set()
                for ws in _connected:
                    try:
                        await ws.send(msg)
                    except:
                        disconnected.add(ws)
                _connected.difference_update(disconnected)
        await asyncio.sleep(_push_interval)

def run_server(fetch_fn, port=8898):
    """Start WebSocket push server in a background thread."""
    loop = asyncio.new_event_loop()

    async def _serve():
        async with websockets.serve(_handler, "0.0.0.0", port):
            await _broadcast_loop(fetch_fn)

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"[WS Server] Push on ws://localhost:{port}")
    return t
