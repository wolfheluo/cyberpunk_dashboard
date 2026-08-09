#!/usr/bin/env node
// T-C frontend fixes — N-9/N-10/N-11/N-12.
// Seams (pre-agreed): static checks on app.js/HTML for the four fixes.
'use strict';
const fs = require('fs');
const path = require('path');

const APP = path.join(__dirname, '..', 'dashboard', 'js', 'app.js');
const HTML = path.join(__dirname, '..', 'dashboard', 'cyberpunk_dashboard.html');
const src = fs.readFileSync(APP, 'utf-8');
const html = fs.readFileSync(HTML, 'utf-8');

let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log(`  ok   ${name}`); }
  else { failed++; console.log(`  FAIL ${name}${detail ? ' — ' + detail : ''}`); }
}

// N-9: client hot-reload passes RAW volume (server units), not volume_m.
// The old code passed ticker.volume_m (millions); the server passes raw
// quoteVolume. Strategies must see the same units in both contexts.
check('N-9 no bare volume_m passed to strategy',
  !/volume:\s*ticker\.volume_m(?!\s*\*)/.test(src),
  'evaluateJSStrategy still passes volume_m without restoring ×1e6');
check('N-9 volume restored to raw units',
  /volume:\s*\(?ticker\.volume_m\s*\*\s*1e6/.test(src),
  'expected volume: ticker.volume_m * 1e6 (or equivalent) in evaluateJSStrategy');

// N-10: no Math.min.apply / Math.max.apply (stack overflow on huge arrays).
const minApply = (src.match(/Math\.min\.apply/g) || []).length;
const maxApply = (src.match(/Math\.max\.apply/g) || []).length;
check('N-10 no Math.min.apply', minApply === 0, `${minApply} remaining`);
check('N-10 no Math.max.apply', maxApply === 0, `${maxApply} remaining`);

// N-11: renderExecLog only auto-scrolls when already pinned to the bottom.
check('N-11 has wasPinned check', /wasPinned|wasAtBottom|isPinned/.test(src),
  'no pinned-to-bottom guard found');
check('N-11 scroll guarded by pinned check',
  /if\s*\(\s*wasPinned/.test(src),
  'scrollTop assignment not guarded');

// N-12: HTML title has no version number (git tags own the version).
check('N-12 title has no version', !/QUANT FLEET v\d/.test(html),
  'title still carries a hardcoded version');

console.log(`\n${failed === 0 ? 'ALL PASS' : failed + ' FAILED'} (${passed} passed, ${failed} failed)`);
process.exit(failed === 0 ? 0 : 1);
