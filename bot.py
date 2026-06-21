import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import threading
import logging
from datetime import datetime, timezone

from iqoptionapi.stable_api import IQ_Option

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================================
# ⚙️ CONFIGURACIÓN OPTIMIZADA PARA RAILWAY
# ==========================================
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPIRATION = 1
BASE_AMOUNT = 25
TIMEFRAME_M1 = 60

# Pares OTC 24/7
PAIRS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC",
    "AUDUSD-OTC", "USDCAD-OTC", "USDCHF-OTC", "NZDUSD-OTC",
    "EURCAD-OTC", "GBPCAD-OTC", "GBPCHF-OTC", "AUDJPY-OTC", "CADJPY-OTC",
    "NZDJPY-OTC", "AUDNZD-OTC", "EURCHF-OTC"
]

MAX_DAILY_TRADES = 100
MAX_LOSS_STREAK = 5
PAUSE_TIME = 900
MAX_RECONNECT_ATTEMPTS = 15   # Más intentos para Railway
RECONNECT_DELAY = 3
RECONNECT_DELAY_LONG = 15

FUERZA_MINIMA = 35
TOLERANCIA_NIVEL = 0.0018
VENTANA_NIVELES = 5

TIEMPO_ESPERA_EJECUCION = 0.2
REINTENTOS_EJECUCION = 4
TIEMPO_MINIMO_VALIDO = 58
ESPERA_TRAS_ERROR = 0.5

# Variables globales de conexión
DAILY_TRADES = 0
CURRENT_DAY = datetime.now(timezone.utc).day
LOSS_STREAK = 0
LAST_LOSS = 0
LAST_TRADE = None
BOT_RUNNING = False
SEÑAL_PENDIENTE = None
IQ_API = None

# ====================================================
# 📱 TELEGRAM
# ====================================================
def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10
            )
        except Exception as e:
            logging.error(f"Telegram: {str(e)}")

def listen_commands():
    global BOT_RUNNING
    last_update_id = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={"offset": last_update_id + 1, "timeout": 30},
                timeout=35
            )
            data = res.json()
            if not data.get("ok"):
                time.sleep(2)
                continue
            for update in data.get("result", []):
                last_update_id = update["update_id"]
                text = update.get("message", {}).get("text", "").strip().lower()
                chat_id = str(update["message"]["chat"]["id"])
                if chat_id != str(CHAT_ID): continue
                if text == "/start":
                    if not BOT_RUNNING:
                        BOT_RUNNING = True
                        send("✅ <b>BOT INICIADO</b> — analizando pares OTC")
                    else: send("ℹ️ Ya está activo")
                elif text == "/stop":
                    BOT_RUNNING = False
                    send("🛑 Detenido")
        except Exception as e:
            logging.error(f"Comandos: {e}")
            time.sleep(1)

# ====================================================
# 🔄 REINICIO DIARIO
# ====================================================
def reset_day():
    global DAILY_TRADES, CURRENT_DAY, LOSS_STREAK, LAST_TRADE, SEÑAL_PENDIENTE
    today = datetime.now(timezone.utc).day
    if today != CURRENT_DAY:
        DAILY_TRADES = LOSS_STREAK = 0
        LAST_TRADE = SEÑAL_PENDIENTE = None
        CURRENT_DAY = today
        if BOT_RUNNING: send("🔄 Nuevo día — contadores reiniciados")

# ====================================================
# 🔌 CONEXIÓN 100 % FIABLE (clave contra el error)
# ====================================================
def connect():
    global IQ_API
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ Faltan credenciales IQ_EMAIL / IQ_PASSWORD")
                time.sleep(RECONNECT_DELAY_LONG)
                attempts += 1
                continue
            # Cierra sesión anterior antes de crear nueva
            if IQ_API and hasattr(IQ_API, "close_connect"):
                try: IQ_API.close_connect()
                except: pass
            IQ_API = IQ_Option(EMAIL, PASSWORD)
            ok, reason = IQ_API.connect()
            if ok:
                IQ_API.change_balance("PRACTICE") # Cambia a REAL si quieres
                balance = IQ_API.get_balance()
                send(f"✅ CONECTADO | Saldo: ${balance:.2f}")
                return IQ_API
            else:
                logging.warning(f"Intento {attempts+1} fallido: {reason}")
        except Exception as e:
            logging.error(f"Error conexión: {str(e)}")
        attempts += 1
        time.sleep(RECONNECT_DELAY)
    send("💥 Máximos intentos — reiniciando ciclo completo")
    time.sleep(30)
    return connect()

def ensure_connection():
    """Verificación obligatoria antes de cada operación/dato"""
    global IQ_API
    try:
        if IQ_API and IQ_API.check_connect():
            return True
    except Exception as e:
        logging.warning(f"Conexión perdida: {e}")
    logging.info("Reconectando automáticamente…")
    IQ_API = connect()
    return IQ_API is not None

# ====================================================
# 📥 OBTENER VELAS — SIN ERROR RECONNECT
# ====================================================
def get_df(iq, pair, retries=4):
    for _ in range(retries):
        try:
            if not ensure_connection():
                time.sleep(ESPERA_TRAS_ERROR)
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
            logging.error(f"{pair}: {str(e)}")
            if "reconnect" in str(e).lower():
                ensure_connection() # Forzar reconexión exacta al error
            time.sleep(ESPERA_TRAS_ERROR)
    return None

# ====================================================
# 🚀 EJECUCIÓN SEGURA
# ====================================================
def ejecutar_operacion(iq, monto, par, direccion, vencimiento):
    for intento in range(REINTENTOS_EJECUCION+1):
        try:
            if not ensure_connection(): continue
            ts = iq.get_server_timestamp()
            sec_rest = 60 - (ts % 60)
            if sec_rest < TIEMPO_MINIMO_VALIDO:
                logging.warning(f"Tiempo insuficiente {sec_rest}s")
                return False, None
            time.sleep(TIEMPO_ESPERA_EJECUCION)
            ok, tid = iq.buy(monto, par, direccion, vencimiento)
            if ok and tid>0: return True, tid
            if intento<REINTENTOS_EJECUCION: time.sleep(0.3)
        except Exception as e:
            logging.error(f"Operación: {e}")
            ensure_connection()
    return False, None

# ====================================================
# 🧠 BUCLE PRINCIPAL
# ====================================================
def main():
    global BOT_RUNNING, LOSS_STREAK, LAST_LOSS, DAILY_TRADES, LAST_TRADE, SEÑAL_PENDIENTE, IQ_API
    threading.Thread(target=listen_commands, daemon=True).start()
    IQ_API = connect()
    send("ℹ️ Sistema listo — usa /start para operar")
    while True:
        try:
            if not BOT_RUNNING: time.sleep(1); continue
            reset_day()
            if not ensure_connection(): time.sleep(2); continue
            if DAILY_TRADES >= MAX_DAILY_TRADES:
                send("ℹ️ Límite diario alcanzado")
                BOT_RUNNING=False; time.sleep(300); continue
            if LOSS_STREAK >= MAX_LOSS_STREAK:
                rest = int(PAUSE_TIME - (time.time()-LAST_LOSS))
                if rest>0: send(f"⏸️ Pausa {rest//60}min"); time.sleep(5); continue
                else: LOSS_STREAK=0; send("✅ Pausa finalizada")

            st = IQ_API.get_server_timestamp()
            sec = st % 60
            current_candle = int(st // 60)

            # Ejecutar señal al inicio de vela
            if current_candle != (last_candle := locals().get("last_candle")):
                locals()["last_candle"] = current_candle
                if SEÑAL_PENDIENTE:
                    p, sig, fz, tn = SEÑAL_PENDIENTE
                    SEÑAL_PENDIENTE = None
                    if (p,sig) == LAST_TRADE: continue
                    LAST_TRADE = (p,sig)
                    send(f"""🚀 OPERACIÓN
💹 {p} | 📍 {tn.upper()} | 💪 {fz}
{'🟢 COMPRA' if sig=='call' else '🔴 VENTA'}""")
                    ok, tid = ejecutar_operacion(IQ_API, BASE_AMOUNT, p, sig, EXPIRATION)
                    if ok:
                        DAILY_TRADES +=1
                        send(f"✅ Abierta — Total: {DAILY_TRADES}")
                        time.sleep(65)
                        try:
                            res = IQ_API.check_win_v4(tid)
                            if res<0:
                                LOSS_STREAK+=1; LAST_LOSS=time.time()
                                send(f"❌ -${abs(res):.2f} | Racha {LOSS_STREAK}")
                            else:
                                LOSS_STREAK=0
                                send(f"✅ +${res:.2f}")
                        except Exception as e:
                            send(f"⚠️ Verificación: {e}")
                            ensure_connection()
                    else: send(f"❌ Falló en {p}")

            # Buscar señales
            if 10 <= sec <= 57:
                mejor = None; max_fz = 0
                for par in PAIRS:
                    df = get_df(IQ_API, par)
                    if df is None: continue
                    try:
                        from strategy import get_reversal_signal
                        res = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                        if res: sig, fz, tn = res
                        if fz >= FUERZA_MINIMA and fz>max_fz:
                            max_fz=fz; mejor=(par, sig, fz, tn)
                    except Exception as e:
                        logging.error(f"Estrategia {par}: {e}")
                if 55 <= sec <= 57 and mejor:
                    SEÑAL_PENDIENTE = mejor
                    par, sig, fz, tn = mejor
                    send(f"🔍 Señal {par} {tn} {fz} — entra siguiente vela")
            time.sleep(0.05)
        except Exception as e:
            send(f"💥 Error: {str(e)} — reconectando…")
            logging.exception("Error global")
            time.sleep(2)
            ensure_connection()

if __name__ == "__main__":
    req = ["IQ_EMAIL","IQ_PASSWORD","TELEGRAM_TOKEN","TELEGRAM_CHAT_ID"]
    faltan = [v for v in req if not os.getenv(v)]
    if faltan: print(f"❌ Faltan vars: {faltan}"); sys.exit(1)
    if not os.path.exists("strategy.py"): print("❌ Falta strategy.py"); sys.exit(1)
    main()
