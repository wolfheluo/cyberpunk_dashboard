({
  NAME: "Grid Trading",
  DESCRIPTION: "對稱網格（多空皆可）：以現價為中心 ±10%（每格 0.1% = 每邊 100 格）。無倉時由首個 0.1% 方向決定 — 跌開多、漲開空。多頭網格：續跌每格加多（遠格遞增 2%×(1+0.25×(d-1))，上限 6%）、回彈每格平多；空頭網格：續漲每格加空、回跌每格回補。穿出區間認賠全平後以現價重建。",
  GRID_LEVELS: 100,   // ±10% / 0.1% per level
  GRID_STEP_PCT: 0.1,
  BASE_SIZE_PCT: 0.02,
  SIZE_GROWTH: 0.25,
  MAX_SIZE_PCT: 0.06,
  grids: {},

  lotSize: function (depth) {
    return Math.min(this.BASE_SIZE_PCT * (1 + this.SIZE_GROWTH * (depth - 1)), this.MAX_SIZE_PCT);
  },

  evaluate: function (ticker, indicators) {
    var sym = ticker.id, price = ticker.price, pos = ticker.position;
    var g = this.grids[sym];
    var L = this.GRID_LEVELS;

    // --- Flat: arm the grid; the first 0.1% move picks the direction ---
    if (!pos) {
      if (!g) {
        this.grids[sym] = { center: price, step: price * this.GRID_STEP_PCT / 100, side: null, levels: 0, last: price };
        return { signal: "HOLD", confidence: 50, factors: { action: "grid_arm", grid: 0 } };
      }
      var gp = Math.round((price - g.center) / g.step);
      if (gp <= -1) {  // fell below center → LONG grid
        g.side = "long"; g.levels = 1; g.last = price;
        return { signal: "BUY", confidence: 80, factors: { action: "grid_open_long", grid: gp },
                 size_pct: this.lotSize(1) };
      }
      if (gp >= 1) {  // rose above center → SHORT grid
        g.side = "short"; g.levels = 1; g.last = price;
        return { signal: "SELL", confidence: 80, factors: { action: "grid_open_short", grid: gp },
                 size_pct: this.lotSize(1) };
      }
      return { signal: "HOLD", confidence: 50, factors: { action: "grid_arm", grid: gp } };
    }

    // --- Position held (recover grid if the state was lost) ---
    if (!g || !g.side) {
      this.grids[sym] = { center: price, step: price * this.GRID_STEP_PCT / 100,
                          side: pos.side === "BUY" ? "long" : "short", levels: 1, last: price };
      return { signal: "HOLD", confidence: 50, factors: { action: "grid_hold", grid: 0 } };
    }
    var gridPos = Math.round((price - g.center) / g.step);
    var factors = { action: "grid_hold", grid: gridPos };

    if (g.side === "long") {
      // buy one more level as price falls
      var buyLevel = -g.levels - 1;
      if (gridPos <= buyLevel && buyLevel >= -L) {
        g.levels += 1; g.last = price;
        factors.action = "grid_buy";
        return { signal: "BUY", confidence: 80, factors: factors, add: true,
                 size_pct: this.lotSize(g.levels) };
      }
      // sell one lot as price recovers
      var sellLevel = -g.levels + 1;
      if (gridPos >= sellLevel) {
        var closePct = 1 / g.levels;   // sequence 1/4→1/3→1/2→1 drains exactly
        g.levels -= 1; g.last = price;
        factors.action = "grid_sell";
        if (g.levels === 0) this.grids[sym] = null; // drained — rebuild on next flat
        return { signal: "SELL", confidence: 80, factors: factors, close_pct: closePct };
      }
      // broke through the bottom → cut losses, rebuild
      if (gridPos < -L) {
        this.grids[sym] = null;
        factors.action = "grid_stopout";
        return { signal: "SELL", confidence: 70, factors: factors };
      }
    } else {  // short grid (mirror)
      // add one more short as price rises
      var addLevel = g.levels + 1;
      if (gridPos >= addLevel && addLevel <= L) {
        g.levels += 1; g.last = price;
        factors.action = "grid_short_add";
        return { signal: "SELL", confidence: 80, factors: factors, add: true,
                 size_pct: this.lotSize(g.levels) };
      }
      // cover one lot as price falls back
      var coverLevel = g.levels - 1;
      if (gridPos <= coverLevel) {
        var closePct = 1 / g.levels;   // 1/4→1/3→1/2→1 drains exactly
        g.levels -= 1; g.last = price;
        factors.action = "grid_cover";
        if (g.levels === 0) this.grids[sym] = null;
        return { signal: "BUY", confidence: 80, factors: factors, close_pct: closePct };
      }
      // broke through the top → cut losses, rebuild
      if (gridPos > L) {
        this.grids[sym] = null;
        factors.action = "grid_stopout";
        return { signal: "BUY", confidence: 70, factors: factors };
      }
    }
    return { signal: "HOLD", confidence: 50, factors: factors };
  }
})
