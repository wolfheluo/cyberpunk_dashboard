#!/usr/bin/env node
// Frontend XSS-defence tests — spec D18 (M-6/N-15).
// Seams (pre-agreed in spec): esc() behaviour is a pure function (node unit);
// unescaped interpolation points are scanned statically (spec: "static checks").
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.join(__dirname, '..', 'dashboard', 'js', 'app.js');
const src = fs.readFileSync(APP, 'utf-8');

let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log(`  ok   ${name}`); }
  else { failed++; console.log(`  FAIL ${name}${detail ? ' — ' + detail : ''}`); }
}

// ---- esc() behaviour (pure function extracted via vm, no DOM needed) ----
const escMatch = src.match(/function esc\(s\)\{[^}]+\}/);
check('esc() definition found', !!escMatch, 'function esc(s) not found in app.js');
if (escMatch) {
  const sandbox = {};
  vm.runInNewContext(escMatch[0] + ';this.esc = esc;', sandbox);
  const esc = sandbox.esc;
  check('esc("&") -> &amp;', esc('&') === '&amp;', esc('&'));
  check('esc("<") -> &lt;', esc('<') === '&lt;', esc('<'));
  check('esc(">") -> &gt;', esc('>') === '&gt;', esc('>'));
  check('esc(\'"\') -> &quot; (D18)', esc('"') === '&quot;', esc('"'));
  check('esc("\'") -> &#39; (D18)', esc("'") === '&#39;', esc("'"));
  check('esc("<img src=x onerror=alert(1)>") fully escaped',
    esc('<img src=x onerror=alert(1)>') === '&lt;img src=x onerror=alert(1)&gt;',
    esc('<img src=x onerror=alert(1)>'));
  check('esc(null) -> ""', esc(null) === '', esc(null));
  check('esc(undefined) -> ""', esc(undefined) === '', esc(undefined));
}

// ---- Static scan: every user/server-controlled interpolation must go through esc() ----
function lineOf(substr) {
  const i = src.indexOf(substr);
  if (i === -1) return null;
  return src.slice(0, i).split('\n').length;
}

// loadSymbols: symbol + name escaped, including the data-sym attribute (quote breakout)
const symLine = lineOf("data-sym=\"'+");
check('loadSymbols escapes s.symbol', symLine !== null && src.slice(src.indexOf("data-sym=\"'+"), src.indexOf("data-sym=\"'+") + 300).includes('esc(s.symbol)'),
  's.symbol must pass esc() incl. data-sym attribute');
check('loadSymbols escapes s.name', src.slice(src.indexOf('symbolList'), src.indexOf('symbolList') + 900).includes('esc(s.name)'),
  's.name must pass esc()');

// loadStrategyList: strategy NAME/DESCRIPTION
const sListIdx = src.indexOf("el.innerHTML+='<div class=\"panel p-2 hover:border-[#00E5FF40]");
check('loadStrategyList escapes s.name', sListIdx !== -1 && src.slice(sListIdx, sListIdx + 900).includes('esc(s.name)'));
check('loadStrategyList escapes s.description', sListIdx !== -1 && src.slice(sListIdx, sListIdx + 900).includes('esc(s.description)'));

// loadStrategies: option text
const optIdx = src.indexOf("as.innerHTML+='<option value=\"'+ss.filename+'\"'");
check('loadStrategies escapes ss.name', optIdx !== -1 && src.slice(optIdx, optIdx + 200).includes('esc(ss.name)'));

// renderBacktestResults: strategy name + symbol
const btIdx = src.indexOf("div.innerHTML='<div class=\"flex items-center justify-between p-2 cursor-pointer");
check('renderBacktestResults escapes sn', btIdx !== -1 && src.slice(btIdx, btIdx + 800).includes('esc(sn)'));
const btRowIdx = src.indexOf("items.map(function(b){");
check('renderBacktestResults escapes b.symbol', btRowIdx !== -1 && src.slice(btRowIdx, btRowIdx + 600).includes('esc(b.symbol)'));

// renderRecentTrades: symbol/side/quantity
const rtIdx = src.indexOf("el.innerHTML+='<div class=\"flex gap-3 border-b");
check('renderRecentTrades escapes t.symbol', rtIdx !== -1 && src.slice(rtIdx, rtIdx + 500).includes('esc(t.symbol)'));
check('renderRecentTrades escapes t.side', rtIdx !== -1 && src.slice(rtIdx, rtIdx + 500).includes('esc(t.side)'));
check('renderRecentTrades escapes t.quantity', rtIdx !== -1 && src.slice(rtIdx, rtIdx + 500).includes('esc(t.quantity)'));

// renderPositionsBar
const pbIdx = src.indexOf("pb.innerHTML+='<span class=\"text-[#00E5FF]\">'+esc(p.symbol)+'</span>");
check('renderPositionsBar escapes p.symbol', pbIdx !== -1, 'esc(p.symbol) missing in positions bar row');
check('renderPositionsBar escapes p.side', pbIdx !== -1 && src.slice(pbIdx, pbIdx + 400).includes('esc(p.side)'));

// renderSignalTable: confidence
const sigIdx = src.indexOf("st.innerHTML+='<div class=\"flex items-center gap-3 py-0.5");
check('renderSignalTable escapes tk.confidence', sigIdx !== -1 && src.slice(sigIdx, sigIdx + 600).includes('esc(tk.confidence)'));

// account tables: positions + cycles
const posTblIdx = src.indexOf("pos.positions.map(function(p){");
check('account positions escapes p.symbol', posTblIdx !== -1 && src.slice(posTblIdx, posTblIdx + 700).includes('esc(p.symbol)'));
check('account positions escapes p.side', posTblIdx !== -1 && src.slice(posTblIdx, posTblIdx + 700).includes('esc(p.side)'));
check('account positions escapes p.strategy', posTblIdx !== -1 && src.slice(posTblIdx, posTblIdx + 700).includes('esc(p.strategy'));
const cycIdx = src.indexOf("pf.cycles.map(function(c){");
check('account cycles escapes c.symbol', cycIdx !== -1 && src.slice(cycIdx, cycIdx + 2500).includes('esc(c.symbol)'));
check('account cycles escapes c.strategy', cycIdx !== -1 && src.slice(cycIdx, cycIdx + 2500).includes('esc(c.strategy'));

// exec log: side + confidence param
check('logLine escapes sc (side)', /pre\+' '\+esc\(sc\)/.test(src));
check('logLine escapes confidence param', /I18n\.t\('log_conf',\{c:esc\(e\.confidence\)\}\)/.test(src) || /I18n\.t\('log_conf',\{c:Number\(e\.confidence\)\}\)/.test(src));

// pipeline node radius must compare by index, not translated label (zh bug)
check('pipeline uses index comparison, not label', !/nd\.label==='SIGNAL'/.test(src));

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
