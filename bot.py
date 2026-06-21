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
# ⚙️ CONFIGURACIÓN PARA RAILWAY
# ==========================================
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPIRATION = 1
BASE_AMOUNT = 91
TIMEFRAME_M1 = 60

PAIRS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC",
    "AUDUSD-OTC", "USDCAD-OTC", "USDCHF-OTC", "NZDUSD-OTC",
    "EURCAD-OTC", "GBPCAD-OTC", "GBPCHF-OTC", "AUDJPY-OTC", "CADJPY-OTC"
]

MAX_DAILY_TRADES = 150
MAX_LOSS_STREAK = 5
PAUSE_TIME = 900
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY = 3
KEEPALIVE_INTERVAL = 18  # Ping antes del corte de Railway

FUERZA_MINIMA = 32
TOLERANCIA_NIVEL = 0.0028
VENTANA_NIVELES = 5

TIEMPO_ESPERA_EJECUCION = 0.02
REINTENTOS_EJECUCION = 4
TIEMPO_MINIMO_VALIDO = 58

# Variables
DAILY_TRADES = 0
CURRENT_DAY = datetime.now(timezone.utc).day
LOSS_STREAK = 0
LAST_LOSS = 0
LAST_TRADE = None
BOT_RUNNING = False
SEÑAL_PENDIENTE = None
LAST_PING = 0

# ====================================================
# 📱 TELEGRAM
# ====================================================
def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=8
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
                params={"offset": last_update_id + 1, "timeout": 20},
                timeout=25
            )
            data = res.json()
            if not data.get("ok"):
                time.sleep(1)
                continue
            for update in data.get("result", []):
                last_update_id = update["update_id"]
                text = update.get("message", {}).get("text", "").strip().lower()
                chat_id = str(update["message"]["chat"]["id"])
                if chat_id != str(CHAT_ID): continue
                if text == "/start":
                    if not BOT_RUNNING:
                        BOT_RUNNING = True
                        send("✅ BOT INICIADO — vela siguiente | OPERACIÓN AUTOMÁTICA ACTIVA")
                    else: send("ℹ️ Ya activo y operando")
                elif text == "/stop":
                    BOT_RUNNING = False
                    send("🛑 Detenido")
        except Exception:
            time.sleep(1)

# ====================================================
# 🔄 REINICIO DIARIO (agregado sin tocar nada más)
# ====================================================
def reset_day():
    global DAILY_TRADES, CURRENT_DAY, LOSS_STREAK, LAST_TRADE, SEÑAL_PENDIENTE
    hoy = datetime.now(timezone.utc).day
    if hoy != CURRENT_DAY:
        DAILY_TRADES = LOSS_STREAK = 0
        LAST_TRADE = SEÑAL_PENDIENTE = None
        CURRENT_DAY = hoy

# ====================================================
# 🔌 CONEXIÓN + MANTENIMIENTO
# ====================================================
def connect():
    global LAST_PING
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ Faltan credenciales")
                time.sleep(10)
                attempts += 1
                continue
            iq = IQ_Option(EMAIL, PASSWORD)
            ok, reason = iq.connect()
            if ok:
                try:
                    _ = iq.get_server_timestamp()
                    iq.change_balance("PRACTICE")
                    balance = iq.get_balance()
                    LAST_PING = time.time()
                    send(f"✅ CONECTADO | ${balance:.2f}")
                    return iq
                except Exception:
                    ok = False
            attempts += 1
            time.sleep(RECONNECT_DELAY)
        except Exception:
            attempts += 1
            time.sleep(RECONNECT_DELAY)
    send("💥 Reintentando en 60s…")
    time.sleep(60)
    return connect()

def check_and_reconnect(iq):
    global LAST_PING
    ahora = time.time()
    try:
        if iq and iq.check_connect():
            if ahora - LAST_PING > KEEPALIVE_INTERVAL:
                iq.get_server_timestamp()
                LAST_PING = ahora
            return iq
    except Exception:
        pass
    return connect()

# ====================================================
# 📥 OBTENER VELAS — IGNORA ERROR, SIGUE TRABAJANDO
# ====================================================
def get_df(iq, pair, retries=2):
    for _ in range(retries):
        try:
            iq = check_and_reconnect(iq)
            if not iq:
                time.sleep(0.3)
                continue
            data = iq.get_candles(pair, TIMEFRAME_M1, 25, time.time())
            if not data or len(data) < 10:
                time.sleep(0.3)
                continue
            df = pd.DataFrame(data)
            df.rename(columns={"max":"high", "min":"low"}, inplace=True)
            df[["open","close","high","low","volume"]] = df[["open","close","high","low","volume"]].astype(float)
            return df
        except Exception as e:
            if "need reconnect" in str(e).lower():
                iq = connect()
            time.sleep(0.4)
    return None

# ====================================================
# 🚀 EJECUCIÓN (igual que tenías, se asegura que se llame)
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
                time.sleep(0.2)
        except Exception:
            iq = check_and_reconnect(iq)
    return False, None

# ====================================================
# 🧠 BUCLE PRINCIPAL — SEÑAL + EJECUCIÓN AUTOMÁTICA
# ====================================================
def main():
    global BOT_RUNNING, LOSS_STREAK, LAST_LOSS, DAILY_TRADES, LAST_TRADE, SEÑAL_PENDIENTE
    threading.Thread(target=listen_commands, daemon=True).start()
    iq = connect()
    last_candle = None
    send("ℹ️ SISTEMA LISTO — usa /start para operar")

    while True:
        try:
            if not BOT_RUNNING:
                time.sleep(0.5)
                continue
            reset_day()
            iq = check_and_reconnect(iq)
            if not iq:
                time.sleep(1)
                continue

            if DAILY_TRADES >= MAX_DAILY_TRADES:
                send("ℹ️ Límite diario alcanzado")
                BOT_RUNNING = False
                time.sleep(300)
                continue
            if LOSS_STREAK >= MAX_LOSS_STREAK:
                rest = int(PAUSE_TIME - (time.time() - LAST_LOSS))
                if rest > 0:
                    send(f"⏸️ Pausa {rest//60}min")
                    time.sleep(5)
                    continue
                LOSS_STREAK = 0

            st = iq.get_server_timestamp()
            sec = st % 60
            current_candle = int(st // 60)

            # ✅ EJECUTA INMEDIATAMENTE AL CAMBIAR VELA
            if current_candle != last_candle:
                last_candle = current_candle
                if SEÑAL_PENDIENTE:
                    p, sig, fz, tn = SEÑAL_PENDIENTE
                    SEÑAL_PENDIENTE = None
                    if (p, sig) == LAST_TRADE: continue
                    LAST_TRADE = (p, sig)
                    send(f"""🚀 EJECUTANDO: {p} | {tn} | {fz}
{'🟢 COMPRA' if sig=='call' else '🔴 VENTA'}""")
                    ok, tid = ejecutar_operacion(iq, BASE_AMOUNT, p, sig, EXPIRATION)
                    if ok:
                        DAILY_TRADES += 1
                        send(f"✅ Abierta — Total: {DAILY_TRADES}")
                        time.sleep(65)
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

            # ✅ BUSCA Y GUARDA SEÑAL
            if 10 <= sec <= 58:
                mejor = None
                max_fz = 0
                for par in PAIRS:
                    df = get_df(iq, par)
                    if df is None:
                        time.sleep(0.2)
                        continue
                    res = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                    if res:
                        sig, fz, tn = res
                        if fz >= FUERZA_MINIMA and fz > max_fz:
                            max_fz = fz
                            mejor = (par, sig, fz, tn)
                if 55 <= sec <= 58 and mejor:
                    SEÑAL_PENDIENTE = mejor
                    p, sig, fz, tn = mejor
                    send(f"🔍 Señal detectada: {p} {tn} | {fz}")

            time.sleep(0.05)

        except Exception:
            time.sleep(2)
            iq = check_and_reconnect(iq)

if __name__ == "__main__":
    req = ["IQ_EMAIL","IQ_PASSWORD","TELEGRAM_TOKEN","TELEGRAM_CHAT_ID"]
    faltan = [v for v in req if not os.getenv(v)]
    if faltan: print(f"❌ Faltan: {faltan}"); sys.exit(1)
    main()
