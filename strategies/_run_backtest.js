// run_backtest.js — backtest a JS strategy against historical klines (daily) for many symbols.
// Uses the SAME evaluate() path as live trading, so results stay consistent.
// Usage: echo '<json>' | node run_backtest.js
// Input:  {strategy: "default.js", symbols: {BTC: [{date, open, high, low, close, volume}, ...], ...}}
// Output: {BTC: {final_equity, total_return_pct, trades_count, buy_count, sell_count, equity_curve, dates}, ...}
'use strict';
const fs = require('fs');
const path = require('path');

const INITIAL_CAPITAL = 10000;
const TRADE_SIZE_PCT = 0.05;
const WARMUP_DAYS = 30;

function calcRSI(c, p) {
  p = p || 14;
  if (c.length < p + 1) return 50;
  let g = 0, l = 0;
  for (let i = 1; i <= p; i++) {
    const d = c[c.length - i] - c[c.length - i - 1];
    if (d > 0) g += d; else l -= d;
  }
  if (l === 0) return 100;
  return 100 - (100 / (1 + g / l));
}
function calcSMA(c, p) {
  p = p || 20;
  if (!c.length) return 0;
  let s = 0;
  const n = Math.min(c.length, p);
  for (let i = 0; i < n; i++) s += c[c.length - 1 - i];
  return s / n;
}
function calcEMA(c, p) {
  p = p || 12;
  if (c.length < 2) return c[c.length - 1] || 0;
  const m = 2 / (p + 1);
  let e = c[0];
  for (let i = 1; i < c.length; i++) e = (c[i] - e) * m + e;
  return e;
}
function emaSeries(c, p) {
  if (!c.length) return [];
  const m = 2 / (p + 1);
  const out = [c[0]];
  for (let i = 1; i < c.length; i++) out.push((c[i] - out[out.length - 1]) * m + out[out.length - 1]);
  return out;
}
function calcMACD(c, fast, slow, signal) {
  fast = fast || 12; slow = slow || 26; signal = signal || 9;
  if (c.length < slow) return [0, 0, 0];
  const ef = emaSeries(c, fast), es = emaSeries(c, slow);
  const macdSeries = ef.map((f, i) => f - es[i]);
  const sigSeries = emaSeries(macdSeries, signal);
  const line = macdSeries[macdSeries.length - 1], sig = sigSeries[sigSeries.length - 1];
  return [line, sig, line - sig];
}
function calcBB(c, period, k) {
  period = period || 20; k = k || 2;
  const n = Math.min(c.length, period);
  if (n < 2) { const last = c[c.length - 1] || 0; return [last, last, last]; }
  const win = c.slice(-n);
  const mid = win.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(win.reduce((a, b) => a + (b - mid) ** 2, 0) / n);
  return [mid + k * sd, mid, mid - k * sd];
}
function calcATR(klines, period) {
  period = period || 14;
  if (klines.length < period + 1) return 0;
  const trs = [];
  for (let i = 1; i < klines.length; i++) {
    const h = klines[i].high, l = klines[i].low, pc = klines[i - 1].close;
    trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
  }
  return trs.slice(-period).reduce((a, b) => a + b, 0) / period;
}

function backtestOne(strategy, symbol, klines) {
  const closesHistory = [];
  const equityCurve = [];
  let cash = INITIAL_CAPITAL;
  let position = null;
  let tradeCount = 0, buyCount = 0, sellCount = 0;

  for (let i = 0; i < klines.length; i++) {
    const k = klines[i];
    const price = k.close;
    closesHistory.push(price);
    if (i < WARMUP_DAYS) {
      equityCurve.push(cash + (position ? position.qty * price * (position.side === 'SELL' ? -1 : 1) : 0));
      continue;
    }
    const macd = calcMACD(closesHistory);
    const bb = calcBB(closesHistory);
    const indicators = {
      rsi: calcRSI(closesHistory, 14),
      sma20: calcSMA(closesHistory, 20),
      sma50: calcSMA(closesHistory, 50),
      ema12: calcEMA(closesHistory, 12),
      ema26: calcEMA(closesHistory, 26),
      ema50: calcEMA(closesHistory, 50),
      macd_line: macd[0], macd_signal: macd[1], macd_hist: macd[2],
      bb_upper: bb[0], bb_middle: bb[1], bb_lower: bb[2],
      atr14: calcATR(klines, 14),
      // Daily data only — 4h values mirror the daily series in backtests.
      rsi_4h: calcRSI(closesHistory, 14),
      sma_4h: calcSMA(closesHistory, 20),
      volSurge: k.volume > 0,
      closes: closesHistory.slice(-100)
    };
    const ticker = {
      id: symbol, name: symbol, price: price, volume: k.volume, change_pct: 0,
      high_24h: k.high, low_24h: k.low,
      pct_from_high: k.high ? (price - k.high) / k.high * 100 : 0,
      pct_from_low: k.low ? (price - k.low) / k.low * 100 : 0,
      book: null,  // no historical order book — book params are live-only
      position: position ? {side: position.side, quantity: position.qty, entry_price: position.entry} : null,
      portfolio: {
        cash: cash,
        total_equity: cash + (position ? position.qty * price * (position.side === 'SELL' ? -1 : 1) : 0)
      }
    };
    let signal = 'HOLD';
    try {
      const out = strategy.evaluate(ticker, indicators) || {};
      signal = out.signal || 'HOLD';
    } catch (e) { /* per-bar errors are ignored, same as live trading */ }

    if (signal === 'BUY') {
      if (position && position.side === 'SELL') {
        // Cover short (entire position; skip if insufficient cash)
        const notional = position.qty * price;
        if (notional <= cash) {
          cash -= notional;
          position = null;
          tradeCount++; buyCount++;
        }
      } else if (!position) {
        // Open long
        const notional = Math.min(cash * TRADE_SIZE_PCT, cash);
        if (notional >= 10) {
          const qty = notional / price;
          cash -= notional;
          position = {side: 'BUY', qty: qty, entry: price};
          tradeCount++; buyCount++;
        }
      }
    } else if (signal === 'SELL') {
      if (position && position.side === 'BUY') {
        // Close long
        cash += position.qty * price;
        position = null;
        tradeCount++; sellCount++;
      } else if (!position) {
        // Open short: sell now, buy back later
        const notional = Math.min(cash * TRADE_SIZE_PCT, cash);
        if (notional >= 10) {
          const qty = notional / price;
          cash += notional;
          position = {side: 'SELL', qty: qty, entry: price};
          tradeCount++; sellCount++;
        }
      }
    }
    equityCurve.push(cash + (position ? position.qty * price * (position.side === 'SELL' ? -1 : 1) : 0));
  }

  if (position && klines.length) {
    // Final settlement by side (D5/C-5): a short must be bought back
    // (cash -= qty*close); a long is sold (cash += qty*close). The old
    // unconditional `cash += qty*close` overstated shorts by 2x notional.
    const last = klines[klines.length - 1].close;
    cash += position.side === 'SELL' ? -position.qty * last : position.qty * last;
  }
  const finalEquity = cash;
  const totalReturn = (finalEquity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100;

  const step = Math.max(1, Math.floor(equityCurve.length / 200));
  const sampledEq = [], sampledDates = [];
  for (let i = WARMUP_DAYS; i < equityCurve.length; i += step) {
    sampledEq.push(equityCurve[i]);
    sampledDates.push(klines[i] ? klines[i].date : '');
  }

  return {
    final_equity: Math.round(finalEquity * 100) / 100,
    total_return_pct: Math.round(totalReturn * 100) / 100,
    trades_count: tradeCount,
    buy_count: buyCount,
    sell_count: sellCount,
    equity_curve: sampledEq,
    dates: sampledDates
  };
}

let input = '';
process.stdin.on('data', d => { input += d; });
process.stdin.on('end', () => {
  try {
    const req = JSON.parse(input);
    const code = fs.readFileSync(path.join(__dirname, req.strategy), 'utf-8');
    const strategy = eval(code);
    if (!strategy || typeof strategy.evaluate !== 'function') throw new Error('strategy has no evaluate()');
    const out = {};
    for (const sym of Object.keys(req.symbols || {})) {
      const klines = (req.symbols[sym] || []).slice().sort((a, b) => (a.date > b.date ? 1 : -1));
      out[sym] = backtestOne(strategy, sym, klines);
    }
    process.stdout.write(JSON.stringify(out));
  } catch (e) {
    process.stdout.write(JSON.stringify({error: String(e)}));
  }
});
