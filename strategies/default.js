({
  NAME: "Simple RSI Strategy",
  DESCRIPTION: "RSI < 30 BUY, RSI > 70 SELL",
  evaluate: function (ticker, indicators) {
    var rsi = indicators.rsi;
    if (rsi < 30) return {signal: "BUY", confidence: 80, factors: {rsi: rsi}};
    if (rsi > 70) return {signal: "SELL", confidence: 75, factors: {rsi: rsi}};
    return {signal: "HOLD", confidence: 50};
  }
})