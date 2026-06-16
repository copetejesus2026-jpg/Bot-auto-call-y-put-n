import time
import os
import pandas as pd
import logging
import sys

try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("❌ Falta instalar iqoptionapi")
    sys.exit(1)

try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    print("❌ Falta instalar python-telegram-bot")
    sys.exit(1)

from strategy import get_reversal_signal

# --------------------------
# CONFIGURACIÓN DE LOGS
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --------------------------
# PARÁMETROS DE OPERACIÓN
# --------------------------
MONTO = 600                # Monto por operación
EXPIRACION = 1             # Vencimiento en minutos
VELA = 60                  # Marco de tiempo en segundos
FUERZA_MIN = 75            # Fuerza mínima para tomar señal

# 🕒 TIEMPOS DE ENTRADA
SEG_INICIO_CUENTA1 = 1     # Cuenta 1 entra rápido
SEG_FIN_CUENTA1 = 3
SEG_INICIO_CUENTA2 = 3     # Cuenta 2 espera y reintenta
SEG_FIN_CUENTA2 = 6

REINTENTOS = 5             # Cantidad de intentos para Cuenta 2
ESPERA = 0.08
REINTENTOS_POR_CUENTA = 8
ESPERA_REINTENTO = 2

ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

MAX_OPER = 20
OPERACIONES_C1 = 0
OPERACIONES_C2 = 0

BOT_ACTIVO = True
ULTIMA_VELA_CERRADA = None
SEÑAL_PENDIENTE = None
YA_OPERO = {}
IQ1 = None
IQ2 = None

# Variables de entorno (Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL_1 = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD_1 = os.getenv("IQ_PASSWORD_1", "")
IQ_EMAIL_2 = os.getenv("IQ_EMAIL_2", "")
IQ_PASSWORD_2 = os.getenv("IQ_PASSWORD_2", "")

# --------------------------
# NOTIFICACIONES TELEGRAM
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Telegram sin configurar")
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML", disable_notification=False)
        logger.info(f"📤 TELEGRAM: {texto[:70]}...")
    except Exception as e:
        logger.error(f"❌ Error enviando a Telegram: {str(e)}")

# --------------------------
# OBTENER SALDO ACTUALIZADO
# --------------------------
def obtener_saldo_actualizado(iq):
    for _ in range(3):
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
# CONEXIÓN A CUENTAS
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
                iq.change_balance("PRACTICE")  # Cambia a "REAL" si usas cuenta real
                saldo = obtener_saldo_actualizado(iq)
                logger.info(f"✅ {nombre} CONECTADO | Saldo: ${saldo}")
                return iq, saldo
            else:
                logger.warning(f"⚠️ {nombre} falló: {motivo}")
        except Exception as e:
            logger.error(f"❌ {nombre} error: {str(e)}")
        time.sleep(ESPERA_REINTENTO)
    return None, 0.0

def conectar_ambas():
    global IQ1, IQ2
    enviar_telegram("🔄 CONECTANDO CUENTAS...")
    IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
    IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")

    while IQ1 is None or IQ2 is None:
        time.sleep(3)
        if IQ1 is None:
            IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
        if IQ2 is None:
            IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")

    mensaje = (
        f"✅ BOT ACTIVO Y LISTO\n"
        f"🔹 Cuenta 1: ${saldo1} | Entrada: seg {SEG_INICIO_CUENTA1}-{SEG_FIN_CUENTA1}\n"
        f"🔹 Cuenta 2: ${saldo2} | Entrada: seg {SEG_INICIO_CUENTA2}-{SEG_FIN_CUENTA2} con {REINTENTOS} reintentos\n"
        f"⏱️ Fuerza mínima: ≥ {FUERZA_MIN}%"
    )
    enviar_telegram(mensaje)
    logger.info(mensaje)
    return True

# --------------------------
# OBTENER DATOS DE VELAS
# --------------------------
def obtener_velas_cerradas(iq, activo):
    try:
        if not iq or not iq.check_connect():
            return None
        ts = int(time.time()) - 2
        velas = iq.get_candles(activo, VELA, 50, ts)
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
        resultado["razon"] = "Ya operó en esta vela"
        logger.info(f"ℹ️ {nombre} ya operó vela {vela_id}")
        return

    exito = False
    id_op = None
    saldo_final = 0

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)
            saldo = obtener_saldo_actualizado(iq)
            if saldo < MONTO:
                resultado["ok"] = False
                resultado["razon"] = f"Saldo insuficiente: ${saldo}"
                logger.warning(f"⚠️ {nombre}: {resultado['razon']}")
                return
            ok, id_op = iq.buy(MONTO, activo, direccion, EXPIRACION)
            if ok and id_op > 0:
                saldo_final = obtener_saldo_actualizado(iq)
                YA_OPERO[clave] = True
                exito = True
                logger.info(f"✅ {nombre} | {activo} | {direccion.upper()} | ID: {id_op} | Saldo: ${saldo_final}")
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ {nombre} Intento {intento+1}/{REINTENTOS}: {str(e)}")
            time.sleep(ESPERA)

    if exito:
        resultado.update({
            "ok": True,
            "direccion": direccion.upper(),
            "id": id_op,
            "saldo": saldo_final
        })
    else:
        resultado["ok"] = False
        resultado["razon"] = f"No se pudo ejecutar después de {REINTENTOS} intentos"
        logger.error(f"❌ {nombre}: {resultado['razon']}")

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA_CERRADA, SEÑAL_PENDIENTE, OPERACIONES_C1, OPERACIONES_C2
    logger.info("🔁 INICIANDO ANÁLISIS VELA A VELA...")

    while BOT_ACTIVO:
        try:
            if not IQ1 or not IQ2 or not IQ1.check_connect() or not IQ2.check_connect():
                logger.warning("⚠️ Conexión perdida, reconectando...")
                conectar_ambas()
                time.sleep(1)
                continue

            ts = IQ1.get_server_timestamp()
            segundos = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1

            if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
                s1 = obtener_saldo_actualizado(IQ1)
                s2 = obtener_saldo_actualizado(IQ2)
                mensaje_fin = (
                    f"✅ SESION FINALIZADA ✅\n"
                    f"🔹 Cuenta 1: {OPERACIONES_C1}/{MAX_OPER} | Saldo: ${s1}\n"
                    f"🔹 Cuenta 2: {OPERACIONES_C2}/{MAX_OPER} | Saldo: ${s2}"
                )
                enviar_telegram(mensaje_fin)
                logger.info(mensaje_fin)
                BOT_ACTIVO = False
                break

            # Analizar vela nueva
            if vela_cerrada != ULTIMA_VELA_CERRADA:
                ULTIMA_VELA_CERRADA = vela_cerrada
                SEÑAL_PENDIENTE = None
                YA_OPERO.clear()
                logger.info(f"🔍 ANALIZANDO VELA CERRADA: {vela_cerrada}")

                mejor_senal = None
                fuerza_max = 0

                for activo in ACTIVOS:
                    df = obtener_velas_cerradas(IQ1, activo)
                    if df is None or df.empty:
                        continue
                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, _ = senal
                        logger.info(f"ℹ️ {activo}: {dirr.upper()} | Fuerza: {fuerza}%")
                        if fuerza >= FUERZA_MIN and fuerza > fuerza_max:
                            fuerza_max = fuerza
                            mejor_senal = (activo, dirr, fuerza)

                if mejor_senal:
                    SEÑAL_PENDIENTE = mejor_senal
                    aviso_senal = (
                        f"📊 SEÑAL DETECTADA ✅\n"
                        f"📈 Activo: {mejor_senal[0]}\n"
                        f"➡️ Dirección: {mejor_senal[1].upper()}\n"
                        f"💪 Fuerza: {mejor_senal[2]}%\n"
                        f"⏱️ Entradas: C1 (1-3 seg) | C2 (3-6 seg con reintentos)"
                    )
                    enviar_telegram(aviso_senal)
                    logger.info(aviso_senal)
                else:
                    logger.info("ℹ️ Ningún activo cumple condiciones en esta vela")

            # 🟢 CUENTA 1: Entrada rápida
            if SEÑAL_PENDIENTE and SEG_INICIO_CUENTA1 <= segundos <= SEG_FIN_CUENTA1:
                activo, direccion, fuerza = SEÑAL_PENDIENTE
                res1 = {"ok": False}
                logger.info(f"⚡ CUENTA 1 | Entrada seg {segundos} | {activo}")
                ejecutar_orden(IQ1, "CUENTA 1", activo, direccion, vela_actual, res1)

                if res1["ok"]:
                    OPERACIONES_C1 += 1
                    enviar_telegram(
                        f"✅ CUENTA 1 EJECUTADA\n"
                        f"📌 {activo} | {direccion.upper()}\n"
                        f"🔹 ID: {res1['id']} | Saldo: ${res1['saldo']}\n"
                        f"📊 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                    )

            # 🟡 CUENTA 2: Espera y reintenta
            if SEÑAL_PENDIENTE and SEG_INICIO_CUENTA2 <= segundos <= SEG_FIN_CUENTA2:
                activo, direccion, fuerza = SEÑAL_PENDIENTE
                res2 = {"ok": False}
                logger.info(f"🔄 CUENTA 2 | Entrada seg {segundos} | {activo} | {REINTENTOS} intentos")
                enviar_telegram(f"🔄 CUENTA 2: Ejecutando con {REINTENTOS} reintentos...")

                ejecutar_orden(IQ2, "CUENTA 2", activo, direccion, vela_actual, res2)

                if res2["ok"]:
                    OPERACIONES_C2 += 1
                    enviar_telegram(
                        f"✅ CUENTA 2 EJECUTADA\n"
                        f"📌 {activo} | {direccion.upper()}\n"
                        f"🔹 ID: {res2['id']} | Saldo: ${res2['saldo']}\n"
                        f"📊 Progreso: {OPERACIONES_C2}/{MAX_OPER}"
                    )
                else:
                    enviar_telegram(f"❌ CUENTA 2: No se pudo entrar después de {REINTENTOS} intentos")

                SEÑAL_PENDIENTE = None

            time.sleep(0.2)

        except Exception as e:
            error_msg = f"💥 ERROR EN BUCLE: {str(e)}"
            logger.error(error_msg)
            enviar_telegram(f"⚠️ {error_msg}")
            time.sleep(2)

# --------------------------
# ARRANQUE
# --------------------------
if __name__ == "__main__":
    logger.info("🤖 BOT DE TRADING INICIANDO...")
    try:
        if TELEGRAM_TOKEN:
            Bot(token=TELEGRAM_TOKEN).delete_webhook(drop_pending_updates=True)
    except:
        pass

    if conectar_ambas():
        bucle_principal()
