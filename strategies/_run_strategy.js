// run_strategy.js — evaluate a JS strategy for many tickers in one node call.
// Stateless process: strategy state (e.g. priceHistory) is passed in with the
// request and returned after evaluation, so the server can persist it between polls.
// Usage: echo '<json>' | node run_strategy.js
// Input:  {strategy: "default.js", state: {...}, tickers: [{id, ticker: {...}, indicators: {...}}, ...]}
// Output: {signals: {BTC: {signal, confidence, factors}, ...}, state: {...}} or {error: "..."}
'use strict';
const fs = require('fs');
const path = require('path');

const RESERVED = ['NAME', 'DESCRIPTION', 'evaluate'];

let input = '';
process.stdin.on('data', d => { input += d; });
process.stdin.on('end', () => {
  try {
    const req = JSON.parse(input);
    const code = fs.readFileSync(path.join(__dirname, req.strategy), 'utf-8');
    const strat = eval(code);
    if (!strat || typeof strat.evaluate !== 'function') throw new Error('strategy has no evaluate()');
    // Restore persisted state onto the strategy object
    if (req.state && typeof req.state === 'object') {
      for (const k of Object.keys(req.state)) strat[k] = req.state[k];
    }
    const results = {};
    for (const t of req.tickers || []) {
      try {
        const out = strat.evaluate(t.ticker, t.indicators) || {};
        results[t.id] = {
          signal: out.signal || 'HOLD',
          confidence: typeof out.confidence === 'number' ? out.confidence : 50,
          factors: out.factors || {},
          add: !!out.add  // strategy requests an add-on to an existing position
        };
      } catch (e) {
        results[t.id] = {signal: 'HOLD', confidence: 50, error: String(e)};
      }
    }
    // Collect non-reserved fields back as persisted state
    const state = {};
    for (const k of Object.keys(strat)) {
      if (RESERVED.indexOf(k) < 0) state[k] = strat[k];
    }
    process.stdout.write(JSON.stringify({signals: results, state: state}));
  } catch (e) {
    process.stdout.write(JSON.stringify({error: String(e)}));
  }
});
