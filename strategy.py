import numpy as np
import pandas as pd

def get_reversal_signal(df, tolerancia_nivel=0.0032, ventana_niveles=4):
    if len(df) < 8:
        return None

    df = df.copy()
    df['ema8'] = df['close'].ewm(span=8, adjust=False).mean()
    df['ema13'] = df['close'].ewm(span=13, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

    delta = df['close'].diff()
    gain = delta.where(delta > 0.0, 0.0)
    loss = -delta.where(delta < 0.0, 0.0)
    avg_gain = gain.rolling(window=6, min_periods=1).mean()
    avg_loss = loss.rolling(window=6, min_periods=1).mean().replace(0, 0.0005)
    rs = avg_gain / avg_loss
    df['rsi'] = 100.0 - (100.0 / (1.0 + rs))

    df['macd'] = df['ema13'] - df['ema21']
    df['signal'] = df['macd'].ewm(span=4, adjust=False).mean()

    try:
        e8_1, e13_1, e21_1 = float(df['ema8'].iloc[-1]), float(df['ema13'].iloc[-1]), float(df['ema21'].iloc[-1])
        c1, c2, o1 = float(df['close'].iloc[-1]), float(df['close'].iloc[-2]), float(df['open'].iloc[-1])
        macd1, sig1, rsi1 = float(df['macd'].iloc[-1]), float(df['signal'].iloc[-1]), float(df['rsi'].iloc[-1])
        vol1 = float(df['volume'].iloc[-1])
        vol_prom = float(df['volume'].iloc[-4:-1].mean()) if len(df) >= 5 else (vol1 if vol1 > 0 else 1.0)
    except Exception:
        return None

    fuerza, senal, tipo = 0, None, ""

    if e8_1 > e13_1 and macd1 > sig1 and 30 < rsi1 < 78 and c1 > o1:
        senal, tipo, fuerza = "call", "soporte", 38
        if e13_1 > e21_1: fuerza += 4
        if c1 > c2: fuerza += 4
        if vol1 >= vol_prom * 0.3: fuerza += 4

    if e8_1 < e13_1 and macd1 < sig1 and 22 < rsi1 < 70 and c1 < o1:
        senal, tipo, fuerza = "put", "resistencia", 38
        if e13_1 < e21_1: fuerza += 4
        if c1 < c2: fuerza += 4
        if vol1 >= vol_prom * 0.3: fuerza += 4

    if senal:
        fuerza = max(0, min(fuerza, 100))
        return (senal, fuerza, tipo)
    return None
