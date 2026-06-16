import numpy as np
import pandas as pd

# --------------------------
# FUNCIONES AUXILIARES
# --------------------------
def body(c):
    """Tamaño del cuerpo de la vela"""
    return abs(c["close"] - c["open"])

def range_c(c):
    """Rango total de la vela (alto - bajo)"""
    r = c["high"] - c["low"]
    return r if r != 0 else 0.0001

def mecha_superior(c):
    """Longitud de la mecha superior"""
    return c["high"] - max(c["open"], c["close"])

def mecha_inferior(c):
    """Longitud de la mecha inferior"""
    return min(c["open"], c["close"]) - c["low"]

def centro_vela(c):
    """Nivel medio de la vela"""
    return (c["high"] + c["low"]) / 2

def bullish(c):
    """Vela alcista: cierre > apertura"""
    return c["close"] > c["open"]

def bearish(c):
    """Vela bajista: cierre < apertura"""
    return c["close"] < c["open"]

# --------------------------
# DETECCIÓN DE SOPORTE Y RESISTENCIA
# --------------------------
def hay_zona_fuerte(df, precio_actual, rango_busqueda=15, tolerancia=0.0008):
    """
    Busca si el precio actual está cerca de una zona donde el precio ya reaccionó antes
    - rango_busqueda: últimas velas a revisar
    - tolerancia: margen de error para considerar mismo nivel
    """
    if len(df) < rango_busqueda:
        return False

    # Tomamos los máximos y mínimos de las últimas velas
    maximos = df["high"].iloc[-rango_busqueda:-1].values
    minimos = df["low"].iloc[-rango_busqueda:-1].values

    # Verificamos si el precio actual está cerca de algún máximo anterior (resistencia)
    for nivel in maximos:
        if abs(precio_actual - nivel) <= tolerancia:
            return True

    # Verificamos si el precio actual está cerca de algún mínimo anterior (soporte)
    for nivel in minimos:
        if abs(precio_actual - nivel) <= tolerancia:
            return True

    return False

# --------------------------
# DETECCIÓN DE VELA DE AGOTAMIENTO / INDECISIÓN
# --------------------------
def es_agotamiento(c):
    """Rechaza velas con mechas largas, cuerpo débil o cierre en el centro"""
    cuerpo = body(c)
    rango = range_c(c)
    if cuerpo < rango * 0.4:  # Cuerpo menor al 40% del rango
        return True
    if mecha_superior(c) > cuerpo * 0.5 or mecha_inferior(c) > cuerpo * 0.5:
        return True  # Mechas muy largas = retroceso
    distancia_centro = abs(c["close"] - centro_vela(c))
    if distancia_centro < rango * 0.15:  # Cierre muy cerca del medio
        return True
    return False

# --------------------------
# ANÁLISIS COMPLETO: DESDE APERTURA HASTA CIERRE
# --------------------------
def analizar_evolucion_vela(c):
    """Evalúa cómo se comportó el precio durante toda la vela"""
    cuerpo = body(c)
    rango = range_c(c)
    if rango == 0:
        return 0

    pct_cuerpo = cuerpo / rango
    pct_mecha_sup = mecha_superior(c) / rango
    pct_mecha_inf = mecha_inferior(c) / rango

    # Vela alcista: debe subir progresivamente y cerrar en zona alta
    if bullish(c):
        cierre_alto = (c["close"] - c["low"]) / rango >= 0.85  # Cierra en el 15% superior
        apertura_baja = (c["open"] - c["low"]) / rango <= 0.25  # Abre en el 25% inferior
        sin_mecha_larga = pct_mecha_sup <= 0.20 and pct_mecha_inf <= 0.15
        if cierre_alto and apertura_baja and sin_mecha_larga:
            return 30  # Máxima puntuación de evolución alcista

    # Vela bajista: debe bajar progresivamente y cerrar en zona baja
    if bearish(c):
        cierre_bajo = (c["high"] - c["close"]) / rango >= 0.85  # Cierra en el 15% inferior
        apertura_alta = (c["high"] - c["open"]) / rango <= 0.25  # Abre en el 25% superior
        sin_mecha_larga = pct_mecha_inf <= 0.20 and pct_mecha_sup <= 0.15
        if cierre_bajo and apertura_alta and sin_mecha_larga:
            return 30  # Máxima puntuación de evolución bajista

    return 0  # Evolución débil o desordenada

# --------------------------
# SEÑAL FINAL CON TODOS LOS FILTROS
# --------------------------
def get_reversal_signal(df):
    if df is None or df.empty or len(df) < 60:
        return None  # Necesitamos suficientes datos

    df = df.copy()

    # Medias móviles para tendencia y estructura
    df["ema5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema13"] = df["close"].ewm(span=13, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    # Velas recientes
    c1 = df.iloc[-1]   # Vela actual cerrada
    c2 = df.iloc[-2]   # Vela anterior
    c3 = df.iloc[-3]
    c4 = df.iloc[-4]

    # ❌ RECHAZO 1: Vela de agotamiento
    if es_agotamiento(c1):
        return None

    # ❌ RECHAZO 2: Precio está en zona de soporte o resistencia fuerte
    if hay_zona_fuerte(df, c1["close"]):
        return None

    fuerza = 0

    # 🔹 1. Análisis completo de evolución desde apertura hasta cierre
    fuerza += analizar_evolucion_vela(c1)

    # 🔹 2. Confirmación de impulso: rompe y cierra fuera de máximos/mínimos anteriores
    if bullish(c1):
        if c1["close"] > c2["high"] and c1["close"] > c3["high"] and c1["close"] > c4["high"]:
            fuerza += 20
    if bearish(c1):
        if c1["close"] < c2["low"] and c1["close"] < c3["low"] and c1["close"] < c4["low"]:
            fuerza += 20

    # 🔹 3. Tendencia alineada (solo operar a favor de la estructura mayor)
    tendencia_alcista = (
        df["ema5"].iloc[-1] > df["ema13"].iloc[-1] >
        df["ema21"].iloc[-1] > df["ema50"].iloc[-1]
    )
    tendencia_bajista = (
        df["ema5"].iloc[-1] < df["ema13"].iloc[-1] <
        df["ema21"].iloc[-1] < df["ema50"].iloc[-1]
    )
    if bullish(c1) and tendencia_alcista:
        fuerza += 20
    if bearish(c1) and tendencia_bajista:
        fuerza += 20

    # 🔹 4. Impulso mayor que velas anteriores
    if body(c1) > body(c2) * 1.2 and body(c1) > body(c3) * 1.1:
        fuerza += 15

    # 🔹 5. Volatilidad suficiente (evitar velas planas)
    if range_c(c1) > range_c(c2) * 0.9:
        fuerza += 15

    # Limitar fuerza al 100%
    fuerza = min(fuerza, 100)

    # 🎯 Solo señales de MÁXIMA calidad
    if fuerza >= 88:
        if bullish(c1):
            return ("call", fuerza, "ALCISTA | FUERA DE ZONAS CLAVE")
        elif bearish(c1):
            return ("put", fuerza, "BAJISTA | FUERA DE ZONAS CLAVE")

    return None
