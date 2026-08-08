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
      equityCurve.push(cash + (position ? position.qty * price : 0));
      continue;
    }
    const indicators = {
      rsi: calcRSI(closesHistory, 14),
      sma20: calcSMA(closesHistory, 20),
      ema12: calcEMA(closesHistory, 12),
      ema26: calcEMA(closesHistory, 26),
      volSurge: k.volume > 0,
      closes: closesHistory.slice(-30)
    };
    const ticker = {id: symbol, name: symbol, price: price, volume: k.volume, change_pct: 0};
    let signal = 'HOLD';
    try {
      const out = strategy.evaluate(ticker, indicators) || {};
      signal = out.signal || 'HOLD';
    } catch (e) { /* per-bar errors are ignored, same as live trading */ }

    if (signal === 'BUY') {
      const notional = Math.min(cash * TRADE_SIZE_PCT, cash);
      if (notional >= 10) {
        const qty = notional / price;
        cash -= notional;
        if (position) {
          position.entry = (position.entry * position.qty + price * qty) / (position.qty + qty);
          position.qty += qty;
        } else {
          position = {qty: qty, entry: price};
        }
        tradeCount++; buyCount++;
      }
    } else if (signal === 'SELL' && position) {
      cash += position.qty * price;
      position = null;
      tradeCount++; sellCount++;
    }
    equityCurve.push(cash + (position ? position.qty * price : 0));
  }

  if (position && klines.length) cash += position.qty * klines[klines.length - 1].close;
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
