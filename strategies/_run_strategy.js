// run_strategy.js — evaluate a JS strategy for many tickers in one node call.
// Usage: echo '<json>' | node run_strategy.js
// Input:  {strategy: "default.js", tickers: [{id, ticker: {...}, indicators: {...}}, ...]}
// Output: {BTC: {signal, confidence, factors}, ...} or {error: "..."}
'use strict';
const fs = require('fs');
const path = require('path');

let input = '';
process.stdin.on('data', d => { input += d; });
process.stdin.on('end', () => {
  try {
    const req = JSON.parse(input);
    const code = fs.readFileSync(path.join(__dirname, req.strategy), 'utf-8');
    const strat = eval(code);
    if (!strat || typeof strat.evaluate !== 'function') throw new Error('strategy has no evaluate()');
    const results = {};
    for (const t of req.tickers || []) {
      try {
        const out = strat.evaluate(t.ticker, t.indicators) || {};
        results[t.id] = {
          signal: out.signal || 'HOLD',
          confidence: typeof out.confidence === 'number' ? out.confidence : 50,
          factors: out.factors || {}
        };
      } catch (e) {
        results[t.id] = {signal: 'HOLD', confidence: 50, error: String(e)};
      }
    }
    process.stdout.write(JSON.stringify(results));
  } catch (e) {
    process.stdout.write(JSON.stringify({error: String(e)}));
  }
});
