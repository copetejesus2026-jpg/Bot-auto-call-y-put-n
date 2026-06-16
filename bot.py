import time
import os
import pandas as pd
import logging
from threading import Thread, Lock

# Importación segura para evitar errores de arranque
try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("❌ Falta instalar iqoptionapi")
    exit(1)

try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    print("❌ Falta instalar python-telegram-bot")
    exit(1)

from strategy import get_reversal_signal

# --------------------------
# CONFIGURACIÓN
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Parámetros de operación
MONTO = 100
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 75

# Entrada solo entre segundo 1 y 6
SEG_INICIO_ENTRADA = 1
SEG_FIN_ENTRADA = 6

REINTENTOS = 5
ESPERA = 0.1
REINTENTOS_POR_CUENTA = 15
ESPERA_REINTENTO = 2

ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

MAX_OPER = 15
OPERACIONES_C1 = 0
OPERACIONES_C2 = 0

BOT_ACTIVO = False
ULTIMA_VELA_CERRADA = None
SEÑAL_PENDIENTE = None
YA_OPERO = {}
IQ1 = None
IQ2 = None
lock = Lock()

# Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL_1 = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD_1 = os.getenv("IQ_PASSWORD_1", "")
IQ_EMAIL_2 = os.getenv("IQ_EMAIL_2", "")
IQ_PASSWORD_2 = os.getenv("IQ_PASSWORD_2", "")

# --------------------------
# TELEGRAM
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Sin credenciales de Telegram")
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
        logger.info(f"📤 Notificación enviada")
    except Exception as e:
        logger.error(f"❌ Telegram: {str(e)}")

# --------------------------
# SALDO ACTUALIZADO
# --------------------------
def obtener_saldo_actualizado(iq):
    for _ in range(5):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)
            saldo = iq.get_balance()
            if saldo is not None and isinstance(saldo, (int, float)) and saldo >= 0:
                return round(saldo, 2)
            time.sleep(0.1)
        except:
            time.sleep(0.1)
    return 0.0

# --------------------------
# CONEXIÓN
# --------------------------
def conectar_cuenta(email, password, nombre):
    if not email or not password:
        logger.error(f"❌ {nombre}: Faltan credenciales")
        return None, 0.0
    for intento in range(REINTENTOS_POR_CUENTA):
        try:
            logger.info(f"🔄 {nombre} - Intento {intento+1}/{REINTENTOS_POR_CUENTA}")
            iq = IQ_Option(email, password)
            ok, motivo = iq.connect()
            if ok:
                time.sleep(0.5)
                iq.change_balance("PRACTICE")
                time.sleep(0.3)
                saldo = obtener_saldo_actualizado(iq)
                logger.info(f"✅ {nombre} conectado | Saldo: ${saldo}")
                return iq, saldo
            else:
                logger.warning(f"⚠️ {nombre} falló: {motivo}")
        except Exception as e:
            logger.error(f"❌ {nombre} error: {str(e)}")
        time.sleep(ESPERA_REINTENTO)
    return None, 0.0

def conectar_ambas():
    global IQ1, IQ2
    enviar_telegram("🔄 Conectando cuentas...")
    IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
    IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")
    while IQ1 is None or IQ2 is None:
        time.sleep(3)
        if IQ1 is None: IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
        if IQ2 is None: IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")
    enviar_telegram(
        f"✅ Cuentas conectadas\n"
        f"🔹 Cuenta 1: ${saldo1}\n🔹 Cuenta 2: ${saldo2}\n"
        f"🚀 Modo: Analizar vela cerrada → Entrar segundo 1-6"
    )
    return True

# --------------------------
# OBTENER VELAS
# --------------------------
def obtener_velas_cerradas(iq, activo):
    try:
        if not iq or not iq.check_connect():
            return None
        ts_antes = int(time.time()) - 2
        velas = iq.get_candles(activo, VELA, 50, ts_antes)
        if not velas or len(velas) < 30:
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max":"high", "min":"low"}, inplace=True)
        df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
        return df
    except Exception as e:
        logger.warning(f"⚠️ {activo}: {str(e)}")
        return None

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela_id, resultado):
    clave = f"{nombre}_{vela_id}"
    if YA_OPERO.get(clave, False):
        resultado["ok"] = False
        resultado["razon"] = "Ya operó"
        return
    exito = False
    id_op = None
    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)
            saldo = obtener_saldo_actualizado(iq)
            if saldo < MONTO:
                resultado["ok"] = False
                resultado["razon"] = f"Saldo insuficiente: ${saldo}"
                return
            ok, id_op = iq.buy(MONTO, activo, direccion, EXPIRACION)
            if ok and id_op > 0:
                saldo_final = obtener_saldo_actualizado(iq)
                exito = True
                logger.info(f"✅ {nombre} | {activo} | {direccion.upper()} | ID: {id_op} | Saldo: ${saldo_final}")
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ Intento {intento+1} {nombre}: {str(e)}")
            time.sleep(ESPERA)
    if exito:
        with lock: YA_OPERO[clave] = True
        resultado.update({"ok":True, "direccion":direccion.upper(), "id":id_op, "saldo":saldo_final})
    else:
        resultado["ok"] = False
        resultado["razon"] = "No se ejecutó"

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA_CERRADA, SEÑAL_PENDIENTE, OPERACIONES_C1, OPERACIONES_C2
    logger.info("🔁 BOT INICIADO: Analizando vela a vela")
    while BOT_ACTIVO:
        try:
            if not IQ1 or not IQ2 or not IQ1.check_connect() or not IQ2.check_connect():
                logger.warning("⚠️ Reconectando...")
                if not conectar_ambas():
                    BOT_ACTIVO = False
                    break
                continue
            ts_servidor = IQ1.get_server_timestamp()
            segundos = ts_servidor % 60
            vela_actual = int(ts_servidor // 60)
            vela_ya_cerrada = vela_actual - 1

            if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
                s1 = obtener_saldo_actualizado(IQ1)
                s2 = obtener_saldo_actualizado(IQ2)
                enviar_telegram(f"✅ FIN\n1: {OPERACIONES_C1}/{MAX_OPER} | ${s1}\n2: {OPERACIONES_C2}/{MAX_OPER} | ${s2}")
                BOT_ACTIVO = False
                break

            if vela_ya_cerrada != ULTIMA_VELA_CERRADA:
                ULTIMA_VELA_CERRADA = vela_ya_cerrada
                SEÑAL_PENDIENTE = None
                YA_OPERO.clear()
                logger.info(f"🔍 Analizando vela cerrada: {vela_ya_cerrada}")
                mejor_senal = None
                fuerza_max = 0
                for activo in ACTIVOS:
                    df = obtener_velas_cerradas(IQ1, activo)
                    if df is None: continue
                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, tipo = senal
                        logger.info(f"ℹ️ {activo}: {dirr.upper()} | Fuerza: {fuerza}%")
                        if fuerza >= FUERZA_MIN and fuerza > fuerza_max:
                            fuerza_max = fuerza
                            mejor_senal = (activo, dirr, fuerza)
                if mejor_senal:
                    SEÑAL_PENDIENTE = mejor_senal
                    enviar_telegram(
                        f"📊 SEÑAL DETECTADA\n"
                        f"📈 Activo: {SEÑAL_PENDIENTE[0]}\n"
                        f"➡️ Dirección: {SEÑAL_PENDIENTE[1].upper()}\n"
                        f"💪 Fuerza: {SEÑAL_PENDIENTE[2]}%\n"
                        f"⏱️ Entrada: segundo {SEG_INICIO_ENTRADA}-{SEG_FIN_ENTRADA}"
                    )

            if SEÑAL_PENDIENTE and SEG_INICIO_ENTRADA <= segundos <= SEG_FIN_ENTRADA:
                activo, direccion, fuerza = SEÑAL_PENDIENTE
                logger.info(f"⚡ ENTRADA | Segundo {segundos} | {activo}")
                enviar_telegram("⚡ Ejecutando operación...")
                res1 = {"ok":False}
                res2 = {"ok":False}
                h1 = Thread(target=ejecutar_orden, args=(IQ1, "CUENTA 1", activo, direccion, vela_actual, res1))
                h2 = Thread(target=ejecutar_orden, args=(IQ2, "CUENTA 2", activo, direccion, vela_actual, res2))
                h1.start(); h2.start(); h1.join(); h2.join()
                if res1["ok"] and res2["ok"]:
                    OPERACIONES_C1 += 1
                    OPERACIONES_C2 += 1
                    enviar_telegram(
                        f"✅ OPERACIÓN EXITOSA\n"
                        f"🔹 Cuenta 1: {res1['direccion']} | ${res1['saldo']}\n"
                        f"🔹 Cuenta 2: {res2['direccion']} | ${res2['saldo']}\n"
                        f"📊 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                    )
                else:
                    motivo = []
                    if not res1["ok"]: motivo.append(f"1: {res1.get('razon','error')}")
                    if not res2["ok"]: motivo.append(f"2: {res2.get('razon','error')}")
                    enviar_telegram(f"❌ Falló: {' | '.join(motivo)}")
                SEÑAL_PENDIENTE = None

            time.sleep(0.1)
        except Exception as e:
            logger.error(f"💥 Error: {str(e)}")
            enviar_telegram(f"⚠️ Error: {str(e)}")
            time.sleep(2)

# --------------------------
# ARRANQUE SEGURO
# --------------------------
if __name__ == "__main__":
    logger.info("🤖 INICIANDO BOT...")
    try:
        if TELEGRAM_TOKEN:
            Bot(token=TELEGRAM_TOKEN).delete_webhook(drop_pending_updates=True)
    except:
        pass
    enviar_telegram("🤖 BOT INICIADO ✅")
    if conectar_ambas():
        BOT_ACTIVO = True
        Thread(target=bucle_principal, daemon=True).start()
    else:
        enviar_telegram("❌ No se pudo conectar. Revisa credenciales.")
