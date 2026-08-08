({
  NAME: "Price Momentum Burst",
  DESCRIPTION: "純價格動能：tick 間價格變化 > 0.1% 觸發做多/做空。live 為秒級（1s poll），backtest 為日線級。",
  priceHistory: {},

  evaluate: function (ticker, indicators) {
    // Per-symbol history — the strategy object is shared across all symbols.
    if (!this.priceHistory[ticker.id]) this.priceHistory[ticker.id] = [];
    var hist = this.priceHistory[ticker.id];
    hist.push({ time: Date.now(), price: ticker.price });
    if (hist.length > 30) hist.shift();

    if (hist.length < 2) {
      return { signal: "WAIT", confidence: 0, factors: { reason: "accumulating" } };
    }

    // Momentum = change vs previous tick (interval = data cadence:
    // ~1s in live trading, 1 day in backtests). No wall-clock filtering —
    // the engine calls evaluate() once per tick, so previous entry IS the
    // previous tick.
    var prev = hist[hist.length - 2].price;
    var priceChangePct = ((ticker.price - prev) / prev) * 100;
    var THRESHOLD = 0.10;

    if (priceChangePct >= THRESHOLD) {
      return { signal: "BUY", confidence: 85, factors: { momentum_pct: priceChangePct, trigger_price: ticker.price } };
    }
    if (priceChangePct <= -THRESHOLD) {
      return { signal: "SELL", confidence: 85, factors: { momentum_pct: priceChangePct, trigger_price: ticker.price } };
    }
    return { signal: "HOLD", confidence: 50, factors: { momentum_pct: priceChangePct } };
  }
})
