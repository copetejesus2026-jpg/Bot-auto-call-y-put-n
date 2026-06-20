from iqoptionapi.stable_api import IQ_Option
import time
import pandas as pd
import numpy as np
import os
import logging
from dotenv import load_dotenv
import telegram

# ---------------------- CONFIGURACIÓN INICIAL ----------------------
load_dotenv()

# Logs para seguimiento
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    filename="bot_logs.log",
    filemode="a"
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
console.setFormatter(formatter)
logging.getLogger("").addHandler(console)

# Cuentas (hasta 2 como pediste)
CUENTAS = [
    {"email": os.getenv("EMAIL1"), "pass": os.getenv("PASS1")},
    {"email": os.getenv("EMAIL2"), "pass": os.getenv("PASS2")}
]
ACTIVO = "EURGBP-OTC"      # Coincide con tus gráficos
TIEMPO_VELA = 60           # 1 minuto
TIEMPO_OPERACION = 2       # 2 minutos de vencimiento
MAX_VELAS_ESPERA = 5       # Límite que definiste
FILTRO_AGOTAMIENTO = 0.4   # Cuerpo ≥ 40% del rango total

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot_tg = telegram.Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None
# -------------------------------------------------------------------

def enviar_mensaje(texto):
    if bot_tg:
        try:
            bot_tg.send_message(chat_id=TELEGRAM_CHAT_ID, text=texto, parse_mode="Markdown")
        except Exception as e:
            logging.warning(f"Telegram error: {e}")

def obtener_velas(api, cantidad=40):
    """Obtiene y estructura velas + filtra agotamiento"""
    try:
        velas = api.get_candles(ACTIVO, TIEMPO_VELA, cantidad, time.time())
        df = pd.DataFrame(velas)[["open", "high", "low", "close", "time"]].copy()
        df["color"] = np.where(df["close"] > df["open"], "VERDE", "ROJA")
        df["cuerpo"] = abs(df["close"] - df["open"])
        df["rango_total"] = df["high"] - df["low"]
        df["valida"] = np.where(
            df["rango_total"] == 0, False,
            (df["cuerpo"] / df["rango_total"]) >= FILTRO_AGOTAMIENTO
        )
        return df
    except Exception as e:
        logging.error(f"Error al obtener velas: {e}")
        return pd.DataFrame()

# ---------------- DETECCIÓN ESTRUCTURA COMO EN IMÁGENES ----------------
def tendencia_alcista(df, n=3):
    """3 máximos y mínimos crecientes + velas válidas"""
    if len(df) < n: return False
    u = df.tail(n)
    return all(u["valida"]) and all(u["high"] > u["high"].shift(1).dropna()) and all(u["low"] > u["low"].shift(1).dropna())

def tendencia_bajista(df, n=3):
    """3 máximos y mínimos decrecientes + velas válidas"""
    if len(df) < n: return False
    u = df.tail(n)
    return all(u["valida"]) and all(u["high"] < u["high"].shift(1).dropna()) and all(u["low"] < u["low"].shift(1).dropna())

def patron_venta_imagen(df):
    """
    Igual que en tus gráficos:
    ↑ → ROJA → VERDE → ROJA (cierra < apertura VERDE)
    Resistencia = Máximo de la vela VERDE intermedia
    Nivel entrada = Mínimo de la 3ª vela
    """
    if len(df) < 10: return None
    df = df.reset_index(drop=True)
    i = len(df) - 4
    if not tendencia_alcista(df.iloc[:i+1]): return None

    v1, v2, v3 = df.iloc[i], df.iloc[i+1], df.iloc[i+2]
    if v1["color"] == "ROJA" and v2["color"] == "VERDE" and v3["color"] == "ROJA":
        if v3["close"] < v2["open"]:
            return {
                "indice_inicio": i + 3,
                "resistencia": round(v2["high"], 5),
                "nivel_entrada": round(v3["low"], 5)
            }
    return None

def patron_compra_imagen(df):
    """
    Espejo bajista:
    ↓ → VERDE → ROJA → VERDE (cierra > apertura ROJA)
    Soporte = Mínimo de la vela ROJA intermedia
    Nivel entrada = Máximo de la 3ª vela
    """
    if len(df) < 10: return None
    df = df.reset_index(drop=True)
    i = len(df) - 4
    if not tendencia_bajista(df.iloc[:i+1]): return None

    v1, v2, v3 = df.iloc[i], df.iloc[i+1], df.iloc[i+2]
    if v1["color"] == "VERDE" and v2["color"] == "ROJA" and v3["color"] == "VERDE":
        if v3["close"] > v2["open"]:
            return {
                "indice_inicio": i + 3,
                "soporte": round(v2["low"], 5),
                "nivel_entrada": round(v3["high"], 5)
            }
    return None

def verificar_rompimiento(df, patron, tipo):
    """Revisa dentro de las 5 velas permitidas"""
    idx = patron["indice_inicio"]
    for paso in range(MAX_VELAS_ESPERA):
        pos = idx + paso
        if pos >= len(df): break
        v = df.iloc[pos]
        if not v["valida"]: continue

        if tipo == "venta" and v["close"] < patron["nivel_entrada"]:
            return True, f"✅ VENTA | Resistencia: {patron['resistencia']} | Entrada: {patron['nivel_entrada']} | Vela: {paso+1}/5"
        if tipo == "compra" and v["close"] > patron["nivel_entrada"]:
            return True, f"✅ COMPRA | Soporte: {patron['soporte']} | Entrada: {patron['nivel_entrada']} | Vela: {paso+1}/5"
    return False, "❌ Sin rompimiento válido en 5 velas"

def operar(api, direccion, cuenta_num):
    estado, id_op = api.buy(1, ACTIVO, direccion, TIEMPO_OPERACION)
    saldo = round(api.get_balance(), 2)
    if estado:
        msg = f"💸 Cuenta {cuenta_num} | {direccion.upper()} | ID: {id_op} | Saldo: ${saldo}"
        logging.info(msg)
        enviar_mensaje(msg)
    else:
        msg = f"⚠️ Cuenta {cuenta_num} | Falló operación {direccion}"
        logging.warning(msg)
        enviar_mensaje(msg)

# ---------------- EJECUCIÓN PRINCIPAL ----------------
if __name__ == "__main__":
    conexiones = []
    for n, dat in enumerate(CUENTAS, start=1):
        if not dat["email"] or not dat["pass"]:
            logging.warning(f"Cuenta {n} sin datos → omitida")
            continue
        con = IQ_Option(dat["email"], dat["pass"])
        ok, razon = con.connect()
        if ok:
            logging.info(f"Cuenta {n} conectada | Saldo: ${con.get_balance():.2f}")
            conexiones.append((n, con))
        else:
            logging.error(f"Cuenta {n} error: {razon}")

    if not conexiones:
        logging.critical("Sin cuentas conectadas")
        exit()

    enviar_mensaje("🤖 Bot iniciado — Estrategia según estructura de imágenes")

    try:
        while True:
            for num, api in conexiones:
                df = obtener_velas(api)
                if df.empty: continue

                # VENTA
                p_venta = patron_venta_imagen(df)
                if p_venta:
                    ok, info = verificar_rompimiento(df, p_venta, "venta")
                    logging.info(f"Cuenta {num}: {info}")
                    if ok: operar(api, "put", num)

                # COMPRA
                p_compra = patron_compra_imagen(df)
                if p_compra:
                    ok, info = verificar_rompimiento(df, p_compra, "compra")
                    logging.info(f"Cuenta {num}: {info}")
                    if ok: operar(api, "call", num)

            time.sleep(10)  # Revisión ligera
    except KeyboardInterrupt:
        enviar_mensaje("⏹ Bot detenido manualmente")
        logging.info("Detenido por usuario")
        for _, api in conexiones: api.close_connect()
