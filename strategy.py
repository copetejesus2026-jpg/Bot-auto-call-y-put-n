import numpy as np
import pandas as pd

def body(c):
    return abs(c["close"] - c["open"])

def range_c(c):
    r = c["high"] - c["low"]
    return r if r != 0 else 0.0001

def mecha_superior(c):
    return c["high"] - max(c["open"], c["close"])

def mecha_inferior(c):
    return min(c["open"], c["close"]) - c["low"]

def bullish(c):
    return c["close"] > c["open"]

def bearish(c):
    return c["close"] < c["open"]

def vela_agotamiento(c):
    """Devuelve True si es vela de agotamiento / reversión"""
    cuerpo = body(c)
    if cuerpo == 0:
        return True
    mecha_sup = mecha_superior(c)
    mecha_inf = mecha_inferior(c)
    # Si alguna mecha es más del 40% del cuerpo → agotamiento
    if mecha_sup > cuerpo * 0.4 or mecha_inf > cuerpo * 0.4:
        return True
    # Si cuerpo es grande pero cierra muy cerca del centro → indecisión
    if cuerpo / range_c(c) >= 0.6 and abs(c["close"] - ((c["high"] + c["low"])/2)) < (range_c(c) * 0.15):
        return True
    return False

def get_reversal_signal(df):
    if df is None or df.empty or len(df) < 50:
        return None

    df = df.copy()
    # EMAs para tendencia
    df["ema5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema13"] = df["close"].ewm(span=13, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    c1 = df.iloc[-1]   # vela actual cerrada
    c2 = df.iloc[-2]
    c3 = df.iloc[-3]

    # ❌ RECHAZAR INMEDIATAMENTE SI ES VELA DE AGOTAMIENTO
    if vela_agotamiento(c1):
        return None

    fuerza = 0

    # 🔹 1. Cuerpo sólido (mínimo 70% del rango total)
    if body(c1) / range_c(c1) >= 0.70:
        fuerza += 25

    # 🔹 2. Rompe máximo/mínimo anterior y CIERRA en la zona extrema
    if bullish(c1) and c1["close"] > c2["high"] and c1["close"] > c3["high"] and c1["close"] > (c1["high"] - range_c(c1)*0.1):
        fuerza += 25
    if bearish(c1) and c1["close"] < c2["low"] and c1["close"] < c3["low"] and c1["close"] < (c1["low"] + range_c(c1)*0.1):
        fuerza += 25

    # 🔹 3. Tendencia clara y a favor
    tendencia_alcista = (df["ema5"].iloc[-1] > df["ema13"].iloc[-1] > df["ema21"].iloc[-1] > df["ema50"].iloc[-1])
    tendencia_bajista = (df["ema5"].iloc[-1] < df["ema13"].iloc[-1] < df["ema21"].iloc[-1] < df["ema50"].iloc[-1])

    if bullish(c1) and tendencia_alcista:
        fuerza += 25
    if bearish(c1) and tendencia_bajista:
        fuerza += 25

    # 🔹 4. Mechas cortas (máximo 20% del cuerpo)
    if mecha_superior(c1) < body(c1)*0.2 and mecha_inferior(c1) < body(c1)*0.2:
        fuerza += 15

    # 🔹 5. Impulso mayor que la vela anterior
    if body(c1) > body(c2)*1.15:
        fuerza += 10

    fuerza = min(fuerza, 100)

    # 🎯 Solo señales muy fuertes y sin agotamiento
    if fuerza >= 85:
        if bullish(c1):
            return ("call", fuerza, "ALCISTA FUERTE - SIN AGOTAMIENTO")
        elif bearish(c1):
            return ("put", fuerza, "BAJISTA FUERTE - SIN AGOTAMIENTO")

    return None
