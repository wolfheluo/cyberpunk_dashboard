"""
Multi-Timeframe Momentum Strategy
BUY:  RSI(1h) < 45 AND price > SMA(4h,20) AND vol > 1.2x avg
SELL: RSI(1h) > 65 OR price < SMA(4h,20) * 0.98
"""
NAME = "Multi-TF Momentum"
DESCRIPTION = "RSI(14) 1h + SMA(20) 4h crossover + volume confirmation"

def evaluate(ticker, indicators):
    rsi = indicators.get("rsi_1h", 50)
    price = ticker["price"]
    sma_4h = indicators.get("sma_4h", price)
    vol_surge = indicators.get("vol_surge", False)

    rsi_factor = 1.0 - (rsi / 100.0)
    sma_factor = min(1.0, max(0.0, (price - sma_4h) / (sma_4h * 0.05 + 0.0001)))
    sma_factor = (sma_factor + 1.0) / 2.0
    vol_factor = 1.0 if vol_surge else 0.4
    composite = rsi_factor * 0.45 + sma_factor * 0.30 + vol_factor * 0.25

    if rsi < 45 and price > sma_4h and vol_surge:
        signal, confidence = "BUY", int(composite * 100)
    elif rsi > 65 or price < sma_4h * 0.98:
        signal, confidence = "SELL", int((1.0 - composite) * 100)
    elif price > sma_4h and rsi < 55:
        signal, confidence = "HOLD", int(composite * 80)
    else:
        signal, confidence = "WAIT", int(composite * 60)

    return {"signal": signal, "confidence": max(10, min(99, confidence)),
            "composite": round(composite, 3), "factors": {"rsi": rsi, "sma_4h": sma_4h, "vol_surge": vol_surge}}
