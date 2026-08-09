({
  NAME: "Grid Trading",
  DESCRIPTION: "網格交易（激進版）：以進場價為中心建立 ±3% 密集網格（每格 0.1%，0.1% 變動即觸發，不防假訊號）。下跌每穿一格買入（遠格倉位遞增：depth d → size = 2%×(1+0.25×(d-1)) cash，上限 6%），上漲每穿一格賣出一份額（部分平倉），低買高賣賺取格差；穿出網格區間停止交易等待回彈，全部格出清後以現價重新建立網格。",
  GRID_LEVELS: 30,
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

    // --- Flat: build / track the grid ---
    if (!pos) {
      if (!g) {
        this.grids[sym] = { center: price, step: price * this.GRID_STEP_PCT / 100, idx: 0, last: price };
        return { signal: "HOLD", confidence: 50, factors: { action: "grid_armed", grid: 0 } };
      }
      var gp = Math.round((price - g.center) / g.step);
      // Price wandered far above the grid → recenter on current price.
      if (gp > L + 2) {
        this.grids[sym] = { center: price, step: price * this.GRID_STEP_PCT / 100, idx: 0, last: price };
        return { signal: "HOLD", confidence: 50, factors: { action: "grid_recenter", grid: 0 } };
      }
      // Price broke below the first level below center → open the position.
      if (gp <= -1) {
        this.grids[sym].idx = -1;
        this.grids[sym].last = price;
        return { signal: "BUY", confidence: 80, factors: { action: "grid_open", grid: -1 },
                 size_pct: this.lotSize(1) };
      }
      return { signal: "HOLD", confidence: 50, factors: { action: "grid_wait", grid: gp } };
    }

    // --- Position held: grid operations ---
    if (!g) {
      this.grids[sym] = { center: price, step: price * this.GRID_STEP_PCT / 100, idx: -1, last: price };
      return { signal: "HOLD", confidence: 50, factors: { action: "grid_hold", grid: 0 } };
    }
    var gridPos = Math.round((price - g.center) / g.step);
    var factors = { action: "grid_hold", grid: gridPos };

    // No hysteresis: every 0.1% move acts immediately (aggressive mode).
    // idx = lots currently held (negative). Move AT MOST one level per tick —
    // a fast move across several levels is caught by subsequent ticks, so the
    // lot-size math (close_pct = 1/|idx|) always stays correct.
    var buyLevel = g.idx - 1;   // next buy level below current holding
    if (gridPos <= buyLevel && buyLevel >= -L && gridPos >= -L) {  // stay inside the grid
      g.idx -= 1; g.last = price;
      factors.action = "grid_buy";
      // Deeper levels buy larger lots (pyramid): size = base × (1 + growth × (depth-1))
      return { signal: "BUY", confidence: 80, factors: factors, add: true,
               size_pct: this.lotSize(-g.idx) };
    }
    var sellLevel = g.idx + 1;  // next sell level above current holding
    if (gridPos >= sellLevel && g.idx < 0) {
      // Holding |idx| lots → each lot is 1/|idx| of the position.
      // Sequence 1/3 → 1/2 → 1 drains the grid completely.
      var closePct = 1 / (-g.idx);
      g.idx += 1; g.last = price;
      if (g.idx === 0) this.grids[sym] = null; // grid finished — rebuild on next flat
      factors.action = "grid_sell";
      return { signal: "SELL", confidence: 80, factors: factors, close_pct: closePct };
    }
    // Price broke through the bottom of the grid → stop buying, wait for a bounce.
    if (gridPos < -L) {
      g.last = price;
      factors.action = "grid_bottom";
      return { signal: "HOLD", confidence: 60, factors: factors };
    }
    return { signal: "HOLD", confidence: 50, factors: factors };
  }
})
