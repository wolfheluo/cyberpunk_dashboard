# Quant Fleet — Algorithmic Trading Dashboard

A real-time cryptocurrency trading dashboard with pluggable JavaScript strategies, automated paper trading, node-driven backtesting, and live Binance data — served by a single Python HTTP server with a vanilla-JS frontend.

[![python](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org)
[![data](https://img.shields.io/badge/data-Binance_API-orange)](https://www.binance.com)
[![database](https://img.shields.io/badge/database-SQLite-green)](https://www.sqlite.org)

---

## Features

- **Live Dashboard** — Real-time watchlist from Binance with sparkline charts, signal table, and factor radar
- **Auto Paper Trading** — $10,000 simulated portfolio; BUY/SELL signals from the active strategy are executed server-side on every scan
- **Pluggable JS Strategies** — Create, edit, activate, and delete strategies as JavaScript object literals; each file exports `NAME`, `DESCRIPTION`, and an `evaluate()` function
- **Backtest Engine** — Runs every strategy against historical daily klines (2025-01 → 2026-06) via Node.js, producing equity curves and return comparisons
- **Portfolio Tracking** — SQLite-backed trade journal, position tracking, and equity curve
- **Dynamic Watchlist** — Add/remove trading pairs through the UI, persisted in SQLite
- **Bilingual UI** — English / Traditional Chinese toggle backed by external JSON dictionaries

---

## Requirements

- Python 3.10+ (standard library only — no pip dependencies)
- **Node.js** — required for strategy evaluation (live signals) and the backtest engine. If Node.js is absent, the server starts but strategies remain HOLD and backtesting is disabled.

---

## Quick Start

```bash
git clone https://github.com/wolfheluo/cyberpunk_dashboard.git
cd cyberpunk_dashboard

# 1. Initialize the database + download historical klines (optional, for backtesting)
python init_db.py

# 2. Start the server
python quant_fleet_server.py
# Quant Fleet on http://localhost:8899

# 3. Open the dashboard
# http://localhost:8899
```

---

## Architecture

```
quant_fleet_server.py        HTTP server + Binance fetcher + trade engine + backtest route
init_db.py                   Database schema, seeding, historical kline downloader
dashboard/
  cyberpunk_dashboard.html   Single-page frontend (Tailwind CDN + vanilla JS)
  js/app.js                  Rendering, i18n, Binance WebSocket price feed
  css/style.css              Cyberpunk theme
  i18n/en.json, zh.json      Translation dictionaries
strategies/
  grid.js                    Grid trading strategy (long/short, size_pct pyramiding)
  _run_strategy.js           Node helper — evaluates the active strategy for live signals
  _run_backtest.js           Node helper — runs backtests for all symbols in one call
quant_fleet.db               SQLite database (auto-created, git-ignored)
backtest_data/               Cached Binance Vision .zip files (git-ignored)
```

### SQLite Tables

| Table              | Purpose                                  |
|--------------------|------------------------------------------|
| `portfolio`        | Cash balance, initial capital            |
| `watchlist`        | Trading symbols + display names          |
| `signals`          | Non-HOLD signals only (audit trail)      |
| `trades`           | All executed paper trades                |
| `positions`        | Current open positions (marked to market)|
| `historical_klines`| Daily OHLCV for backtesting              |
| `prices`           | 5-minute price snapshots (data source)   |

---

## Navigation

| Section     | Purpose                                        |
|-------------|------------------------------------------------|
| DASHBOARD   | Real-time trading panel: signals, radar, pipeline, log |
| STRATEGIES  | View, edit, create, delete strategy files      |
| BACKTEST    | Run all strategies against historical data     |
| WATCHLIST   | Add/remove trading pairs                       |
| MY ACCOUNT  | Portfolio summary, positions, trade history, equity curve |

---

## API Endpoints

### Data

| Method | Path               | Description                               |
|--------|--------------------|-------------------------------------------|
| GET    | `/api/data`        | Full dashboard payload (tickers, signals, KPI, log) |
| GET    | `/api/portfolio`   | Cash, position value, total equity        |
| GET    | `/api/positions`   | Open positions, marked to market          |
| GET    | `/api/trades`      | Recent 200 trades                         |
| GET    | `/api/symbols`     | Watchlist symbols                         |

### Strategy Management

| Method | Path                              | Description                                   |
|--------|-----------------------------------|-----------------------------------------------|
| GET    | `/api/strategies`                 | List all strategies + active                  |
| POST   | `/api/strategy/activate`          | Switch active strategy `{"filename":"..."}`   |
| GET    | `/api/strategy/{file}/code`       | Read strategy source code                     |
| POST   | `/api/strategy/{file}/save`       | Save edited code `{"code":"..."}`             |
| POST   | `/api/strategy/create`            | Create new strategy `{"filename":"..."}`      |
| POST   | `/api/strategy/{file}/delete`     | Delete a strategy                             |

Strategy filenames are restricted to `[A-Za-z0-9_-]+.js`; path traversal is rejected.

### Backtest / Account / Symbols

| Method | Path                   | Description                                        |
|--------|------------------------|----------------------------------------------------|
| POST   | `/api/backtest/run`    | Backtest all strategies against all symbols (Node) |
| POST   | `/api/reset`           | Reset portfolio to $10,000, clear trades/positions |
| POST   | `/api/symbols/add`     | Add symbol `{"symbol":"DOGEUSDT","name":"Dogecoin"}` |
| DELETE | `/api/symbols/{SYMBOL}`| Remove symbol from watchlist                       |
| GET    | `/api/params/ref`      | Strategy parameter reference (CSV download)        |

---

## Writing a Strategy

Strategies live in `strategies/` as JavaScript object literals. Each file must
## Strategy Return Values

`evaluate()` returns `{signal, confidence, factors}` plus two optional flags:

- `add: true` — add to an existing same-side position (avg entry recalculated)
- `close_pct: 0.2` — partial close ratio for SELL (default 1.0 = full close).
  Used by grid strategies to sell one grid lot at a time.

export `NAME`, `DESCRIPTION`, and an `evaluate(ticker, indicators)` function:

```js
({
  NAME: "My Strategy",
  DESCRIPTION: "Brief description",
  evaluate: function (ticker, indicators) {
    // ticker: {id, name, price, volume, change_pct, high_24h, low_24h,
    //          pct_from_high, pct_from_low, book, position, portfolio}
    //   position: current holding for THIS symbol {side, quantity, entry_price}
    //             or null
    //   portfolio: {cash, total_equity} — available balance & marked equity
    //   book: {best_bid, best_ask, bid_qty, ask_qty, spread_pct, imbalance}
    //         (order book summary — null in backtests, live-only)
    // indicators: {rsi, sma20, sma50, ema12, ema26, ema50,
    //              macd_line, macd_signal, macd_hist,
    //              bb_upper, bb_middle, bb_lower, atr14,
    //              rsi_4h, sma_4h, volSurge, closes}
    if (indicators.rsi < 30) return {signal: "BUY", confidence: 80};
    if (indicators.rsi > 70) return {signal: "SELL", confidence: 75};
    return {signal: "HOLD", confidence: 50};
  }
})
```

**Signal values**: `BUY`, `SELL`, `HOLD`, `WAIT`
**Confidence**: 0–100
**`factors`**: optional object logged to the `signals` table for audit

**Parameter notes**:
- `indicators.*` are computed from 1h closes (plus `rsi_4h` / `sma_4h` from 4h closes).
- `ticker.book.*` comes from the Binance order book — available in live trading only;
  backtests pass `book: null` (no historical order book).
- In backtests (daily data), `rsi_4h`/`sma_4h` mirror the daily series; `high_24h`/`low_24h`
  are the current candle's high/low.
- Client-side hot-reload approximates `atr14` and the 4h values from the live tick
  buffer; the server (authoritative, trade-executing) uses real candle data.

The exact same `evaluate()` code path drives live trading (via `_run_strategy.js`)
and backtesting (via `_run_backtest.js`), so results stay consistent.

Download the full parameter reference from the STRATEGIES page (**PARAMS** button).

### Strategies

There are **no built-in strategies** — create your own from the STRATEGIES page
(+ NEW button) or drop a `.js` file into `strategies/`. Each strategy is an
object literal with `NAME`, `DESCRIPTION` and an `evaluate(ticker, indicators)`
function returning `{signal: "BUY"|"SELL"|"HOLD", confidence, size_pct,
close_pct, add}`.

| Signal param  | Meaning                                                    | Default |
|---------------|------------------------------------------------------------|---------|
| `size_pct`    | Notional size when opening/adding (grids scale 2%→6%)     | 5%      |
| `close_pct`   | Fraction of the position closed on an opposite signal     | 1.0     |
| `add`         | Allow same-side add-on while a position is held           | false   |

`grid.js` is included as a reference implementation (symmetric long/short
grid, ±10% band at 0.1% per level).

### Backtest vs Live Differences (N-25)

Backtests replay **daily** klines through the same `evaluate()` path as live
trading, with these documented differences:

| Input | Backtest | Live |
|-------|----------|------|
| `change_pct` | always 0 | real 24h change |
| `high_24h` / `low_24h` | day's range | real 24h range |
| `book` | `null` | real order book |
| `volSurge` | volume > 0 | 1.5x recent 1h average |
| `rsi_4h` / `sma_4h` | mirrors daily series | real 4h closes |
| position/portfolio | tracked inside the backtest | live paper account |

---

## Security Notes

**Risk acceptance (decision 2026-08-09, spec/remediation.md D20 option d):**
this is a single-user **paper-trading** dashboard. The HTTP API has **no
authentication**, binds `0.0.0.0`, and answers with `Access-Control-Allow-Origin:
*` — anyone on the same network segment can read/write the API (account reset,
simulate trades, strategy files). This risk was reviewed and **accepted**:
- trades are simulated (no real funds); the account is resettable
- the exposure surface is narrowed by earlier fixes: path traversal blocked
  (D1), stored XSS closed (D18), simulate input validated (M-5)
- **Do not** deploy on a public network or share it with other users without
  re-evaluating (auth token / write-endpoint protection / removing CORS `*`).

## Backtesting

Historical daily klines are downloaded from [Binance Vision](https://data.binance.vision)
for 2025-01-01 → 2026-06-30:

```bash
python init_db.py
```

Opening the BACKTEST page runs every strategy against every symbol automatically:
$10,000 initial capital, 5% position size, 30-day warmup. Results show per-strategy
equity curves and return comparison. Requires Node.js.

---

## Paper Trading

- Initial capital: **$10,000** (configurable in `init_db.py`)
- Position size: **5%** of cash per trade (`TRADE_SIZE_PCT` in `quant_fleet_server.py`)
- **BUY** opens/adds a long position, or covers an existing short
- **SELL** closes an existing long, or opens a short position when flat
- One position per symbol (long and short are mutually exclusive)
- Short P&L is `(entry − current) × qty`; positions are marked to market on every scan
- All trades are recorded in SQLite

### Portfolio KPIs

KPIs are computed from actual trade history (FIFO realized PnL for win rate;
equity-curve statistics for Sharpe ratio and max drawdown). Metrics without
sufficient data display as `--` rather than fabricated values.

---

## Configuration

| Variable          | File                   | Default      | Description                    |
|-------------------|------------------------|--------------|--------------------------------|
| `INITIAL_CAPITAL` | `init_db.py`           | 10,000       | Starting portfolio value       |
| `HIST_START`      | `init_db.py`           | (2025, 1)    | Backtest start (year, month)   |
| `HIST_END`        | `init_db.py`           | (2026, 6)    | Backtest end                   |
| `DEFAULT_SYMBOLS` | `init_db.py`           | 7 pairs      | Seeded on first DB creation    |
| `TRADE_SIZE_PCT`  | `quant_fleet_server.py`| 0.05         | % of cash per trade            |
| `port`            | `quant_fleet_server.py`| 8899         | HTTP server port               |

---

## Notes

- **No pip dependencies** — Python standard library only; Node.js is required for strategy execution and backtesting
- **Cross-platform** — runs on Windows, macOS, Linux
- **Network access** — Binance REST + WebSocket required for live data; the server binds `0.0.0.0` so the dashboard can be reached from other machines on the LAN (paper trading only — no authentication is enforced)
- **Historical data** — ~15 MB of cached `.zip` files in `backtest_data/` (git-ignored)
- The Tailwind CDN warning in the browser console is expected (development build)
