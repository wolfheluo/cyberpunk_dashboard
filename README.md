# Quant Fleet — Algorithmic Trading Dashboard

A real-time cryptocurrency trading dashboard with pluggable strategies, paper trading, backtesting, and live Binance data — all in a single Python server + HTML frontend.

![](https://img.shields.io/badge/python-3.10+-blue)
![](https://img.shields.io/badge/data-Binance_API-orange)
![](https://img.shields.io/badge/database-SQLite-green)

---

## Features

- **Live Dashboard** — Real-time 7 crypto pairs from Binance with sparkline charts, signal table, and factor radar
- **Auto Paper Trading** — $10,000 simulated portfolio, auto-executes BUY/SELL based on active strategy
- **Pluggable Strategies** — Write/edit/delete Python strategy files via browser; strategies auto-reload
- **Backtest Engine** — Run all strategies against historical daily klines (2025-01 → 2026-06) with equity curve comparison
- **Portfolio Tracking** — SQLite-backed trade journal, position tracking, P&L, and equity curve
- **Dynamic Watchlist** — Add/remove trading pairs via UI, stored in SQLite
- **Bilingual UI** — English / Traditional Chinese toggle with external JSON dictionaries

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/wolfheluo/cyberpunk_dashboard.git
cd cyberpunk_dashboard

# 2. Initialize database + download historical data (~2 min)
python init_db.py

# 3. Start server
python quant_fleet_server.py

# 4. Open browser
# http://localhost:8899
```

No dependencies beyond Python 3.10+ standard library.

---

## Architecture

```
quant_fleet_server.py    ← HTTP server + Binance fetcher + trade engine + backtest engine
init_db.py               ← Database schema, seeding, historical kline downloader
cyberpunk_dashboard.html ← Single-page frontend (Tailwind + vanilla JS)
strategies/              ← Pluggable strategy .py files
i18n/                    ← en.json / zh.json translation dictionaries
quant_fleet.db           ← SQLite database (auto-created)
backtest_data/           ← Cached Binance Vision .zip files
```

### SQLite Tables

| Table | Purpose |
|-------|---------|
| `portfolio` | Cash balance, initial capital |
| `watchlist` | Trading symbols + names |
| `signals` | Every signal generated (audit trail) |
| `trades` | All paper trades (BUY/SELL) |
| `positions` | Current open positions (upserted) |
| `historical_klines` | Daily OHLCV for backtesting |

---

## Navigation

```
📊 DASHBOARD    — Real-time trading panel
⚙️ STRATEGIES   — View, edit, create, delete strategies
📈 BACKTEST     — Run all strategies against historical data
📋 WATCHLIST    — Add/remove trading pairs
👤 MY ACCOUNT   — Portfolio summary, positions, trade history, equity curve
```

---

## API Endpoints

### Data

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/data` | Full dashboard data (tickers, signals, KPI, log) |
| GET | `/api/portfolio` | Cash, position value, total equity |
| GET | `/api/positions` | Open positions list |
| GET | `/api/trades` | Recent 50 trades |
| GET | `/api/symbols` | Watchlist symbols |

### Strategy Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/strategies` | List all strategies + active |
| POST | `/api/strategy/activate` | Switch active strategy `{"filename":"..."}` |
| GET | `/api/strategy/{file}/code` | Read strategy source code |
| POST | `/api/strategy/{file}/save` | Save edited strategy code `{"code":"..."}` |
| POST | `/api/strategy/create` | Create new strategy `{"filename":"..."}` |
| POST | `/api/strategy/{file}/delete` | Delete a strategy |

### Backtest

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/backtest/run` | Run on-the-fly backtest for all symbols × all strategies |

### Account

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/reset` | Reset portfolio to $10,000, clear all trades/positions |

### Symbols

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/symbols/add` | Add symbol to watchlist `{"symbol":"DOGEUSDT","name":"Dogecoin"}` |
| DELETE | `/api/symbols/{SYMBOL}` | Remove symbol from watchlist |

### Other

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/params/ref` | Download strategy parameter reference (CSV) |
| GET | `/i18n/{lang}.json` | Translation dictionary |

---

## Writing a Strategy

Strategies live in `strategies/` as standalone `.py` files. Each file must export:

```python
NAME = "My Strategy"
DESCRIPTION = "Brief description of what it does"

def evaluate(ticker, indicators):
    """
    ticker:     {"id": "BTC", "name": "Bitcoin", "price": 65100.50, "volume": 1.5e9}
    indicators: {
        "rsi_1h": 45.2,        # RSI(14) on 1h closes
        "sma_4h": 64800.30,     # SMA(20) on 4h closes
        "sma_1h_20": 65050.10,  # SMA(20) on 1h closes
        "ema_12": 65120.00,     # EMA(12) on 1h closes
        "ema_26": 65080.50,     # EMA(26) on 1h closes
        "vol_surge": True,      # Volume > 1.5x recent average
        "closes_1h": [...],     # Last 30 1h close prices
        "closes_4h": [...],     # Last 30 4h close prices
    }

    Returns: {"signal": "BUY", "confidence": 82, "factors": {"rsi": 45.2}}
    """
    if indicators["rsi_1h"] < 30:
        return {"signal": "BUY", "confidence": 80}
    elif indicators["rsi_1h"] > 70:
        return {"signal": "SELL", "confidence": 75}
    return {"signal": "HOLD", "confidence": 50}
```

**Signal values**: `BUY`, `SELL`, `HOLD`, `WAIT`  
**Confidence**: 0–100 integer  
**`factors`**: Optional dict of computed values (logged in signals table for audit)

Download the full parameter reference from the STRATEGIES page (**📥 PARAMS** button).

### Built-in Strategies

| File | Strategy | Logic |
|------|----------|-------|
| `momentum.py` | Multi-TF Momentum | RSI(1h) < 45 + price > SMA(4h) + vol surge → BUY |
| `bollinger_mean_reversion.py` | Bollinger Mean Reversion | Price < lower band + RSI < 35 → BUY |
| `ema_crossover_trend.py` | EMA Crossover Trend | EMA(12) > EMA(26) + price > EMA(50) → BUY |

---

## Backtesting

Historical daily klines are downloaded from [Binance Vision](https://data.binance.vision) for 2025-01-01 → 2026-06-30.

```bash
python init_db.py          # Downloads all historical data
```

Then open **BACKTEST** page — backtest runs on-the-fly every time you visit the page. Each strategy is evaluated against every symbol with $10,000 initial capital, 5% position size, and 30-day warmup period. Results show equity curves and return comparison.

Edit a strategy → re-visit BACKTEST → see updated results immediately.

---

## Paper Trading

- Initial capital: **$10,000**
- Position size: **5%** per trade
- BUY executes when strategy signals BUY (only if no existing position)
- SELL executes when strategy signals SELL (liquidates entire position)
- All trades recorded in SQLite `trades` table
- Portfolio P&L tracked in real-time on DASHBOARD and MY ACCOUNT

---

## Configuration

Edit these in `init_db.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `INITIAL_CAPITAL` | 10,000 | Starting portfolio value |
| `HIST_START` | (2025, 1) | Backtest start (year, month) |
| `HIST_END` | (2026, 6) | Backtest end |
| `DEFAULT_SYMBOLS` | 7 pairs | Seeded on first DB creation |

Edit in `quant_fleet_server.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADE_SIZE_PCT` | 0.05 | % of portfolio per trade |
| `port` | 8899 | HTTP server port |

---

## Directory Structure

```
cyberpunk_dashboard/
├── quant_fleet_server.py      # Main server
├── init_db.py                 # DB init + historical download
├── cyberpunk_dashboard.html   # Frontend SPA
├── quant_fleet.db             # SQLite (auto-created)
├── strategies/                # Strategy plugins
│   ├── momentum.py
│   ├── bollinger_mean_reversion.py
│   └── ema_crossover_trend.py
├── i18n/
│   ├── en.json
│   └── zh.json
├── backtest_data/             # Cached kline .zip files
├── .gitignore
└── README.md
```

---

## Notes

- **No external dependencies** — uses only Python standard library
- **Cross-platform** — works on Windows, macOS, Linux
- **Binance API** — requires internet connection for live data
- **Historical data** — ~15MB of cached .zip files in `backtest_data/`
- The Tailwind CSS CDN warning in browser console is expected (not using production build)
