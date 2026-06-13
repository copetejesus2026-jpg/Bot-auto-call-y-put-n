import numpy as np
import pandas as pd

# ==================================================
# 🚀 ESTRATEGIA OPTIMIZADA - IGUAL A TUS GRÁFICOS
# ✅ Señales de reversión cerca de bandas
# ✅ Mayor sensibilidad sin perder calidad
# ✅ Coincide con flechas verdes/rojas
# ==================================================

def get_reversal_signal(df, tolerancia_nivel=0.0028, ventana_niveles=5):
    if len(df) < 8:
        return None

    df = df.copy()

    # Bandas dinámicas igual a gráfico
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['desv'] = df['close'].rolling(window=20).std()
    df['banda_sup'] = df['ema20'] + 1.8 * df['desv']
    df['banda_inf'] = df['ema20'] - 1.8 * df['desv']

    # Filtros de tendencia
    df['ema8'] = df['close'].ewm(span=8, adjust=False).mean()
    df['ema13'] = df['close'].ewm(span=13, adjust=False).mean()

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=5, min_periods=1).mean()
    avg_loss = loss.rolling(window=5, min_periods=1).mean().replace(0, 0.001)
    rs = avg_gain / avg_loss
    df['rsi'] = 100.0 - (100.0 / (1.0 + rs))

    # --------------------------
    # DATOS ACTUALES
    # --------------------------
    try:
        sup = float(df['banda_sup'].iloc[-1])
        inf = float(df['banda_inf'].iloc[-1])
        med = float(df['ema20'].iloc[-1])

        c1 = float(df['close'].iloc[-1])
        o1 = float(df['open'].iloc[-1])
        h1 = float(df['high'].iloc[-1])
        l1 = float(df['low'].iloc[-1])

        e8 = float(df['ema8'].iloc[-1])
        e13 = float(df['ema13'].iloc[-1])
        rsi1 = float(df['rsi'].iloc[-1])

    except Exception:
        return None

    # --------------------------
    # CONDICIONES DE REVERSIÓN
    # --------------------------
    fuerza = 0
    senal = None
    tipo_nivel = ""

    # COMPRA: Precio cerca de banda inferior
    if l1 <= inf * (1 + tolerancia_nivel) and c1 > l1:
        if rsi1 < 35 and e8 > e13:
            senal = "call"
            tipo_nivel = "SOPORTE / BANDA INFERIOR"
            fuerza = 45
            if c1 > o1: fuerza += 10
            if rsi1 < 28: fuerza += 10

    # VENTA: Precio cerca de banda superior
    if h1 >= sup * (1 - tolerancia_nivel) and c1 < h1:
        if rsi1 > 65 and e8 < e13:
            senal = "put"
            tipo_nivel = "RESISTENCIA / BANDA SUPERIOR"
            fuerza = 45
            if c1 < o1: fuerza += 10
            if rsi1 > 72: fuerza += 10

    if senal is not None:
        fuerza = max(32, min(fuerza, 100))
        return (senal, fuerza, tipo_nivel)

    return None
