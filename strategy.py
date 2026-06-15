import numpy as np
import pandas as pd

def body(c):
    return abs(c["close"] - c["open"])

def range_c(c):
    return c["high"] - c["low"]

def bullish(c):
    return c["close"] > c["open"]

def bearish(c):
    return c["close"] < c["open"]

def get_reversal_signal(df):
    if len(df) < 30:
        return None
    df = df.copy()
    df["ema5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema13"] = df["close"].ewm(span=13, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    c1 = df.iloc[-1]
    c2 = df.iloc[-2]
    fuerza = 0
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
    fuerza = min(fuerza, 100)
    return ("call", fuerza, "ALCISTA") if bullish(c1) else ("put", fuerza, "BAJISTA") if bearish(c1) else None
