"""
Trend Following Strategy (EMA Crossover)
BUY:  EMA(12) crosses above EMA(26) + price > EMA(50)
SELL: EMA(12) crosses below EMA(26)
"""
NAME = "EMA Crossover Trend"
DESCRIPTION = "EMA(12/26) crossover + EMA(50) filter — follow the trend"

def evaluate(ticker, indicators):
    price = ticker["price"]
    ema_12 = indicators.get("ema_12", price)
    ema_26 = indicators.get("ema_26", price)
    ema_50 = indicators.get("sma_4h", price)  # reuse SMA 4h as long-term filter

    # Normalize to 0-1: where price sits between ema_12 and ema_26
    ema_range = abs(ema_12 - ema_26) + 0.0001
    ema_position = (price - ema_26) / ema_range
    ema_position = max(-1.0, min(2.0, ema_position))

    rsi = indicators.get("rsi_1h", 50)
    rsi_factor = min(1.0, max(0.0, 1.0 - rsi / 100.0))

    composite = (ema_position * 0.6 + rsi_factor * 0.4)
    composite = max(0.0, min(1.0, (composite + 0.5)))

    if ema_12 > ema_26 and price > ema_50:
        signal, confidence = "BUY", int(composite * 100)
    elif ema_12 < ema_26:
        signal, confidence = "SELL", int((1.0 - composite) * 100)
    elif price > ema_50:
        signal, confidence = "HOLD", int(composite * 70)
    else:
        signal, confidence = "WAIT", int(50)

    return {"signal": signal, "confidence": max(10, min(99, confidence)),
            "composite": round(composite, 3), "factors": {"ema_12": round(ema_12, 4), "ema_26": round(ema_26, 4), "ema_50": round(ema_50, 4)}}
