"""
Mean Reversion Strategy (Bollinger Bands)
BUY:  price < lower_band AND RSI < 35
SELL: price > upper_band AND RSI > 65
"""
NAME = "Bollinger Mean Reversion"
DESCRIPTION = "Bollinger Bands (20,2) + RSI(14) — buy at lower band, sell at upper"

def evaluate(ticker, indicators):
    price = ticker["price"]
    rsi = indicators.get("rsi_1h", 50)
    sma_20 = indicators.get("sma_1h_20", price)
    closes = indicators.get("closes_1h", [price])

    # Bollinger Bands (20,2)
    if len(closes) >= 20:
        std = (sum((c - sma_20) ** 2 for c in closes[-20:]) / 20) ** 0.5
        upper = sma_20 + 2 * std
        lower = sma_20 - 2 * std
        bb_position = (price - lower) / (upper - lower + 0.0001)
    else:
        upper = price * 1.05
        lower = price * 0.95
        bb_position = 0.5

    bb_position = max(0.0, min(1.0, bb_position))

    if price < lower and rsi < 35:
        signal, confidence = "BUY", int((1 - bb_position) * 100)
    elif price > upper and rsi > 65:
        signal, confidence = "SELL", int(bb_position * 100)
    elif rsi < 45 and bb_position < 0.3:
        signal, confidence = "HOLD", int((0.5 - bb_position) * 80 + 40)
    else:
        signal, confidence = "WAIT", int(50)

    return {"signal": signal, "confidence": max(10, min(99, confidence)),
            "composite": round(bb_position, 3), "factors": {"rsi": rsi, "bb_upper": round(upper, 4), "bb_lower": round(lower, 4)}}
