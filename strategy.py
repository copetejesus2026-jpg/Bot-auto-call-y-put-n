import pandas as pd
import numpy as np

def get_reversal_signal(df, tolerancia=0.0018, ventana=5):
    """
    ✅ SIEMPRE devuelve: (señal, fuerza, tipo_nivel) o None
    NUNCA deja variables sin valor definidas
    """
    try:
        if len(df) < ventana + 2:
            return None

        ultimos = df.tail(ventana).copy()
        max_nivel = ultimos["high"].max()
        min_nivel = ultimos["low"].min()
        ultimo = df.iloc[-1]
        penultimo = df.iloc[-2]

        # Evitar división por cero
        if max_nivel == min_nivel:
            return None

        fuerza = 0
        señal = None
        tipo_nivel = ""

        # Señal COMPRA (Soporte + rechazo alcista)
        if abs(ultimo["close"] - min_nivel) <= tolerancia:
            if ultimo["close"] > ultimo["open"] and penultimo["close"] < penultimo["open"]:
                fuerza = int( ((max_nivel - ultimo["low"]) / (max_nivel - min_nivel)) * 100 )
                señal = "call"
                tipo_nivel = "soporte"

        # Señal VENTA (Resistencia + rechazo bajista)
        elif abs(ultimo["close"] - max_nivel) <= tolerancia:
            if ultimo["close"] < ultimo["open"] and penultimo["close"] > penultimo["open"]:
                fuerza = int( ((ultimo["high"] - min_nivel) / (max_nivel - min_nivel)) * 100 )
                señal = "put"
                tipo_nivel = "resistencia"

        # ✅ Garantiza retorno completo
        if señal is not None and fuerza > 0:
            return señal, fuerza, tipo_nivel
        return None

    except Exception as e:
        return None
