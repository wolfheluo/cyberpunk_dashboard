#!/usr/bin/env node
// T-A (N-1/N-2/N-3): backtest helper alignment with live trading.
// Seam (pre-agreed): node-level execution of _run_backtest.js with synthetic
// klines; expected values hand-computed from the trade rules.
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

// N-1: invalid strategy filename must be rejected (defense-in-depth).
// Strong test: a traversal that resolves to a REAL object-literal strategy
// (../strategies/grid.js == same file via path.join escape) would eval and
// backtest fine WITHOUT validation — with validation it must error.
{
  const inputs = [
    { strategy: '../strategies/grid.js', why: 'traversal to real strategy' },
    { strategy: 'bad name.js', why: 'spaces in filename' },
    { strategy: '../../etc/passwd', why: 'traversal to non-JS file' },
  ];
  for (const { strategy, why } of inputs) {
    try {
      const raw = execFileSync('node', [RUNNER],
        { input: JSON.stringify({ strategy, symbols: {} }), cwd: ROOT, encoding: 'utf-8' });
      const out = JSON.parse(raw);
      check(`N-1 rejected (${why})`, !!(out && out.error), out);
    } catch (e) {
      check(`N-1 rejected (${why})`, true);
    }
  }
}

// N-2/N-3: MIN_CASH gate + rejected_count on underfunded cover
{
  // 50 flat days then 50 more — strategy BUYs once (5% of 10k = 500 notional),
  // then SELLs with close_pct to drain. To hit MIN_CASH we first drain cash
  // below 1000 with a big open, then a second BUY must be skipped.
  const klines = synthKlines(120, Array(120).fill(100));
  // hand-computed: open 1 BUY at 5% = 500 -> cash 9500, qty 5
  // then SELL close_pct 1.0 at same price -> cash 10000 (round trip)
  // Underfunded-cover probe: strategy returns BUY with size_pct 0.5 (50% of cash)
  // repeatedly — second BUY on same side needs add:true; cover needs a short.
  const strat = `({
    NAME: "N2 Probe",
    DESCRIPTION: "MIN_CASH + rejected probe",
    evaluate: function (t, i) {
      // open long 50% of cash, then try to open again (flat again after close)
      if (i.closes.length === 60) return {signal: "BUY", confidence: 80, size_pct: 0.5};
      if (i.closes.length === 61) return {signal: "SELL", confidence: 80};
      return {signal: "HOLD", confidence: 50};
    }
  })`;
  // 50% of 10000 = 5000 -> cash 5000; SELL closes -> cash 10000. Then a second
  // BUY at 50% would leave cash 5000 — never below MIN_CASH here, so this only
  // proves the round trip still works (regression guard).
  const r = runBacktest('n2_probe.js', strat, { BTC: klines });
  const bt = r.BTC;
  check('N-2/N-3 regression: round trip works', bt && bt.trades_count === 2, bt);
  check('N-2/N-3 regression: rejected_count present', bt && typeof bt.rejected_count === 'number', bt);
}

// N-2: MIN_CASH gate — repeated 50%-of-cash adds drain cash below 1000; the
// next add must be skipped (live execute_trade rejects cash < MIN_CASH).
// size_pct is clamped to 0.5 by clampSz, so each add takes 50% of remaining.
{
  const klines = synthKlines(120, Array(120).fill(100));
  const strat = `({
    NAME: "N2 MinCash",
    DESCRIPTION: "min cash gate probe",
    evaluate: function (t, i) {
      const n = i.closes.length;
      if (n >= 60 && n <= 64) return {signal: "BUY", confidence: 80, size_pct: 0.5, add: true};
      return {signal: "HOLD", confidence: 50};
    }
  })`;
  // hand-computed at price 100 (size clamp 0.5):
  //   bar 60 open: 10000*0.5=5000 -> cash 5000, qty 50
  //   bar 61 add:  5000*0.5=2500 -> cash 2500, qty 75
  //   bar 62 add:  2500*0.5=1250 -> cash 1250, qty 87.5
  //   bar 63 add:  1250*0.5=625  -> cash 625,  qty 93.75
  //   bar 64 add:  cash 625 < MIN_CASH(1000) -> REJECTED (rejected_count 1)
  // final settlement: sell 93.75*100 = 9375 -> cash 625+9375 = 10000
  const r = runBacktest('n2_mincash.js', strat, { BTC: klines });
  const bt = r.BTC;
  check('N-2 adds below MIN_CASH rejected', bt && bt.trades_count === 4, bt);
  check('N-2 rejected_count === 1', bt && bt.rejected_count === 1, bt);
  check('N-2 final equity intact', bt && bt.final_equity === 10000, bt && bt.final_equity);
}

console.log(`\n${failed === 0 ? 'ALL PASS' : failed + ' FAILED'} (${passed} passed, ${failed} failed)`);
process.exit(failed === 0 ? 0 : 1);
