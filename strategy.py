import numpy as np
import pandas as pd

# --------------------------
# FUNCIONES AUXILIARES
# --------------------------
def body(c):
    return abs(c["close"] - c["open"])

def range_c(c):
    r = c["high"] - c["low"]
    return r if r != 0 else 0.0001

def mecha_superior(c):
    return c["high"] - max(c["open"], c["close"])

def mecha_inferior(c):
    return min(c["open"], c["close"]) - c["low"]

def centro_vela(c):
    return (c["high"] + c["low"]) / 2

def bullish(c):
    return c["close"] > c["open"]

def bearish(c):
    return c["close"] < c["open"]

# --------------------------
# SOPORTE Y RESISTENCIA
# --------------------------
def hay_zona_fuerte(df, precio_actual, rango_busqueda=12, tolerancia=0.001):
    if len(df) < rango_busqueda:
        return False
    maximos = df["high"].iloc[-rango_busqueda:-1].values
    minimos = df["low"].iloc[-rango_busqueda:-1].values
    for nivel in maximos:
        if abs(precio_actual - nivel) <= tolerancia:
            return True
    for nivel in minimos:
        if abs(precio_actual - nivel) <= tolerancia:
            return True
    return False

# --------------------------
# DETECCIÓN DE AGOTAMIENTO
# --------------------------
def es_agotamiento(c):
    cuerpo = body(c)
    rango = range_c(c)
    if cuerpo < rango * 0.35:
        return True
    if mecha_superior(c) > cuerpo * 0.6 or mecha_inferior(c) > cuerpo * 0.6:
        return True
    distancia_centro = abs(c["close"] - centro_vela(c))
    if distancia_centro < rango * 0.2:
        return True
    return False

# --------------------------
# ANÁLISIS DE EVOLUCIÓN
# --------------------------
def analizar_evolucion_vela(c):
    cuerpo = body(c)
    rango = range_c(c)
    if rango == 0:
        return 0
    if bullish(c):
        cierre_alto = (c["close"] - c["low"]) / rango >= 0.75
        apertura_baja = (c["open"] - c["low"]) / rango <= 0.35
        if cierre_alto and apertura_baja:
            return 25
    if bearish(c):
        cierre_bajo = (c["high"] - c["close"]) / rango >= 0.75
        apertura_alta = (c["high"] - c["open"]) / rango <= 0.35
        if cierre_bajo and apertura_alta:
            return 25
    return 0

# --------------------------
# SEÑAL FINAL
# --------------------------
def get_reversal_signal(df):
    if df is None or df.empty or len(df) < 40:
        return None

    df = df.copy()
    df["ema5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema13"] = df["close"].ewm(span=13, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    c1 = df.iloc[-1]
    c2 = df.iloc[-2]
    c3 = df.iloc[-3]

    if es_agotamiento(c1):
        return None

    if hay_zona_fuerte(df, c1["close"]):
        return None

    fuerza = 0
    fuerza += analizar_evolucion_vela(c1)

    if bullish(c1) and c1["close"] > c2["high"] and c1["close"] > c3["high"]:
        fuerza += 20
    if bearish(c1) and c1["close"] < c2["low"] and c1["close"] < c3["low"]:
        fuerza += 20

    tendencia_alcista = df["ema5"].iloc[-1] > df["ema13"].iloc[-1] > df["ema21"].iloc[-1]
    tendencia_bajista = df["ema5"].iloc[-1] < df["ema13"].iloc[-1] < df["ema21"].iloc[-1]

    if bullish(c1) and tendencia_alcista:
        fuerza += 20
    if bearish(c1) and tendencia_bajista:
        fuerza += 20

    if body(c1) > body(c2) * 1.1:
        fuerza += 15

    fuerza = min(fuerza, 100)

    if fuerza >= 80:
        if bullish(c1):
            return ("call", fuerza, "ALCISTA VÁLIDA")
        elif bearish(c1):
            return ("put", fuerza, "BAJISTA VÁLIDA")

    return None
