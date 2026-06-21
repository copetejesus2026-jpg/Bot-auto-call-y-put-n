import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import threading
import logging
from datetime import datetime, timezone

from strategy import get_reversal_signal
from iqoptionapi.stable_api import IQ_Option

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================================
# ⚙️ CONFIGURACIÓN RÁPIDA Y ESTABLE
# ==========================================
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPIRATION = 1
BASE_AMOUNT = 91
TIMEFRAME_M1 = 60

# ✅ MÁS PARES = MÁS OPORTUNIDADES
PAIRS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC",
    "AUDUSD-OTC", "USDCAD-OTC", "USDCHF-OTC", "NZDUSD-OTC",
    "EURCAD-OTC", "GBPCAD-OTC", "GBPCHF-OTC", "AUDJPY-OTC", "CADJPY-OTC",
    "AUDCAD-OTC", "AUDCHF-OTC", "EURAUD-OTC", "EURCHF-OTC", "GBPAUD-OTC"
]

MAX_DAILY_TRADES = 200       # Más operaciones permitidas
MAX_LOSS_STREAK = 5
PAUSE_TIME = 900
MAX_RECONNECT_ATTEMPTS = 15
RECONNECT_DELAY = 1.5
KEEPALIVE_INTERVAL = 10      # Ping MUY frecuente = sin cortes
MAX_SILENCE = 15

FUERZA_MINIMA = 28           # ✅ Menor exigencia = MÁS SEÑALES
TOLERANCIA_NIVEL = 0.0032    # ✅ Zona más amplia = MÁS SEÑALES
VENTANA_NIVELES = 4

TIEMPO_ESPERA_EJECUCION = 0.01
REINTENTOS_EJECUCION = 6
TIEMPO_MINIMO_VALIDO = 55    # ✅ Entra antes, menos espera

# Variables
DAILY_TRADES = 0
CURRENT_DAY = datetime.now(timezone.utc).day
LOSS_STREAK = 0
LAST_LOSS = 0
LAST_TRADE = None
BOT_RUNNING = False
SEÑAL_PENDIENTE = None
LAST_PING = 0
ULTIMA_ACTIVIDAD = time.time()

# ====================================================
# 📱 TELEGRAM — AVISOS RÁPIDOS
# ====================================================
def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=5
            )
        except Exception:
            pass

def listen_commands():
    global BOT_RUNNING
    last_update_id = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={"offset": last_update_id + 1, "timeout": 15},
                timeout=20
            )
            data = res.json()
            if not data.get("ok"):
                time.sleep(0.5)
                continue
            for update in data.get("result", []):
                last_update_id = update["update_id"]
                text = update.get("message", {}).get("text", "").strip().lower()
                chat_id = str(update["message"]["chat"]["id"])
                if chat_id != str(CHAT_ID): continue
                if text == "/start":
                    if not BOT_RUNNING:
                        BOT_RUNNING = True
                        send("✅ <b>BOT ACTIVADO</b>\nModo rápido: más señales + ejecución inmediata")
                    else: send("ℹ️ Ya está analizando a máxima velocidad")
                elif text == "/stop":
                    BOT_RUNNING = False
                    send("🛑 Detenido")
        except Exception:
            time.sleep(0.5)

# ====================================================
# 🔄 REINICIO DIARIO
# ====================================================
def reset_day():
    global DAILY_TRADES, CURRENT_DAY, LOSS_STREAK, LAST_TRADE, SEÑAL_PENDIENTE
    hoy = datetime.now(timezone.utc).day
    if hoy != CURRENT_DAY:
        DAILY_TRADES = LOSS_STREAK = 0
        LAST_TRADE = SEÑAL_PENDIENTE = None
        CURRENT_DAY = hoy
        if BOT_RUNNING:
            send("🔄 Nuevo ciclo — todo listo")

# ====================================================
# 🔌 CONEXIÓN + VIGILANCIA DE VIDA
# ====================================================
def connect():
    global LAST_PING, ULTIMA_ACTIVIDAD
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ Faltan credenciales IQ")
                time.sleep(5)
                attempts += 1
                continue
            iq = IQ_Option(EMAIL, PASSWORD)
            ok, reason = iq.connect()
            time.sleep(0.8)
            if ok:
                try:
                    _ = iq.get_server_timestamp()
                    iq.change_balance("PRACTICE")
                    balance = iq.get_balance()
                    LAST_PING = ULTIMA_ACTIVIDAD = time.time()
                    send(f"✅ CONECTADO | ${balance:.2f}")
                    return iq
                except Exception:
                    ok = False
            attempts += 1
            time.sleep(RECONNECT_DELAY)
        except Exception:
            attempts += 1
            time.sleep(RECONNECT_DELAY)
    send("💥 Reinicio completo en 20s…")
    time.sleep(20)
    return connect()

def check_and_reconnect(iq):
    global LAST_PING, ULTIMA_ACTIVIDAD
    ahora = time.time()
    try:
        if iq and iq.check_connect():
            if ahora - LAST_PING > KEEPALIVE_INTERVAL:
                iq.get_server_timestamp()
                LAST_PING = ahora
            # ✅ Reinicia si no hay actividad
            if ahora - ULTIMA_ACTIVIDAD > 45:
                send("⚠️ Sin actividad — refrescando…")
                return connect()
            return iq
    except Exception:
        pass
    return connect()

# ====================================================
# 📥 VELAS: POCOS INTENTOS, RÁPIDO
# ====================================================
def get_df(iq, pair, retries=2):
    global ULTIMA_ACTIVIDAD
    for _ in range(retries):
        try:
            iq = check_and_reconnect(iq)
            if not iq:
                time.sleep(0.15)
                continue
            data = iq.get_candles(pair, TIMEFRAME_M1, 22, time.time())
            if not data or len(data) < 8:
                time.sleep(0.15)
                continue
            df = pd.DataFrame(data)
            df.rename(columns={"max":"high", "min":"low"}, inplace=True)
            df[["open","close","high","low","volume"]] = df[["open","close","high","low","volume"]].astype(float)
            ULTIMA_ACTIVIDAD = time.time()
            return df
        except Exception as e:
            if "need reconnect" in str(e).lower():
                iq = connect()
            time.sleep(0.2)
    return None

# ====================================================
# 🚀 EJECUCIÓN INSTANTÁNEA
# ====================================================
def ejecutar_operacion(iq, monto, par, direccion, vencimiento):
    for intento in range(REINTENTOS_EJECUCION + 1):
        try:
            iq = check_and_reconnect(iq)
            if not iq: continue
            ts = iq.get_server_timestamp()
            sec_rest = 60 - (ts % 60)
            if sec_rest < TIEMPO_MINIMO_VALIDO:
                return False, None
            time.sleep(TIEMPO_ESPERA_EJECUCION)
            ok, tid = iq.buy(monto, par, direccion, vencimiento)
            if ok and tid > 0:
                return True, tid
            if intento < REINTENTOS_EJECUCION:
                time.sleep(0.1)
        except Exception:
            iq = check_and_reconnect(iq)
    return False, None

# ====================================================
# 🧠 BUCLE: ANÁLISIS CONTINUO Y RÁPIDO
# ====================================================
def main():
    global BOT_RUNNING, LOSS_STREAK, LAST_LOSS, DAILY_TRADES, LAST_TRADE, SEÑAL_PENDIENTE
    threading.Thread(target=listen_commands, daemon=True).start()
    iq = connect()
    last_candle = None
    send("ℹ️ <b>LISTO</b> | /start = analiza sin parar")

    while True:
        try:
            if not BOT_RUNNING:
                time.sleep(0.2)
                continue
            reset_day()
            iq = check_and_reconnect(iq)
            if not iq:
                time.sleep(0.5)
                continue

            if DAILY_TRADES >= MAX_DAILY_TRADES:
                send("ℹ️ Límite alcanzado")
                BOT_RUNNING = False
                time.sleep(120)
                continue
            if LOSS_STREAK >= MAX_LOSS_STREAK:
                rest = int(PAUSE_TIME - (time.time() - LAST_LOSS))
                if rest > 0:
                    time.sleep(2)
                    continue
                LOSS_STREAK = 0

            st = iq.get_server_timestamp()
            sec = st % 60
            current_candle = int(st // 60)

            # ✅ EJECUCIÓN AL CAMBIAR VELA
            if current_candle != last_candle:
                last_candle = current_candle
                if SEÑAL_PENDIENTE:
                    p, sig, fz, tn = SEÑAL_PENDIENTE
                    SEÑAL_PENDIENTE = None
                    if (p, sig) == LAST_TRADE: continue
                    LAST_TRADE = (p, sig)
                    send(f"""🚀 <b>ENTRADA</b>
💹 {p} | {tn}
💪 {fz}
{'🟢 COMPRA' if sig=='call' else '🔴 VENTA'}""")
                    ok, tid = ejecutar_operacion(iq, BASE_AMOUNT, p, sig, EXPIRATION)
                    if ok:
                        DAILY_TRADES += 1
                        send(f"✅ Abierta — Total: {DAILY_TRADES}")
                        time.sleep(62)
                        try:
                            res = iq.check_win_v4(tid)
                            if res < 0:
                                LOSS_STREAK += 1
                                LAST_LOSS = time.time()
                                send(f"❌ -${abs(res):.2f}")
                            else:
                                LOSS_STREAK = 0
                                send(f"✅ +${res:.2f}")
                        except Exception:
                            pass

            # ✅ BUSCA SEÑALES TODO EL TIEMPO (10‑58 seg)
            if 10 <= sec <= 58:
                mejor = None
                max_fz = 0
                # ✅ RECORRE TODOS LOS PARES RÁPIDO
                for par in PAIRS:
                    df = get_df(iq, par)
                    if df is None:
                        continue
                    res = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                    if res:
                        sig, fz, tn = res
                        if fz >= FUERZA_MINIMA and fz > max_fz:
                            max_fz = fz
                            mejor = (par, sig, fz, tn)
                if mejor:
                    SEÑAL_PENDIENTE = mejor
                    p, sig, fz, tn = mejor
                    send(f"🔍 {p} | {tn} | {fz}")

            time.sleep(0.03)  # ✅ CICLO MÁS CORTO = MÁS ANÁLISIS

        except Exception as e:
            send(f"🔄 Recuperando: {str(e)[:40]}")
            time.sleep(1)
            iq = check_and_reconnect(iq)

if __name__ == "__main__":
    req = ["IQ_EMAIL","IQ_PASSWORD","TELEGRAM_TOKEN","TELEGRAM_CHAT_ID"]
    faltan = [v for v in req if not os.getenv(v)]
    if faltan: print(f"❌ Faltan: {faltan}"); sys.exit(1)
    if not os.path.exists("strategy.py"): print("❌ Falta strategy.py"); sys.exit(1)
    main()
