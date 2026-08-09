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

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
