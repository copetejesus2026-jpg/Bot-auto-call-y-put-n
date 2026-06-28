import pandas as pd
import numpy as np

# ================= INDICADORES =================

def add_indicators(df):
    df = df.copy()

    # EMA
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    # ATR
    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    ranges = pd.concat(
        [high_low, high_close, low_close],
        axis=1
    )

    true_range = ranges.max(axis=1)
    df["atr"] = true_range.rolling(14).mean()

    # RSI
    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


# ================= TENDENCIA =================

def trend(df):
    if len(df) < 60:
        return None

    # VELA CERRADA
    ema20 = df["ema20"].iloc[-2]
    ema50 = df["ema50"].iloc[-2]

    if pd.isna(ema20) or pd.isna(ema50):
        return None

    if ema20 > ema50:
        return "call"

    if ema20 < ema50:
        return "put"

    return None


# ================= IMPULSO =================

def strong_candle(candle):
    body = abs(candle["close"] - candle["open"])
    full = candle["high"] - candle["low"]

    if full == 0:
        return False

    return (body / full) > 0.55


# ================= CONTINUIDAD =================

def continuation(df, direction):
    if len(df) < 4:
        return False

    # SOLO VELAS CERRADAS
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]

    # CALL
    if direction == "call":
        if (
            c1["close"] > c1["open"] and
            c2["close"] > c2["open"] and
            c1["close"] > c2["close"] and
            strong_candle(c1)
        ):
            return True

    # PUT
    if direction == "put":
        if (
            c1["close"] < c1["open"] and
            c2["close"] < c2["open"] and
            c1["close"] < c2["close"] and
            strong_candle(c1)
        ):
            return True

    return False


# ================= SOPORTE / RESISTENCIA =================

def support_resistance(df):
    highs = []
    lows = []

    if len(df) < 30:
        return highs, lows

    for i in range(10, len(df) - 10):
        high = df["high"].iloc[i]
        low = df["low"].iloc[i]

        # resistencia
        if high == max(df["high"].iloc[i - 5:i + 5]):
            highs.append(high)

        # soporte
        if low == min(df["low"].iloc[i - 5:i + 5]):
            lows.append(low)

    return highs, lows


# ================= FILTRO REVERSION =================

def near_reversal_zone(df):
    if len(df) < 30:
        return False

    # VELA CERRADA
    price = df["close"].iloc[-2]
    atr = df["atr"].iloc[-2]

    if pd.isna(atr):
        return False

    highs, lows = support_resistance(df)
    zone_distance = atr * 0.40

    for h in highs:
        if abs(price - h) < zone_distance:
            return True

    for l in lows:
        if abs(price - l) < zone_distance:
            return True

    return False


# ================= VOLATILIDAD =================

def volatility_ok(df):
    if len(df) < 20:
        return False

    atr = df["atr"].iloc[-2]
    mean_atr = df["atr"].mean()

    if pd.isna(atr) or pd.isna(mean_atr):
        return False

    return atr > mean_atr * 0.7


# ================= RSI FILTER =================

def rsi_ok(df, direction):
    rsi = df["rsi"].iloc[-2]

    if pd.isna(rsi):
        return False

    if direction == "call":
        return 50 < rsi < 75

    if direction == "put":
        return 25 < rsi < 50

    return False


# ================= SEÑAL PRINCIPAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 80 or len(df_m5) < 80:
        return None, None

    # volatilidad
    if not volatility_ok(df_m1):
        return None, None

    # tendencia
    direction = trend(df_m5)

    if direction is None:
        return None, None

    # filtro soporte / resistencia
    if near_reversal_zone(df_m1):
        return None, None

    # continuidad
    if not continuation(df_m1, direction):
        return None, None

    # RSI
    if not rsi_ok(df_m1, direction):
        return None, None

    return direction, 1
