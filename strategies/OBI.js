({
  NAME: "Momentum & OBI Scalping v2",
  DESCRIPTION: "秒級動能 + 盤口失衡(OBI)進場。持倉管理：虧損>2%停損、盈利>3%了結、盈利且動能續強加倉(add, 30s cooldown)。注意：backtest 無盤口資料，進場條件(OBI)僅 live 有效。",
  tickHistory: {},
  lastAdd: {},

  evaluate: function (ticker, indicators) {
    // --- 1. Per-symbol momentum (previous tick vs this tick) ---
    if (!this.tickHistory[ticker.id]) this.tickHistory[ticker.id] = [];
    var hist = this.tickHistory[ticker.id];
    hist.push({ time: Date.now(), price: ticker.price });
    if (hist.length > 30) hist.shift();
    if (hist.length < 2) {
      return { signal: "WAIT", confidence: 0, factors: { reason: "accumulating" } };
    }
    var prev = hist[hist.length - 2].price;
    var momentumPct = ((ticker.price - prev) / prev) * 100;

    // --- 2. Order book imbalance (live only; 0 in backtests) ---
    var obi = (ticker.book && ticker.book.imbalance !== undefined) ? ticker.book.imbalance : 0;

    var pos = ticker.position;
    var f = { momentum_pct: momentumPct, obi: obi };

    // --- 3. No position: entry on momentum burst + OBI confirmation ---
    if (!pos) {
      if (momentumPct >= 0.05 && obi >= 0.6) {
        f.action = "open_long";
        return { signal: "BUY", confidence: 80, factors: f };
      }
      if (momentumPct <= -0.05 && obi <= -0.6) {
        f.action = "open_short";
        return { signal: "SELL", confidence: 80, factors: f };
      }
      f.action = "wait_entry";
      return { signal: "HOLD", confidence: 50, factors: f };
    }

    // --- 4. Position held: manage by performance ---
    var entry = pos.entry_price, side = pos.side;
    var pnlPct = side === "BUY"
      ? ((ticker.price - entry) / entry) * 100
      : ((entry - ticker.price) / entry) * 100;
    f.pnl_pct = pnlPct;
    f.position = side;

    // 4a. Stop loss: -2% → close the position
    if (pnlPct <= -2) {
      f.action = "stop_loss";
      return { signal: side === "BUY" ? "SELL" : "BUY", confidence: 90, factors: f };
    }
    // 4b. Take profit: +3% → close the position
    if (pnlPct >= 3) {
      f.action = "take_profit";
      return { signal: side === "BUY" ? "SELL" : "BUY", confidence: 85, factors: f };
    }
    // 4c. Profit + momentum continues in our direction + OBI agrees → add-on
    var addOk = (side === "BUY" && momentumPct > 0.03 && obi >= 0.5) ||
                (side === "SELL" && momentumPct < -0.03 && obi <= -0.5);
    if (addOk && pnlPct > 0) {
      if (!this.lastAdd[ticker.id] || Date.now() - this.lastAdd[ticker.id] > 30000) {
        this.lastAdd[ticker.id] = Date.now();
        f.action = "add_on";
        return { signal: side, confidence: 75, factors: f, add: true };
      }
      f.action = "add_cooldown";
      return { signal: "HOLD", confidence: 60, factors: f };
    }
    // 4d. Otherwise hold
    f.action = "hold";
    return { signal: "HOLD", confidence: 50, factors: f };
  }
})
