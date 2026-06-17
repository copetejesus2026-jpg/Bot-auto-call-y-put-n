import numpy as np
import pandas as pd

def body(c): return abs(c["close"] - c["open"])
def range_c(c): r = c["high"] - c["low"]; return r if r != 0 else 0.0001
def mecha_superior(c): return c["high"] - max(c["open"], c["close"])
def mecha_inferior(c): return min(c["open"], c["close"]) - c["low"]
def bullish(c): return c["close"] > c["open"]
def bearish(c): return c["close"] < c["open"]

def obtener_niveles_clave(df, rango=12):
    maximos = []; minimos = []
    for i in range(2, len(df)-2):
        if df["high"].iloc[i]>df["high"].iloc[i-1] and df["high"].iloc[i]>df["high"].iloc[i+1]: maximos.append(df["high"].iloc[i])
        if df["low"].iloc[i]<df["low"].iloc[i-1] and df["low"].iloc[i]<df["low"].iloc[i+1]: minimos.append(df["low"].iloc[i])
    return maximos[-rango:], minimos[-rango:]

def precio_toco_nivel(precio, niveles, tolerancia=0.0008):
    for n in niveles:
        if abs(precio - n) <= tolerancia: return n
    return None

def es_reversion_exacta(c, nivel):
    rango = range_c(c)
    if rango == 0: return False, ""
    # Rechazo fuerte
    if abs(c["high"] - nivel) <= 0.0008 and c["close"] < nivel - (rango*0.25):
        return True, "RECHAZO RESISTENCIA"
    if abs(c["low"] - nivel) <= 0.0008 and c["close"] > nivel + (rango*0.25):
        return True, "RECHAZO SOPORTE"
    # Respeto y cierre justo
    if abs(c["close"] - nivel) <= 0.0008:
        if bullish(c) and c["open"] < nivel: return True, "RESPETO SOPORTE"
        if bearish(c) and c["open"] > nivel: return True, "RESPETO RESISTENCIA"
    return False, ""

def es_agotamiento(c):
    cuerpo = body(c); rango = range_c(c)
    if cuerpo < rango*0.4: return True
    if mecha_superior(c) > cuerpo*0.55 or mecha_inferior(c) > cuerpo*0.55: return True
    return False

def get_reversal_signal(df):
    if df is None or len(df) < 40: return None
    df = df.copy()
    df["ema5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema13"] = df["close"].ewm(span=13, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    c1 = df.iloc[-1]
    if es_agotamiento(c1): return None
    resistencias, soportes = obtener_niveles_clave(df)
    # Reversión bajista
    n_res = precio_toco_nivel(c1["high"], resistencias)
    if n_res:
        ok, tipo = es_reversion_exacta(c1, n_res)
        if ok:
            tend_baja = df["ema5"].iloc[-1] < df["ema13"].iloc[-1] < df["ema21"].iloc[-1]
            return ("put", 85 if tend_baja else 80, tipo)
    # Reversión alcista
    n_sop = precio_toco_nivel(c1["low"], soportes)
    if n_sop:
        ok, tipo = es_reversion_exacta(c1, n_sop)
        if ok:
            tend_alta = df["ema5"].iloc[-1] > df["ema13"].iloc[-1] > df["ema21"].iloc[-1]
            return ("call", 85 if tend_alta else 80, tipo)
    return None
