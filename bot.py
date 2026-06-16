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

# Rango exacto de entrada
SEG_INICIO_ENTRADA = 1
SEG_FIN_ENTRADA = 6

REINTENTOS = 5
ESPERA = 0.08
REINTENTOS_POR_CUENTA = 8
ESPERA_REINTENTO = 2

# Lista de activos a analizar
ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

MAX_OPER = 20              # Límite de operaciones por sesión
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
# FUNCIÓN DE NOTIFICACIONES TELEGRAM
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
    enviar_telegram("🔄 CONECTANDO AMBAS CUENTAS...")
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
        f"🔹 Cuenta 1: ${saldo1}\n"
        f"🔹 Cuenta 2: ${saldo2}\n"
        f"⏱️ Analiza velas cerradas | Entrada: segundo 1-6"
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
# EJECUTAR ORDEN EN UNA CUENTA
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
            logger.warning(f"⚠️ {nombre} Intento {intento+1}: {str(e)}")
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
        resultado["razon"] = "No se pudo ejecutar"
        logger.error(f"❌ {nombre}: {resultado['razon']}")

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA_CERRADA, SEÑAL_PENDIENTE, OPERACIONES_C1, OPERACIONES_C2
    logger.info("🔁 INICIANDO ANÁLISIS VELA A VELA...")

    while BOT_ACTIVO:
        try:
            # Verificar conexión
            if not IQ1 or not IQ2 or not IQ1.check_connect() or not IQ2.check_connect():
                logger.warning("⚠️ Conexión perdida, reconectando...")
                conectar_ambas()
                time.sleep(1)
                continue

            ts = IQ1.get_server_timestamp()
            segundos = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1

            # Detener si se alcanza el límite
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

            # Analizar cada vela nueva cerrada
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
                        f"⏱️ Entrada programada: segundo {SEG_INICIO_ENTRADA} a {SEG_FIN_ENTRADA}"
                    )
                    enviar_telegram(aviso_senal)
                    logger.info(aviso_senal)
                else:
                    logger.info("ℹ️ Ningún activo cumple condiciones en esta vela")

            # Ejecutar operación en AMBAS CUENTAS al mismo tiempo
            if SEÑAL_PENDIENTE and SEG_INICIO_ENTRADA <= segundos <= SEG_FIN_ENTRADA:
                activo, direccion, fuerza = SEÑAL_PENDIENTE
                logger.info(f"⚡ EJECUTANDO ENTRADA | Segundo {segundos} | {activo} | {direccion.upper()}")
                enviar_telegram(f"⚡ EJECUTANDO: {activo} | {direccion.upper()}")

                # Variables para guardar resultado de cada cuenta
                res1 = {"ok": False}
                res2 = {"ok": False}

                # Ejecutar en ambas cuentas al mismo tiempo
                ejecutar_orden(IQ1, "CUENTA 1", activo, direccion, vela_actual, res1)
                ejecutar_orden(IQ2, "CUENTA 2", activo, direccion, vela_actual, res2)

                # Contar operaciones exitosas
                if res1["ok"]:
                    OPERACIONES_C1 += 1
                if res2["ok"]:
                    OPERACIONES_C2 += 1

                # Enviar resumen completo
                aviso_operacion = (
                    f"✅ OPERACIÓN EJECUTADA ✅\n"
                    f"📌 Activo: {activo}\n"
                    f"➡️ Dirección: {direccion.upper()}\n"
                    f"🔹 Cuenta 1: {'✅ ID ' + str(res1['id']) + ' | Saldo: $' + str(res1['saldo']) if res1['ok'] else '❌ No ejecutada'}\n"
                    f"🔹 Cuenta 2: {'✅ ID ' + str(res2['id']) + ' | Saldo: $' + str(res2['saldo']) if res2['ok'] else '❌ No ejecutada'}\n"
                    f"📊 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                )
                enviar_telegram(aviso_operacion)
                logger.info(aviso_operacion)

                SEÑAL_PENDIENTE = None

            time.sleep(0.3)

        except Exception as e:
            error_msg = f"💥 ERROR EN BUCLE: {str(e)}"
            logger.error(error_msg)
            enviar_telegram(f"⚠️ {error_msg}")
            time.sleep(2)

# --------------------------
# ARRANQUE DEL BOT
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
