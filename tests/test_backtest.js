#!/usr/bin/env node
// D5 (C-5): backtest final settlement must respect position side.
// Seam (pre-agreed in spec): node-level execution of _run_backtest.js with
// synthetic klines. Expected values are hand-computed from the trade rules:
//   open short at $100: notional 500 (5% of $10k) -> qty 5, cash 10500
//   final close $110:   buy back 5*110 = 550 -> cash 10500 - 550 = 9950
// The old bug did cash += 550 -> 11050 (overstated by 2x notional).
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const RUNNER = path.join(ROOT, 'strategies', '_run_backtest.js');
const STRAT_DIR = path.join(ROOT, 'strategies');

let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log(`  ok   ${name}`); }
  else { failed++; console.log(`  FAIL ${name}${detail !== undefined ? ' — got: ' + JSON.stringify(detail) : ''}`); }
}

function synthKlines(days, closes) {
  const out = [];
  for (let i = 0; i < days; i++) {
    const c = closes[i];
    out.push({ date: `2025-01-${String(i + 1).padStart(2, '0')}`, open: c, high: c, low: c, close: c, volume: 1000 });
  }
  return out;
}

function runBacktest(stratName, stratCode, symbols) {
  const file = path.join(STRAT_DIR, stratName);
  fs.writeFileSync(file, stratCode);
  try {
    const input = JSON.stringify({ strategy: stratName, symbols });
    const raw = execFileSync('node', [RUNNER], { input, cwd: ROOT, encoding: 'utf-8' });
    return JSON.parse(raw);
  } finally {
    fs.unlinkSync(file);
  }
}

const ALWAYS_SELL = '({evaluate:function(){return {signal:"SELL"};}})';
const ALWAYS_BUY = '({evaluate:function(){return {signal:"BUY"};}})';

// 35 days: 30 warmup + 5. Open happens at day 31 (price 100), price then rises to 110.
const closes = Array(30).fill(100).concat([100, 100, 100, 100, 110]);
const symbols = { BTC: synthKlines(35, closes) };

const res = runBacktest('t_always_sell.js', ALWAYS_SELL, symbols).BTC;
check('C-5: short settlement final_equity == 9950 (hand-computed)',
  res.final_equity === 9950, { final_equity: res.final_equity });
check('C-5: total_return_pct == -0.5',
  res.total_return_pct === -0.5, res.total_return_pct);
check('C-5: one short opened (sell_count 1)',
  res.sell_count === 1 && res.buy_count === 0, { sell: res.sell_count, buy: res.buy_count });

// Long control: open long at 100 (cash 9500, qty 5), settle at 110 -> 10050.
const resLong = runBacktest('t_always_buy.js', ALWAYS_BUY, symbols).BTC;
check('C-5: long settlement still correct == 10050',
  resLong.final_equity === 10050, { final_equity: resLong.final_equity });

// ============================================================
// D6 (M-8): backtest must honour add / close_pct / size_pct like execute_trade
// ============================================================

// 1) size_pct scales the opening notional:
//    short 10% of 10k = 1000 @100 -> qty 10, cash 11000; settle @110 -> -1100 -> 9900
const flat = Array(30).fill(100).concat([100, 100, 100, 100, 110]);
const r1 = runBacktest('t_size_pct.js',
  '({evaluate:function(){return{signal:"SELL",size_pct:0.1};}})',
  { BTC: synthKlines(35, flat) }).BTC;
check('D6: size_pct 0.1 honoured (final_equity 9900)',
  r1.final_equity === 9900, { final_equity: r1.final_equity });

// 2) close_pct partial close + add:
//    n1 SELL open @100 (qty5, cash10500)
//    n2 BUY close_pct 0.5 @95  -> qty2.5, cash 10500-237.5 = 10262.5
//    n3 BUY close rest @90     -> qty2.5, cash 10262.5-225 = 10037.5
//    (old code ignored close_pct: n2 closed everything @95, n3 opened a long,
//     final 10025 — distinguishable)
const waves = Array(30).fill(100).concat([100, 95, 90, 90, 90, 90]);
const r2 = runBacktest('t_partial.js',
  `var n=0;
  ({evaluate:function(){n++;
    if(n===1)return{signal:"SELL"};
    if(n===2)return{signal:"BUY",close_pct:0.5};
    if(n===3)return{signal:"BUY"};
    return{signal:"HOLD"};}})`,
  { BTC: synthKlines(36, waves) }).BTC;
check('D6: close_pct 0.5 partial close honoured (final_equity 10037.5)',
  r2.final_equity === 10037.5, { final_equity: r2.final_equity });
check('D6: three trades (open + 2 closes)',
  r2.trades_count === 3, r2.trades_count);

// 3) add: same-side signal with add:true adds to the position (avg cost)
//    n1 SELL open @100 (qty5, cash10500); n2 SELL add @100 (qty5 -> qty10,
//    cash 10500+500=11000); n3 BUY close all @100 -> cash 11000-1000 = 10000
//    (old code: n2 was a no-op -> only open+close -> trades 2)
const r3 = runBacktest('t_add.js',
  `var n=0;
  ({evaluate:function(){n++;
    if(n===1)return{signal:"SELL"};
    if(n===2)return{signal:"SELL",add:true};
    if(n===3)return{signal:"BUY"};
    return{signal:"HOLD"};}})`,
  { BTC: synthKlines(35, flat) }).BTC;
check('D6: add:true adds to position (3 trades, final 10000)',
  r3.trades_count === 3 && r3.final_equity === 10000,
  { trades: r3.trades_count, final_equity: r3.final_equity });

// ============================================================
// N-22: _run_strategy.js must reject traversal filenames (defence in depth)
// ============================================================
const evil = JSON.parse(execFileSync('node', [path.join(ROOT, 'strategies', '_run_strategy.js')], {
  input: JSON.stringify({ strategy: '../../../../etc/passwd', tickers: [] }),
  encoding: 'utf-8',
}));
check('N-22: _run_strategy.js rejects traversal filename', !!evil.error, evil);

// ============================================================
// N-24: grid.js dead state field g.last must be gone (write-only)
// ============================================================
const gridSrc = fs.readFileSync(path.join(ROOT, 'strategies', 'grid.js'), 'utf-8');
check('N-24: grid.js has no g.last write-only state', !/\.last\s*=/.test(gridSrc));

// ============================================================
// M-9: grid recover must reverse-engineer levels from position size,
// not reset to levels:1 (which drains the whole position on one close)
// ============================================================
(function () {
  const strat = eval(gridSrc);  // grid.js is an object literal expression
  strat.grids = {};             // state lost (server restart / save)
  // 3 layers @ $100 on $10k: lots 2 + 2.5 + 3 = 7.5 units
  const t = { id: 'BTC', price: 100, change_pct: 0, volume: 1,
              high_24h: 101, low_24h: 99, pct_from_high: 0, pct_from_low: 0,
              book: null,
              position: { side: 'BUY', quantity: 7.5, entry_price: 100 },
              portfolio: { cash: 10000, total_equity: 10000 } };
  strat.evaluate(t, { closes: [100, 100, 100], rsi: 50, sma20: 100, sma50: 100,
                      ema12: 100, ema26: 100, ema50: 100, macd_line: 0, macd_signal: 0,
                      macd_hist: 0, bb_upper: 101, bb_middle: 100, bb_lower: 99,
                      atr14: 0.5, rsi_4h: 50, sma_4h: 100, volSurge: false });
  const g = strat.grids['BTC'];
  check('M-9: recover infers ~3 grid levels from 7.5-unit position',
        g && g.levels >= 3, g ? { levels: g.levels } : 'no grid state');
})();

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
