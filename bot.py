import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import threading
import logging
import gc
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================================
# ⚙️ CONFIGURACIÓN PARA RAILWAY / CLOUD
# ==========================================
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPIRATION = 1
BASE_AMOUNT = 25
TIMEFRAME_M1 = 60

PARES = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC",
    "AUDUSD-OTC", "USDCAD-OTC", "USDCHF-OTC", "NZDUSD-OTC",
    "EURCAD-OTC", "GBPCAD-OTC", "GBPCHF-OTC", "AUDJPY-OTC", "CADJPY-OTC",
    "NZDJPY-OTC", "AUDNZD-OTC", "EURCHF-OTC"
]

MAX_DAILY_TRADES = 100
MAX_LOSS_STREAK = 5
PAUSE_TIME = 900
MAX_RECONNECT_ATTEMPTS = 15
RECONNECT_DELAY = 5
RECONNECT_DELAY_LONG = 30
PING_INTERVAL = 20           # Comprobar conexión cada 20 seg
MAX_SILENCE = 45             # Reiniciar si no hay datos 45 seg

FUERZA_MINIMA = 35
TOLERANCIA_NIVEL = 0.0018
VENTANA_NIVELES = 5

TIEMPO_ESPERA_EJECUCION = 0.3
REINTENTOS_EJECUCION = 4
TIEMPO_MINIMO_VALIDO = 57
ESPERA_TRAS_ERROR = 1.2

# Variables globales
DAILY_TRADES = 0
CURRENT_DAY = datetime.now(timezone.utc).day
LOSS_STREAK = 0
LAST_LOSS = 0
LAST_TRADE = None
BOT_RUNNING = False
SEÑAL_PENDIENTE = None
IQ_API = None
LAST_PING = 0
LAST_DATA = 0

# ====================================================
# 📱 TELEGRAM
# ====================================================
def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=12
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
# 🔌 CONEXIÓN + PING MANUAL — CLAVE DEL ÉXITO
# ====================================================
from iqoptionapi.stable_api import IQ_Option

def connect():
    global IQ_API, LAST_PING, LAST_DATA
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ Faltan credenciales IQ_EMAIL / IQ_PASSWORD")
                time.sleep(RECONNECT_DELAY_LONG)
                attempts += 1
                continue
            # ✅ Limpieza total para evitar sesiones fantasma
            if IQ_API is not None:
                try:
                    IQ_API.close_connect()
                except:
                    pass
                IQ_API = None
                gc.collect()
            time.sleep(2)

            IQ_API = IQ_Option(EMAIL, PASSWORD)
            ok, reason = IQ_API.connect()
            time.sleep(3) # Tiempo extra para estabilizar WebSocket

            if ok:
                IQ_API.change_balance("PRACTICE") # Cambia a "REAL" si deseas
                balance = IQ_API.get_balance()
                LAST_PING = LAST_DATA = time.time()
                send(f"✅ CONECTADO | Saldo: ${balance:.2f}")
                return IQ_API
            else:
                logging.warning(f"Intento {attempts+1}: {reason}")
        except Exception as e:
            logging.error(f"Error conexión: {str(e)}")
        attempts += 1
        time.sleep(RECONNECT_DELAY)
    send("💥 Reinicio completo en 40 seg…")
    time.sleep(40)
    return connect()

def ping_server():
    """Mantiene conexión viva sin esperar errores"""
    global LAST_PING, LAST_DATA
    try:
        if IQ_API and IQ_API.check_connect():
            _ = IQ_API.get_server_timestamp()
            LAST_PING = time.time()
            return True
    except:
        return False
    return False

def ensure_connection():
    """Verificación PREVENTIVA: reconecta ANTES de que falle"""
    global IQ_API, LAST_DATA
    now = time.time()
    # Ping periódico
    if now - LAST_PING > PING_INTERVAL:
        ping_server()
    # Reinicio si no hay actividad
    if not IQ_API or not IQ_API.check_connect() or (now - LAST_DATA > MAX_SILENCE):
        logging.warning("⚠️ Sesión inactiva o rota — reiniciando…")
        IQ_API = connect()
    return IQ_API is not None

# ====================================================
# 📥 OBTENER VELAS — LLAMADA SEGURA
# ====================================================
def get_df(iq, pair, retries=5):
    global LAST_DATA
    for _ in range(retries):
        try:
            if not ensure_connection():
                time.sleep(ESPERA_TRAS_ERROR)
                continue
            data = iq.get_candles(pair, TIMEFRAME_M1, 25, time.time())
            if not data or len(data) < 10:
                time.sleep(0.5)
                continue
            df = pd.DataFrame(data)
            df.rename(columns={"max":"high", "min":"low"}, inplace=True)
            df[["open","close","high","low","volume"]] = df[["open","close","high","low","volume"]].astype(float)
            LAST_DATA = time.time() # ✅ Confirma actividad válida
            return df
        except Exception as e:
            err = str(e).lower()
            logging.error(f"{pair}: {err}")
            # ✅ Reconexión inmediata solo al error exacto
            if "need reconnect" in err or "connection" in err or "timed out" in err:
                ensure_connection()
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
            if ok and tid>0:
                return True, tid
            if intento<REINTENTOS_EJECUCION: time.sleep(0.4)
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
    last_candle = None

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

            if current_candle != last_candle:
                last_candle = current_candle
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

            if 10 <= sec <= 57:
                mejor = None; max_fz = 0
                for par in PARES:
                    df = get_df(IQ_API, par)
                    if df is None: continue
                    try:
                        from strategy import get_reversal_signal
                        resultado = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                        if resultado and isinstance(resultado, tuple) and len(resultado)==3:
                            sig, fz, tn = resultado
                            if isinstance(fz, (int,float)) and fz>=FUERZA_MINIMA and fz>max_fz:
                                max_fz=fz; mejor=(par, sig, fz, tn)
                    except Exception as e:
                        logging.error(f"Estrategia {par}: {e}")
                if 55 <= sec <= 57 and mejor:
                    SEÑAL_PENDIENTE = mejor
                    par, sig, fz, tn = mejor
                    send(f"🔍 Señal {par} {tn} | Fuerza: {fz}")
            time.sleep(0.1)
        except Exception as e:
            send(f"💥 Error: {str(e)} — reconectando…")
            logging.exception("Error global")
            time.sleep(3)
            ensure_connection()

if __name__ == "__main__":
    req = ["IQ_EMAIL","IQ_PASSWORD","TELEGRAM_TOKEN","TELEGRAM_CHAT_ID"]
    faltan = [v for v in req if not os.getenv(v)]
    if faltan: print(f"❌ Faltan vars: {faltan}"); sys.exit(1)
    if not os.path.exists("strategy.py"): print("❌ Falta strategy.py"); sys.exit(1)
    main()
