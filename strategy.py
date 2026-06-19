import numpy as np
import pandas as pd

# ==================================================
# 🚀 ESTRATEGIA DE REVERSIÓN - MÁS ENTRADAS
# ✅ Solo opera en Soporte / Resistencia
# ✅ Condiciones más flexibles
# ✅ Detección de niveles ampliada
# ✅ Compatible con todos los pares OTC
# ==================================================

def get_reversal_signal(df, tolerancia_nivel=0.0018, ventana_niveles=5):
    # Reducimos velas mínimas para analizar más rápido
    if len(df) < ventana_niveles + 3:
        return None

    df = df.copy()

    # --------------------------
    # INDICADORES AJUSTADOS
    # --------------------------
    df['ema8'] = df['close'].ewm(span=8, adjust=False).mean()
    df['ema13'] = df['close'].ewm(span=13, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=5, min_periods=1).mean()
    avg_loss = loss.rolling(window=5, min_periods=1).mean().replace(0, 0.001)
    rs = avg_gain / avg_loss
    df['rsi'] = 100.0 - (100.0 / (1.0 + rs))

    df['macd'] = df['ema13'] - df['ema21']
    df['signal'] = df['macd'].ewm(span=4, adjust=False).mean()

    # --------------------------
    # DETECCIÓN DE NIVELES MEJORADA
    # --------------------------
    def detectar_niveles(datos, ventana):
        soportes = []
        resistencias = []
        total = len(datos)
        # Analizamos todo el historial disponible
        for i in range(ventana, total - ventana):
            min_rango = datos['low'].iloc[i-ventana:i+ventana+1].min()
            max_rango = datos['high'].iloc[i-ventana:i+ventana+1].max()
            
            if abs(datos['low'].iloc[i] - min_rango) / min_rango <= 0.001:
                soportes.append(round(min_rango, 5))
            if abs(datos['high'].iloc[i] - max_rango) / max_rango <= 0.001:
                resistencias.append(round(max_rango, 5))
        
        # Eliminamos duplicados y tomamos los más recientes
        soportes = sorted(list(set(soportes)), reverse=True)[:8]
        resistencias = sorted(list(set(resistencias)))[:8]
        return soportes, resistencias

    soportes, resistencias = detectar_niveles(df, ventana_niveles)

    # --------------------------
    # DATOS ACTUALES
    # --------------------------
    try:
        e8_1 = float(df['ema8'].iloc[-1])
        e13_1 = float(df['ema13'].iloc[-1])
        e21_1 = float(df['ema21'].iloc[-1])

        c1 = float(df['close'].iloc[-1])
        o1 = float(df['open'].iloc[-1])
        l1 = float(df['low'].iloc[-1])
        h1 = float(df['high'].iloc[-1])

        macd1 = float(df['macd'].iloc[-1])
        sig1 = float(df['signal'].iloc[-1])
        hist1 = float(df['macd'].iloc[-1] - df['signal'].iloc[-1])
        rsi1 = float(df['rsi'].iloc[-1])
        vol1 = float(df['volume'].iloc[-1])

        vol_prom = float(df['volume'].iloc[-4:-1].mean()) if len(df) >= 5 else max(vol1, 1.0)

    except Exception:
        return None

    # --------------------------
    # VERIFICACIÓN EN NIVEL
    # --------------------------
    en_soporte = False
    en_resistencia = False

    for sop in soportes:
        if abs(c1 - sop) / sop <= tolerancia_nivel:
            en_soporte = True
            break

    for res in resistencias:
        if abs(c1 - res) / res <= tolerancia_nivel:
            en_resistencia = True
            break

    if not en_soporte and not en_resistencia:
        return None

    # --------------------------
    # CONDICIONES FLEXIBLES PARA MÁS SEÑALES
    # --------------------------
    fuerza = 0
    senal = None
    tipo_nivel = ""

    # ✅ COMPRA en SOPORTE
    if en_soporte:
        cond_compra = (
            (e8_1 >= e13_1) and
            macd1 >= sig1 and
            rsi1 > 28 and rsi1 < 72 and
            c1 >= o1
        )
        if cond_compra:
            senal = "call"
            tipo_nivel = "soporte"
            fuerza = 40
            if e13_1 >= e21_1: fuerza += 5
            if hist1 > 0: fuerza += 5
            if vol1 >= vol_prom * 0.35: fuerza += 5

    # ✅ VENTA en RESISTENCIA
    if en_resistencia:
        cond_venta = (
            (e8_1 <= e13_1) and
            macd1 <= sig1 and
            rsi1 < 72 and rsi1 > 28 and
            c1 <= o1
        )
        if cond_venta:
            senal = "put"
            tipo_nivel = "resistencia"
            fuerza = 40
            if e13_1 <= e21_1: fuerza += 5
            if hist1 < 0: fuerza += 5
            if vol1 >= vol_prom * 0.35: fuerza += 5

    # --------------------------
    # RETORNO
    # --------------------------
    if senal is not None:
        fuerza = max(25, min(fuerza, 100))
        return (senal, fuerza, tipo_nivel)

    return None
