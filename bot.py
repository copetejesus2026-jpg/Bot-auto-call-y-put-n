import time
import os
import pandas as pd
import logging
from threading import Thread
from iqoptionapi.stable_api import IQ_Option
from telegram import Bot
from telegram.error import TelegramError
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

MONTO = 600
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 98
REINTENTOS = 8
ESPERA = 0.2
SEG_DETECCION = 54
SEG_INICIO = 56
SEG_FIN = 59

ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

MAX_OPER_C1 = 15
MAX_OPER_C2 = 15
OPERACIONES_C1 = 0
OPERACIONES_C2 = 0

BOT_ACTIVO = False
ULTIMA_VELA = None
OP_VELA_C1 = None
OP_VELA_C2 = None
CUENTA_ANALISIS = 1

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OFFSET = 0

# --------------------------
# CONEXIÓN CUENTAS
# --------------------------
def conectar(email, clave, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, clave)
        ok, motivo = iq.connect()
        if ok:
            time.sleep(1)
            iq.change_balance("PRACTICE")
            saldo = round(iq.get_balance(), 2)
            mensaje = f"✅ {nombre} conectado | Saldo: ${saldo}"
            logger.info(mensaje)
            enviar_mensaje_telegram(mensaje)
            return iq, saldo
        else:
            mensaje = f"❌ Error {nombre}: {motivo}"
            logger.error(mensaje)
            enviar_mensaje_telegram(mensaje)
            return None, 0
    except Exception as e:
        mensaje = f"❌ Fallo {nombre}: {str(e)}"
        logger.error(mensaje)
        enviar_mensaje_telegram(mensaje)
        return None, 0

def conectar_ambas():
    iq1, _ = conectar(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
    time.sleep(2)
    iq2, _ = conectar(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
    return iq1, iq2

# --------------------------
# TELEGRAM SIN ERRORES NI CONFLICTOS
# --------------------------
def enviar_mensaje_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=texto, parse_mode="HTML")
    except TelegramError as e:
        logger.warning(f"⚠️ Telegram: {e}")

def limpiar_mensajes_antiguos():
    global OFFSET
    if not TELEGRAM_TOKEN:
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        updates = bot.get_updates(offset=-1, timeout=1)
        if updates:
            OFFSET = updates[-1].update_id + 1
        else:
            OFFSET = 0
        logger.info("📡 Telegram limpio y listo")
        enviar_mensaje_telegram("🤖 Bot listo. Usa /start para operar y /stop para detener.")
    except Exception as e:
        logger.warning(f"⚠️ Limpieza Telegram: {e}")

def escuchar_comandos():
    global BOT_ACTIVO, OPERACIONES_C1, OPERACIONES_C2, OFFSET
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Sin credenciales de Telegram")
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    limpiar_mensajes_antiguos()

    while True:
        try:
            updates = bot.get_updates(offset=OFFSET, timeout=10)
            for upd in updates:
                OFFSET = upd.update_id + 1
                if not upd.message or str(upd.message.chat_id) != str(TELEGRAM_CHAT_ID):
                    continue
                texto = upd.message.text.strip().lower()

                if texto == "/start":
                    if not BOT_ACTIVO:
                        OPERACIONES_C1 = 0
                        OPERACIONES_C2 = 0
                        BOT_ACTIVO = True
                        Thread(target=bucle_principal, daemon=True).start()
                        enviar_mensaje_telegram("✅ Bot INICIADO. Hará 15 operaciones en cada cuenta. CUENTA_2 invierte la dirección.")
                    else:
                        enviar_mensaje_telegram("ℹ️ Ya está activo.")

                elif texto == "/stop":
                    BOT_ACTIVO = False
                    enviar_mensaje_telegram("⏹️ Bot DETENIDO.")

        except TelegramError as e:
            if "Conflict" in str(e):
                OFFSET = 0
                time.sleep(2)
            else:
                logger.warning(f"⚠️ Telegram: {e}")
                time.sleep(3)
        except Exception as e:
            logger.warning(f"⚠️ Comandos: {e}")
            time.sleep(3)

# --------------------------
# DATOS DE MERCADO
# --------------------------
def obtener_velas(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.2)
        datos = iq.get_candles(activo, VELA, 50, time.time())
        if not datos or len(datos) < 30:
            return None
        df = pd.DataFrame(datos)
        df.rename(columns={"max":"high", "min":"low"}, inplace=True)
        df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
        return df
    except Exception as e:
        logger.error(f"⚠️ {activo}: {e}")
        return None

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela_actual, resultado):
    global OP_VELA_C1, OP_VELA_C2, OPERACIONES_C1, OPERACIONES_C2

    if nombre == "CUENTA_1":
        if OP_VELA_C1 == vela_actual or OPERACIONES_C1 >= MAX_OPER_C1:
            resultado["ok"] = False
            return
        dir_final = direccion  # ✅ Cuenta 1: dirección normal
    else:
        if OP_VELA_C2 == vela_actual or OPERACIONES_C2 >= MAX_OPER_C2:
            resultado["ok"] = False
            return
        dir_final = "put" if direccion == "call" else "call"  # ✅ Cuenta 2: dirección INVERTIDA

    logger.info(f"📤 Enviando a {nombre}: {activo} {dir_final} ${MONTO}")
    exito = False
    saldo_final = id_op = None

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)
            disponibles = iq.get_all_ACTIVES_OPCODE()
            if activo not in disponibles:
                logger.warning(f"⚠️ {activo} no disponible")
                time.sleep(0.3)
                continue
            estado, id_op = iq.buy(MONTO, activo, dir_final, EXPIRACION)
            if estado and id_op > 0:
                time.sleep(0.4)
                saldo_final = round(iq.get_balance(), 2)
                mensaje = f"✅ {nombre} | {activo} {dir_final} | ID: {id_op} | Saldo: ${saldo_final}"
                logger.info(mensaje)
                enviar_mensaje_telegram(mensaje)
                exito = True
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ {nombre} intento {intento+1}: {e}")
            time.sleep(ESPERA)

    if exito:
        if nombre == "CUENTA_1":
            OP_VELA_C1 = vela_actual
            OPERACIONES_C1 += 1
            enviar_mensaje_telegram(f"📊 C1: {OPERACIONES_C1}/15 completadas")
        else:
            OP_VELA_C2 = vela_actual
            OPERACIONES_C2 += 1
            enviar_mensaje_telegram(f"📊 C2: {OPERACIONES_C2}/15 completadas | Dirección invertida")
        resultado.update({"ok": True, "id": id_op, "saldo": saldo_final})
    else:
        resultado["ok"] = False
        enviar_mensaje_telegram(f"❌ Falló orden en {nombre} para {activo}")

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OP_VELA_C1, OP_VELA_C2, OPERACIONES_C1, OPERACIONES_C2, CUENTA_ANALISIS
    iq1, iq2 = conectar_ambas()
    if not iq1 or not iq2:
        enviar_mensaje_telegram("❌ No se conectaron las cuentas")
        BOT_ACTIVO = False
        return

    enviar_mensaje_telegram("🤖 BOT ACTIVO | Analizando señales...")
    senal = None

    while BOT_ACTIVO:
        try:
            if OPERACIONES_C1 >= MAX_OPER_C1 and OPERACIONES_C2 >= MAX_OPER_C2:
                enviar_mensaje_telegram("✅ 30 operaciones completadas. Bot detenido.")
                BOT_ACTIVO = False
                break

            ts = iq1.get_server_timestamp()
            seg = int(ts % 60)
            vela_act = int(ts // 60)

            if vela_act != ULTIMA_VELA:
                ULTIMA_VELA = vela_act
                OP_VELA_C1 = OP_VELA_C2 = None
                senal = None
                CUENTA_ANALISIS = 2 if CUENTA_ANALISIS == 1 else 1

            if seg == SEG_DETECCION:
                mejor = None
                fuerza_max = 0
                logger.info(f"🔍 Analizando con CUENTA_{CUENTA_ANALISIS}")
                iq_analisis = iq1 if CUENTA_ANALISIS == 1 else iq2

                for act in ACTIVOS:
                    df = obtener_velas(iq_analisis, act)
                    if df is None or df.empty:
                        continue
                    s = get_reversal_signal(df)
                    if s:
                        dir_ori, f, _ = s
                        if f >= FUERZA_MIN and f > fuerza_max:
                            fuerza_max = f
                            mejor = (act, dir_ori, f)

                if mejor:
                    act, dir_ori, f = mejor
                    senal = (act, dir_ori, f)
                    enviar_mensaje_telegram(f"🔔 Señal original: {act} {dir_ori} | Fuerza: {f}%")

            if senal and SEG_INICIO <= seg <= SEG_FIN:
                act, dir_ori, f = senal
                logger.info("🚀 Enviando órdenes")

                res1 = {"ok": False}
                res2 = {"ok": False}

                if OPERACIONES_C1 < MAX_OPER_C1:
                    t1 = Thread(target=ejecutar_orden, args=(iq1, "CUENTA_1", act, dir_ori, vela_act, res1))
                    t1.start()
                    t1.join()

                if OPERACIONES_C2 < MAX_OPER_C2:
                    t2 = Thread(target=ejecutar_orden, args=(iq2, "CUENTA_2", act, dir_ori, vela_act, res2))
                    t2.start()
                    t2.join()

                senal = None

            time.sleep(0.05)

        except Exception as e:
            enviar_mensaje_telegram(f"💥 Error: {str(e)} | Reconectando...")
            iq1, iq2 = conectar_ambas()
            time.sleep(3)

# --------------------------
# EJECUCIÓN
# --------------------------
if __name__ == "__main__":
    escuchar_comandos()
