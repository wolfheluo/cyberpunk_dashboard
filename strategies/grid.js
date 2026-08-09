({
  NAME: "Grid Trading",
  DESCRIPTION: "網格交易（激進版）：以開倉價為中心建立 ±10% 網格（每格 0.1% = 每邊 100 格，0.1% 變動即觸發，不防假訊號）。下跌每穿一格買入（遠格倉位遞增：depth d → size = 2%×(1+0.25×(d-1)) cash，上限 6%），上漲每穿一格賣出一份額（部分平倉），低買高賣賺取格差；穿出網格區間認賠全平後以現價重新建立網格。",
  GRID_LEVELS: 100,  // ±10% / 0.1% per level
  GRID_STEP_PCT: 0.1,
  BASE_SIZE_PCT: 0.02,   // first lot = 2% of cash
  SIZE_GROWTH: 0.25,     // each deeper level adds +25% of the base lot size
  MAX_SIZE_PCT: 0.06,    // cap the deepest lots at 6% of cash
  grids: {},

  lotSize: function (depth) {
    // Lot size as fraction of cash for grid level `depth` (1 = first lot below center).
    return Math.min(this.BASE_SIZE_PCT * (1 + this.SIZE_GROWTH * (depth - 1)), this.MAX_SIZE_PCT);
  },

  evaluate: function (ticker, indicators) {
    var sym = ticker.id, price = ticker.price, pos = ticker.position;
    var g = this.grids[sym];
    var L = this.GRID_LEVELS;

    // --- Flat: open the base position immediately, centered on the current
    // price (aggressive mode — no waiting for a 0.1% drop to arm the grid). ---
    if (!pos) {
      this.grids[sym] = { center: price, step: price * this.GRID_STEP_PCT / 100, idx: 0, last: price };
      return { signal: "BUY", confidence: 80, factors: { action: "grid_open", grid: 0 },
               size_pct: this.lotSize(1) };
    }

    // --- Position held: grid operations ---
    if (!g) {
      this.grids[sym] = { center: price, step: price * this.GRID_STEP_PCT / 100, idx: -1, last: price };
      return { signal: "HOLD", confidence: 50, factors: { action: "grid_hold", grid: 0 } };
    }
    var gridPos = Math.round((price - g.center) / g.step);
    var factors = { action: "grid_hold", grid: gridPos };

    // No hysteresis: every 0.1% move acts immediately (aggressive mode).
    // idx = the DEEPEST held grid level (0 = base position at center, -1 =
    // one buy below, ...). Move AT MOST one level per tick — a fast move is
    // caught by subsequent ticks, keeping the lot math exact.
    var buyLevel = g.idx - 1;   // price must fall one more level to buy
    if (gridPos <= buyLevel && buyLevel >= -L && gridPos >= -L) {  // stay inside the grid
      g.idx -= 1; g.last = price;
      factors.action = "grid_buy";
      // Deeper levels buy larger lots (pyramid): depth = |idx|+1
      return { signal: "BUY", confidence: 80, factors: factors, add: true,
               size_pct: this.lotSize(-g.idx + 1) };
    }
    var sellLevel = g.idx + 1;  // price rose back one level above the deepest holding
    if (gridPos >= sellLevel && g.idx <= 0) {
      // Held |idx|+1 lots (levels idx..0) → sell one lot = 1/(|idx|+1).
      // Sequence 1/4 → 1/3 → 1/2 → 1 drains the grid exactly.
      var closePct = 1 / (1 - g.idx);
      g.idx += 1; g.last = price;
      if (g.idx > 0) this.grids[sym] = null; // drained above center — rebuild on next flat
      factors.action = "grid_sell";
      return { signal: "SELL", confidence: 80, factors: factors, close_pct: closePct };
    }
    // Price broke through the bottom of the grid → cut losses, rebuild the grid
    // at the current price so the strategy keeps trading (aggressive mode —
    // never sits idle waiting for a bounce).
    if (gridPos < -L) {
      this.grids[sym] = null; // rebuilt on the next flat signal
      factors.action = "grid_stopout";
      return { signal: "SELL", confidence: 70, factors: factors };
    }
    return { signal: "HOLD", confidence: 50, factors: factors };
  }
})
