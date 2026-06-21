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

from strategy import get_reversal_signal
from iqoptionapi.stable_api import IQ_Option

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================================
# ⚙️ CONFIGURACIÓN - EJECUCIÓN PRECISA + PROTECCIÓN RAILWAY
# ==========================================
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPIRATION = 1
BASE_AMOUNT = 91
TIMEFRAME_M1 = 60

# Todos los pares OTC
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
MAX_SILENCE = 25  # ← NUEVO: detecta caída si no hay datos 25s (justo antes de corte Railway)

FUERZA_MINIMA = 32
TOLERANCIA_NIVEL = 0.0028
VENTANA_NIVELES = 5

TIEMPO_ESPERA_EJECUCION = 0.02
REINTENTOS_EJECUCION = 4
TIEMPO_MINIMO_VALIDO = 58

# Variables globales
DAILY_TRADES = 0
CURRENT_DAY = datetime.now(timezone.utc).day
LOSS_STREAK = 0
LAST_LOSS = 0
LAST_TRADE = None
BOT_RUNNING = False
SEÑAL_PENDIENTE = None
LAST_VALID_DATA = 0  # ← NUEVO: controla si el canal está vivo

# ====================================================
# 📱 FUNCIONES TELEGRAM
# ====================================================
def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=8
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
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if chat_id != str(CHAT_ID):
                    continue

                if text == "/start":
                    if not BOT_RUNNING:
                        BOT_RUNNING = True
                        send("✅ <b>BOT INICIADO</b>\nEstrategia: Reversión Bandas\nEjecución: EXACTA en vela siguiente")
                    else:
                        send("ℹ️ El bot ya está activo.")
                elif text == "/stop":
                    if BOT_RUNNING:
                        BOT_RUNNING = False
                        send("🛑 <b>BOT DETENIDO</b>")
                    else:
                        send("ℹ️ El bot ya está detenido.")

        except Exception as e:
            logging.error(f"Comandos: {str(e)}")
            time.sleep(1)

# ====================================================
# 🔄 REINICIO DIARIO
# ====================================================
def reset_day():
    global DAILY_TRADES, CURRENT_DAY, LOSS_STREAK, LAST_TRADE, SEÑAL_PENDIENTE
    today = datetime.now(timezone.utc).day
    if today != CURRENT_DAY:
        DAILY_TRADES = 0
        LOSS_STREAK = 0
        LAST_TRADE = None
        SEÑAL_PENDIENTE = None
        CURRENT_DAY = today
        if BOT_RUNNING:
            send("🔄 <b>NUEVO DÍA</b> | Contadores reiniciados.")

# ====================================================
# 🔌 CONEXIÓN + VERIFICACIÓN INTELIGENTE (sin errores)
# ====================================================
def connect():
    global LAST_VALID_DATA
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ ERROR: Credenciales no configuradas.")
                time.sleep(10)
                attempts += 1
                continue

            iq = IQ_Option(EMAIL, PASSWORD)
            ok, reason = iq.connect()
            time.sleep(2)  # Tiempo mínimo para estabilizar

            if ok:
                try:
                    # ✅ Prueba real: no solo "check_connect"
                    _ = iq.get_server_timestamp()
                    iq.change_balance("PRACTICE")
                    balance = iq.get_balance()
                    LAST_VALID_DATA = time.time()
                    send(f"✅ <b>CONECTADO</b>\nSaldo: ${balance:.2f}")
                    return iq
                except Exception as val_err:
                    logging.warning(f"Conectado sin datos: {val_err}")
                    ok = False
            else:
                send(f"❌ Conexión fallida: {reason}")
                
        except Exception as e:
            send(f"❌ Error conexión: {str(e)}")
        
        attempts += 1
        time.sleep(RECONNECT_DELAY)
    
    send("💥 Reintentando en 60s...")
    time.sleep(60)
    return connect()

def ensure_alive(iq):
    """✅ Nueva función: solo reconecta si está MUERTA, no por tiempo"""
    global LAST_VALID_DATA
    ahora = time.time()
    try:
        if iq and iq.check_connect() and (ahora - LAST_VALID_DATA < MAX_SILENCE):
            # Ping ligero
            iq.get_server_timestamp()
            LAST_VALID_DATA = ahora
            return iq
    except Exception:
        pass
    logging.info("🔄 Conexión caída — reconectando…")
    return connect()

# ====================================================
# 📥 OBTENER DATOS — SIN ERROR `need reconnect`
# ====================================================
def get_df(iq, pair, retries=2):
    global LAST_VALID_DATA
    for _ in range(retries):
        try:
            # ✅ Siempre verifica antes de pedir
            iq = ensure_alive(iq)
            if not iq:
                time.sleep(0.2)
                continue

            data = iq.get_candles(pair, TIMEFRAME_M1, 30, time.time())
            if not data or len(data) < 10:
                time.sleep(0.2)
                continue

            df = pd.DataFrame(data)
            df.rename(columns={"max": "high", "min": "low"}, inplace=True)
            df[["open","close","high","low","volume"]] = df[["open","close","high","low","volume"]].astype(float)
            LAST_VALID_DATA = time.time()
            return df

        except Exception as e:
            err = str(e).lower()
            logging.error(f"Datos {pair}: {str(e)}")
            # ✅ Solo reconecta si es error de conexión
            if "need reconnect" in err or "websocket" in err:
                iq = ensure_alive(iq)
            time.sleep(0.3)
    
    return None

# ====================================================
# 🚀 EJECUCIÓN PRECISA
# ====================================================
def ejecutar_operacion(iq, monto, par, direccion, vencimiento):
    global LAST_VALID_DATA
    for intento in range(REINTENTOS_EJECUCION + 1):
        try:
            iq = ensure_alive(iq)
            if not iq:
                continue
            
            tiempo_servidor = iq.get_server_timestamp()
            segundos_restantes = 60 - (tiempo_servidor % 60)
            
            if segundos_restantes < TIEMPO_MINIMO_VALIDO:
                return False, None
            
            time.sleep(TIEMPO_ESPERA_EJECUCION)
            status, trade_id = iq.buy(monto, par, direccion, vencimiento)
            LAST_VALID_DATA = time.time()
            
            if status and trade_id > 0:
                return True, trade_id
            
            if intento < REINTENTOS_EJECUCION:
                time.sleep(0.1)

        except Exception as e:
            if intento < REINTENTOS_EJECUCION:
                time.sleep(0.1)
    
    return False, None

# ====================================================
# 🧠 BUCLE PRINCIPAL
# ====================================================
def main():
    global BOT_RUNNING, LOSS_STREAK, LAST_LOSS, DAILY_TRADES, LAST_TRADE, SEÑAL_PENDIENTE
    threading.Thread(target=listen_commands, daemon=True).start()

    iq = connect()
    last_candle = None
    send("ℹ️ <b>SISTEMA LISTO</b>\nEjecución: solo vela siguiente\nEnvía /start para operar")

    while True:
        try:
            if not BOT_RUNNING:
                time.sleep(0.5)
                continue

            reset_day()

            iq = ensure_alive(iq)
            if not iq:
                time.sleep(1)
                continue

            if DAILY_TRADES >= MAX_DAILY_TRADES:
                send("ℹ️ Límite diario alcanzado.")
                BOT_RUNNING = False
                time.sleep(300)
                continue

            if LOSS_STREAK >= MAX_LOSS_STREAK:
                restante = int(PAUSE_TIME - (time.time() - LAST_LOSS))
                if restante > 0:
                    send(f"⏸️ Pausa: {restante//60} min")
                    time.sleep(5)
                    continue
                else:
                    LOSS_STREAK = 0
                    LAST_TRADE = None
                    send("✅ Pausa finalizada.")

            server_time = iq.get_server_timestamp()
            sec = server_time % 60
            current_candle = int(server_time // 60)

            # Ejecutar señal guardada
            if current_candle != last_candle:
                last_candle = current_candle
                
                if SEÑAL_PENDIENTE is not None:
                    pair, signal, fuerza, tipo_nivel = SEÑAL_PENDIENTE
                    SEÑAL_PENDIENTE = None

                    if (pair, signal) == LAST_TRADE:
                        continue
                    LAST_TRADE = (pair, signal)

                    send(f"""🚀 <b>EJECUTANDO ENTRADA</b>
💹 Activo: {pair}
📍 Zona: {tipo_nivel}
💪 Fuerza: {fuerza}/100
📊 Tipo: {'🟢 COMPRA' if signal == 'call' else '🔴 VENTA'}
⏱️ Vencimiento: 1 minuto""")

                    status, trade_id = ejecutar_operacion(iq, BASE_AMOUNT, pair, signal, EXPIRATION)

                    if status:
                        DAILY_TRADES += 1
                        send(f"✅ <b>OPERACIÓN ABIERTA</b> | ${BASE_AMOUNT:.2f} | Total: {DAILY_TRADES}/{MAX_DAILY_TRADES}")

                        time.sleep(65)
                        try:
                            res = iq.check_win_v4(trade_id)
                            LAST_VALID_DATA = time.time()
                            if res is None:
                                continue

                            if res < 0:
                                LOSS_STREAK += 1
                                LAST_LOSS = time.time()
                                send(f"❌ <b>PERDIDA</b> | -${abs(res):.2f}\nRacha: {LOSS_STREAK}/{MAX_LOSS_STREAK}")
                            else:
                                LOSS_STREAK = 0
                                send(f"✅ <b>GANADA</b> | +${res:.2f}\n_________________________")

                        except Exception as e:
                            send(f"⚠️ Verificación: {str(e)}")
                            iq = ensure_alive(iq)
                    else:
                        send(f"❌ No se pudo ejecutar en {pair}")

            # Buscar señales
            if 10 <= sec <= 58:
                mejor_opcion = None
                mayor_fuerza = 0

                for pair in PAIRS:
                    df = get_df(iq, pair)
                    if df is None:
                        time.sleep(0.1)  # Menos saturación
                        continue

                    resultado = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                    if resultado is not None:
                        signal, fuerza, tipo_nivel = resultado
                        if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor_opcion = (pair, signal, fuerza, tipo_nivel)

                if 55 <= sec <= 58 and mejor_opcion is not None:
                    SEÑAL_PENDIENTE = mejor_opcion
                    pair, signal, fuerza, tipo_nivel = mejor_opcion
                    send(f"""🔍 <b>SEÑAL DETECTADA</b>
💹 Activo: {pair}
📍 Nivel: {tipo_nivel}
💪 Fuerza: {fuerza}/100
⏳ EJECUCIÓN: SIGUIENTE VELA""")

            time.sleep(0.015)

        except Exception as e:
            send(f"💥 Error: {str(e)} | Reiniciando...")
            logging.exception("Error en bucle")
            time.sleep(2)
            iq = ensure_alive(iq)

if __name__ == "__main__":
    required = ["IQ_EMAIL", "IQ_PASSWORD", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"❌ Faltan variables: {', '.join(missing)}")
        sys.exit(1)
    main()
