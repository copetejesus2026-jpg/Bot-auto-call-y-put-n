import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import threading
import logging
from datetime import datetime, timezone

# Bloqueo total de mensajes de error de la librería
class BlockOutput:
    def __init__(self):
        self._stdout_fd = os.dup(1)
        self._stderr_fd = os.dup(2)
        self._null_fd = os.open(os.devnull, os.O_WRONLY)

    def __enter__(self):
        os.dup2(self._null_fd, 1)
        os.dup2(self._null_fd, 2)

    def __exit__(self, *args):
        os.dup2(self._stdout_fd, 1)
        os.dup2(self._stderr_fd, 2)
        os.close(self._null_fd)
        os.close(self._stdout_fd)
        os.close(self._stderr_fd)

# Silenciar librerías externas
logging.basicConfig(level=logging.CRITICAL)
logging.getLogger("iqoptionapi").setLevel(logging.CRITICAL)
logging.getLogger("websocket").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Importación protegida
with BlockOutput():
    from strategy import get_reversal_signal
    from iqoptionapi.stable_api import IQ_Option

# Mostrar solo tus mensajes
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True
)

# ==========================================
# ⚙️ CONFIGURACIÓN
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

MAX_DAILY_TRADES = 200
MAX_LOSS_STREAK = 5
PAUSE_TIME = 900
MAX_RECONNECT_ATTEMPTS = 99999
RECONNECT_DELAY = 1.0
KEEPALIVE_INTERVAL = 7
MAX_SILENCE = 10
REINTENTOS_EJECUCION = 8
TIEMPO_EJECUCION_SEG = 1.5

FUERZA_MINIMA = 28
TOLERANCIA_NIVEL = 0.0032
VENTANA_NIVELES = 4

# Variables globales
DAILY_TRADES = 0
CURRENT_DAY = datetime.now(timezone.utc).day
LOSS_STREAK = 0
LAST_LOSS = 0
LAST_TRADE = None
BOT_RUNNING = False
SEÑAL_PARA_EJECUTAR = None
LAST_PING = 0
ULTIMA_RESPUESTA = time.time()

# ====================================================
# 📱 TELEGRAM
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
                        send("✅ BOT ACTIVADO — Analiza toda la vela, entra al abrir la siguiente")
                        logging.info("✅ BOT INICIADO | ESTRATEGIA: Analiza toda vela → Entra al abrir siguiente")
                    else:
                        send("ℹ️ Ya está analizando")
                elif text == "/stop":
                    BOT_RUNNING = False
                    send("🛑 Detenido")
                    logging.info("🛑 DETENIDO")
        except Exception:
            time.sleep(0.5)

# ====================================================
# 🔄 REINICIO DIARIO
# ====================================================
def reset_day():
    global DAILY_TRADES, CURRENT_DAY, LOSS_STREAK, LAST_TRADE, SEÑAL_PARA_EJECUTAR
    hoy = datetime.now(timezone.utc).day
    if hoy != CURRENT_DAY:
        DAILY_TRADES = LOSS_STREAK = 0
        LAST_TRADE = SEÑAL_PARA_EJECUTAR = None
        CURRENT_DAY = hoy
        logging.info("🔄 Nuevo ciclo — contadores reiniciados")

# ====================================================
# 🔌 CONEXIÓN PERMANENTE
# ====================================================
def connect():
    global LAST_PING, ULTIMA_RESPUESTA
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ Faltan credenciales IQ")
                time.sleep(5)
                continue
            with BlockOutput():
                iq = IQ_Option(EMAIL, PASSWORD)
                ok, _ = iq.connect()
                time.sleep(0.5)
                if ok:
                    try:
                        _ = iq.get_server_timestamp()
                        iq.change_balance("PRACTICE")
                        balance = iq.get_balance()
                        LAST_PING = ULTIMA_RESPUESTA = time.time()
                        send(f"✅ CONECTADO | Saldo: ${balance:.2f}")
                        logging.info(f"✅ CONECTADO | Saldo: ${balance:.2f}")
                        return iq
                    except Exception:
                        ok = False
            attempts += 1
            time.sleep(RECONNECT_DELAY)
        except Exception:
            attempts += 1
            time.sleep(RECONNECT_DELAY)
    return connect()

def keep_alive_check(iq):
    global LAST_PING, ULTIMA_RESPUESTA
    ahora = time.time()
    try:
        if iq and iq.check_connect():
            if ahora - LAST_PING > KEEPALIVE_INTERVAL:
                with BlockOutput():
                    _ = iq.get_server_timestamp()
                LAST_PING = ahora
                ULTIMA_RESPUESTA = ahora
            if ahora - ULTIMA_RESPUESTA > MAX_SILENCE:
                logging.info("⚠️ Refrescando conexión…")
                return connect()
            return iq
    except Exception:
        pass
    return connect()

# ====================================================
# 📥 OBTENER VELAS
# ====================================================
def get_df(iq, pair, retries=4):
    for _ in range(retries):
        try:
            iq = keep_alive_check(iq)
            if not iq: continue
            logging.info(f"🔍 Analizando par: {pair} — durante toda la vela")
            with BlockOutput():
                data = iq.get_candles(pair, TIMEFRAME_M1, 22, time.time())
            if not data or len(data) < 8:
                continue
            df = pd.DataFrame(data)
            df.rename(columns={"max":"high", "min":"low"}, inplace=True)
            df[["open","close","high","low","volume"]] = df[["open","close","high","low","volume"]].astype(float)
            ULTIMA_RESPUESTA = time.time()
            return df
        except Exception:
            continue
    return None

# ====================================================
# 🚀 EJECUCIÓN JUSTO AL ABRIR VELA
# ====================================================
def ejecutar_operacion_al_abrir(iq, monto, par, direccion, vencimiento):
    for intento in range(REINTENTOS_EJECUCION + 1):
        try:
            iq = keep_alive_check(iq)
            if not iq: continue
            ts = iq.get_server_timestamp()
            sec = ts % 60
            if 1 <= sec <= 2:
                time.sleep(TIEMPO_EJECUCION_SEG - 1)
                with BlockOutput():
                    ok, tid = iq.buy(monto, par, direccion, vencimiento)
                if ok and tid > 0:
                    logging.info(f"✅ EJECUTADO AL ABRIR VELA: {par} | {direccion.upper()} | Seg: {sec}")
                    return True, tid
            if intento < REINTENTOS_EJECUCION:
                time.sleep(0.1)
        except Exception:
            iq = keep_alive_check(iq)
    return False, None

# ====================================================
# 🧠 BUCLE PRINCIPAL
# ====================================================
def main():
    global BOT_RUNNING, LOSS_STREAK, LAST_LOSS, DAILY_TRADES, LAST_TRADE, SEÑAL_PARA_EJECUTAR
    threading.Thread(target=listen_commands, daemon=True).start()
    iq = connect()
    vela_anterior = None
    logging.info("ℹ️ SISTEMA LISTO — Analiza toda la vela, entra segundo 1‑2 siguiente")
    send("ℹ️ SISTEMA LISTO — usa /start")

    while True:
        try:
            if not BOT_RUNNING:
                time.sleep(0.2)
                continue
            reset_day()
            iq = keep_alive_check(iq)
            if not iq:
                time.sleep(0.3)
                continue

            if DAILY_TRADES >= MAX_DAILY_TRADES:
                logging.info("ℹ️ Límite diario alcanzado")
                BOT_RUNNING = False
                time.sleep(120)
                continue
            if LOSS_STREAK >= MAX_LOSS_STREAK:
                rest = int(PAUSE_TIME - (time.time() - LAST_LOSS))
                if rest > 0:
                    time.sleep(2)
                    continue
                LOSS_STREAK = 0

            ts = iq.get_server_timestamp()
            segundo_actual = ts % 60
            vela_actual = int(ts // 60)

            # Ejecutar al abrir nueva vela
            if vela_actual != vela_anterior:
                vela_anterior = vela_actual
                if SEÑAL_PARA_EJECUTAR:
                    p, sig, fz, tn = SEÑAL_PARA_EJECUTAR
                    SEÑAL_PARA_EJECUTAR = None
                    if (p, sig) == LAST_TRADE: continue
                    LAST_TRADE = (p, sig)
                    send(f"""🚀 SEÑAL LISTA PARA EJECUCIÓN AL ABRIR
📈 {p} | {tn} | Fuerza: {fz}
{'🟢 COMPRA' if sig=='call' else '🔴 VENTA'}""")
                    ok, tid = ejecutar_operacion_al_abrir(iq, BASE_AMOUNT, p, sig, EXPIRATION)
                    if ok:
                        DAILY_TRADES += 1
                        send(f"✅ ABIERTA AL ABRIR VELA — Total: {DAILY_TRADES}")
                        time.sleep(62)
                        try:
                            with BlockOutput():
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

            # Analizar toda la vela (segundos 3‑59)
            if 3 <= segundo_actual <= 59:
                mejor = None
                max_fz = 0
                for par in PAIRS:
                    df = get_df(iq, par)
                    if df is None: continue
                    res = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                    if res:
                        sig, fz, tn = res
                        if fz >= FUERZA_MINIMA and fz > max_fz:
                            max_fz = fz
                            mejor = (par, sig, fz, tn)
                if mejor:
                    SEÑAL_PARA_EJECUTAR = mejor
                    p, sig, fz, tn = mejor
                    logging.info(f"🎯 SEÑAL VALIDADA EN VELA ACTUAL: {p} | {tn} | Fuerza: {fz} — EJECUCIÓN AL ABRIR SIGUIENTE")
                    send(f"🔍 Señal confirmada: {p} | Entra al abrir próxima vela")

            time.sleep(0.03)

        except Exception:
            logging.info("ℹ️ Recuperación automática")
            time.sleep(0.8)
            iq = keep_alive_check(iq)

if __name__ == "__main__":
    req = ["IQ_EMAIL","IQ_PASSWORD","TELEGRAM_TOKEN","TELEGRAM_CHAT_ID"]
    faltan = [v for v in req if not os.getenv(v)]
    if faltan:
        print(f"❌ Faltan variables: {faltan}"); sys.exit(1)
    if not os.path.exists("strategy.py"):
        print("❌ Falta strategy.py"); sys.exit(1)
    main()
