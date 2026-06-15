import numpy as np
import pandas as pd

def body(c):
    """Tamaño del cuerpo de la vela"""
    return abs(c["close"] - c["open"])

def range_c(c):
    """Rango total alto-bajo de la vela"""
    return c["high"] - c["low"]

def bullish(c):
    """Vela alcista"""
    return c["close"] > c["open"]

def bearish(c):
    """Vela bajista"""
    return c["close"] < c["open"]

def get_reversal_signal(df):
    """
    Estrategia: Fuerza de vela + medias móviles
    Devuelve: (dirección, fuerza, descripción) o None
    """
    if len(df) < 30:
        return None

    df = df.copy()

    # Cálculo de medias móviles exponenciales
    df['ema5'] = df['close'].ewm(span=5, adjust=False).mean()
    df['ema13'] = df['close'].ewm(span=13, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

    # Últimas 2 velas
    c1 = df.iloc[-1]
    c2 = df.iloc[-2]

    fuerza = 0

    # Suma de puntos según condiciones
    if body(c1) >= range_c(c1) * 0.7:
        fuerza += 40

    if c1["close"] > c2["close"]:
        fuerza += 20
    if c1["close"] < c2["close"]:
        fuerza += 20

    if df["ema5"].iloc[-1] > df["ema13"].iloc[-1]:
        fuerza += 20
    if df["ema5"].iloc[-1] < df["ema13"].iloc[-1]:
        fuerza += 20

    if body(c1) > body(c2):
        fuerza += 20

    # Limitar fuerza máxima a 100
    fuerza = min(fuerza, 100)

    # Definir dirección de la operación
    if bullish(c1):
        return ("call", fuerza, "FUERTE ALCISTA")
    if bearish(c1):
        return ("put", fuerza, "FUERTE BAJISTA")

    return None
