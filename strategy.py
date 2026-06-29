import pandas as pd
import numpy as np

# ================= INDICADORES BASE =================

def add_indicators(df):
    df = df.copy()

    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df["atr"] = ranges.max(axis=1).rolling(14).mean()

    return df


# ================= CANDLE READER =================

def classify_candle(candle):
    body = abs(candle["close"] - candle["open"])
    total = candle["high"] - candle["low"]

    if total == 0:
        return "neutral"

    upper = candle["high"] - max(candle["open"], candle["close"])
    lower = min(candle["open"], candle["close"]) - candle["low"]

    body_ratio = body / total
    bullish = candle["close"] > candle["open"]

    if body_ratio < 0.20:
        return "doji"

    if lower > body * 1.5:
        return "bullish_rejection"

    if upper > body * 1.5:
        return "bearish_rejection"

    if bullish and body_ratio > 0.65:
        return "bullish_strong"

    if (not bullish) and body_ratio > 0.65:
        return "bearish_strong"

    if bullish:
        return "bullish_weak"

    return "bearish_weak"


# ================= STRUCTURE =================

def detect_structure(df):
    highs = df["high"].tail(8).tolist()
    lows = df["low"].tail(8).tolist()

    higher_highs = 0
    lower_lows = 0

    for i in range(1, len(highs)):
        if highs[i] > highs[i - 1]:
            higher_highs += 1

        if lows[i] < lows[i - 1]:
            lower_lows += 1

    if higher_highs >= 5:
        return "bullish"

    if lower_lows >= 5:
        return "bearish"

    return "range"


# ================= SEQUENCE ANALYZER =================

def analyze_sequence(df):
    seq = []

    for i in range(-6, -1):
        seq.append(classify_candle(df.iloc[i]))

    bullish_count = sum("bullish" in s for s in seq)
    bearish_count = sum("bearish" in s for s in seq)
    doji_count = sum(s == "doji" for s in seq)

    if bullish_count >= 3 and bearish_count <= 1:
        return "buyers_control"

    if bearish_count >= 3 and bullish_count <= 1:
        return "sellers_control"

    if doji_count >= 2:
        return "indecision"

    return "mixed"


# ================= REJECTION =================

def detect_rejection(df):
    c = classify_candle(df.iloc[-2])

    if c == "bullish_rejection":
        return "call"

    if c == "bearish_rejection":
        return "put"

    return None


# ================= MOMENTUM LOSS =================

def momentum_exhaustion(df):
    bodies = []

    for i in [-4, -3, -2]:
        c = df.iloc[i]
        bodies.append(abs(c["close"] - c["open"]))

    if bodies[0] > bodies[1] > bodies[2]:
        return True

    return False


# ================= BREAK CONFIRM =================

def confirm_break(df, direction):
    current = df.iloc[-2]
    previous = df.iloc[-3]

    if direction == "call":
        return current["close"] > previous["high"]

    if direction == "put":
        return current["close"] < previous["low"]

    return False


# ================= VOLATILITY FILTER =================

def volatility_ok(df):
    atr = df["atr"].iloc[-2]

    if pd.isna(atr):
        return False

    avg_range = (df["high"] - df["low"]).tail(20).mean()

    return avg_range > atr * 0.7


# ================= MAIN SIGNAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 50:
        return None, None, None

    if not volatility_ok(df_m1):
        return None, None, None

    structure = detect_structure(df_m1)
    sequence = analyze_sequence(df_m1)
    rejection = detect_rejection(df_m1)
    exhausted = momentum_exhaustion(df_m1)

    score = 0
    signal = None

    # CALL setup
    if structure == "bullish":
        score += 2

    if sequence == "buyers_control":
        score += 2

    if rejection == "call":
        score += 3
        signal = "call"

    # PUT setup
    if structure == "bearish":
        score += 2

    if sequence == "sellers_control":
        score += 2

    if rejection == "put":
        score += 3
        signal = "put"

    if exhausted:
        score += 1

    if signal is None:
        if structure == "bullish" and sequence == "buyers_control":
            signal = "call"

        elif structure == "bearish" and sequence == "sellers_control":
            signal = "put"

    if signal is None:
        return None, None, None

    if not confirm_break(df_m1, signal):
        return None, None, None

    context = {
        "score": score,
        "structure": structure,
        "sequence": sequence,
        "rejection": rejection,
        "exhausted": exhausted
    }

    if score >= 5:
        return signal, 1, context

    return None, None, None
