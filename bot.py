import time
import os
import pandas as pd
import logging
import sys

# --------------------------
# DEPENDENCIAS
# --------------------------
try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("❌ Instala: pip install git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git")
    sys.exit(1)

try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    print("❌ Instala: pip install python-telegram-bot==13.15")
    sys.exit(1)

from strategy import get_reversal_signal

# --------------------------
# CONFIGURACIÓN
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Parámetros
MONTO = 600
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 80
SEG_INICIO = 0
SEG_FIN = 9
REINTENTOS = 10
ESPERA_INTENTO = 0.1
MAX_OPER = 20

ACTIVOS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURJPY-OTC",
    "USDCHF-OTC", "AUDUSD-OTC", "GBPJPY-OTC"
]

# Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD = os.getenv("IQ_PASSWORD_1", "")

# Variables globales
IQ = None
OPERACIONES = 0
BOT_ACTIVO = True
ULTIMA_VELA = None
YA_EJECUTADO = {}
mejor_senal = None

# --------------------------
# NOTIFICACIONES TELEGRAM
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        Bot(token=TELEGRAM_TOKEN).send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# --------------------------
# CONEXIÓN COMPLETA Y SEGURA
# --------------------------
def conectar_nueva_cuenta():
    """Crea una sesión totalmente nueva cada vez"""
    if not IQ_EMAIL or not IQ_PASSWORD:
        logger.error("❌ Faltan credenciales de acceso")
        return None, 0.0

    for intento in range(10):
        try:
            logger.info(f"🔄 Conectando nuevo intento {intento+1}/10")
            iq = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
            iq.connect()
            time.sleep(1.5)

            if iq.check_connect():
                iq.change_balance("PRACTICE")  # ⚠️ Cambia a "REAL" si usas dinero real
                saldo = round(iq.get_balance(), 2)
                logger.info(f"✅ Conectado exitosamente | Saldo: ${saldo}")
                enviar_telegram(f"✅ BOT CONECTADO\n💵 Saldo: ${saldo}")
                return iq, saldo
            else:
                logger.warning(f"Intento {intento+1} falló, reintentando...")
        except Exception as e:
            logger.error(f"Error en conexión: {str(e)}")
        time.sleep(2)

    logger.critical("❌ No se pudo conectar después de 10 intentos")
    enviar_telegram("❌ ERROR: No se pudo conectar a IQ Option")
    return None, 0.0

def reiniciar_sesion():
    """Cierra la sesión anterior y crea una nueva"""
    global IQ
    logger.warning("🔁 Reiniciando sesión completa...")
    try:
        if IQ is not None:
            del IQ
            time.sleep(1)
    except:
        pass
    IQ, _ = conectar_nueva_cuenta()
    return IQ is not None and IQ.check_connect()

# --------------------------
# OBTENER VELAS SIN ERRORES
# --------------------------
def obtener_velas(activo):
    """Obtiene velas con manejo de errores y reconexión"""
    global IQ
    for intento in range(5):
        try:
            if IQ is None or not IQ.check_connect():
                reiniciar_sesion()
                time.sleep(1)
                continue

            ts = int(time.time())
            velas = IQ.get_candles(activo, VELA, 60, ts)

            if velas and len(velas) >= 40:
                df = pd.DataFrame(velas)
                df.rename(columns={"max": "high", "min": "low"}, inplace=True)
                df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
                return df

            logger.info(f"⚠️ Pocos datos para {activo}, reintentando...")
        except Exception as e:
            logger.warning(f"Error al obtener velas de {activo}: {str(e)}")
            reiniciar_sesion()
        time.sleep(0.8)

    logger.error(f"❌ No se pudieron obtener velas de {activo} después de 5 intentos")
    return None

# --------------------------
# EJECUTAR OPERACIÓN
# --------------------------
def ejecutar_orden(activo, direccion, vela_id):
    clave = f"orden_{vela_id}"
    if YA_EJECUTADO.get(clave):
        return False, None, 0.0

    for intento in range(REINTENTOS):
        try:
            if IQ is None or not IQ.check_connect():
                reiniciar_sesion()
                time.sleep(1)
                continue

            saldo = round(IQ.get_balance(), 2)
            if saldo < MONTO:
                enviar_telegram(f"❌ Saldo insuficiente: ${saldo}")
                return False, None, saldo

            ok, id_op = IQ.buy(MONTO, activo, direccion, EXPIRACION)

            if ok and id_op > 0:
                saldo_final = round(IQ.get_balance(), 2)
                YA_EJECUTADO[clave] = True
                logger.info(f"✅ Orden ejecutada | {activo} | {direccion} | ID: {id_op}")
                return True, id_op, saldo_final

            logger.info(f"⏳ Reintento {intento+1} de ejecución...")
        except Exception as e:
            logger.warning(f"Error ejecutando orden: {str(e)}")
            reiniciar_sesion()
        time.sleep(ESPERA_INTENTO)

    enviar_telegram(f"❌ No se pudo ejecutar orden en {activo}")
    return False, None, 0.0

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES, IQ, mejor_senal
    logger.info("🚀 BOT INICIADO - Versión estable sin errores de conexión")
    enviar_telegram("🤖 BOT INICIADO CORRECTAMENTE")

    while BOT_ACTIVO:
        try:
            if IQ is None or not IQ.check_connect():
                reiniciar_sesion()
                time.sleep(1)
                continue

            ts = IQ.get_server_timestamp()
            segundos = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1

            if OPERACIONES >= MAX_OPER:
                saldo_final = round(IQ.get_balance(), 2)
                enviar_telegram(
                    f"✅ SESIÓN FINALIZADA\n"
                    f"📊 Operaciones: {OPERACIONES}/{MAX_OPER}\n"
                    f"💵 Saldo final: ${saldo_final}"
                )
                BOT_ACTIVO = False
                break

            # Analizar cuando cierra la vela
            if vela_cerrada != ULTIMA_VELA:
                ULTIMA_VELA = vela_cerrada
                YA_EJECUTADO.clear()
                mejor_fuerza = 0
                mejor_senal = None
                logger.info(f"🔍 Analizando vela cerrada: {vela_cerrada}")

                for activo in ACTIVOS:
                    df = obtener_velas(activo)
                    if df is None:
                        continue

                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, tipo = senal
                        logger.info(f"📈 {activo} → {dirr.upper()} | Fuerza: {fuerza}%")
                        if fuerza >= FUERZA_MIN and fuerza > mejor_fuerza:
                            mejor_fuerza = fuerza
                            mejor_senal = (activo, dirr, fuerza, tipo)

                if mejor_senal:
                    activo, direccion, fuerza, tipo = mejor_senal
                    enviar_telegram(
                        f"📊 SEÑAL DETECTADA\n"
                        f"📌 Activo: {activo}\n"
                        f"➡️ Dirección: {direccion.upper()}\n"
                        f"💪 Fuerza: {fuerza}%\n"
                        f"🔍 Tipo: {tipo}"
                    )
                else:
                    logger.info("ℹ️ No hay señales válidas en esta vela")

            # Ejecutar en la ventana de tiempo
            if mejor_senal and SEG_INICIO <= segundos <= SEG_FIN:
                activo, direccion, fuerza, tipo = mejor_senal
                ok, id_op, saldo = ejecutar_orden(activo, direccion, vela_actual)

                if ok:
                    OPERACIONES += 1
                    enviar_telegram(
                        f"✅ OPERACIÓN EJECUTADA\n"
                        f"📌 {activo} | {direccion.upper()}\n"
                        f"🆔 ID: {id_op}\n"
                        f"💵 Saldo: ${saldo}\n"
                        f"📊 Progreso: {OPERACIONES}/{MAX_OPER}"
                    )

                mejor_senal = None

            time.sleep(0.2)

        except Exception as e:
            logger.error(f"💥 Error en bucle principal: {str(e)}")
            enviar_telegram(f"⚠️ Error: {str(e)}")
            reiniciar_sesion()
            time.sleep(3)

# --------------------------
# ARRANQUE
# --------------------------
if __name__ == "__main__":
    IQ, _ = conectar_nueva_cuenta()
    if IQ:
        bucle_principal()
    else:
        logger.critical("❌ No se pudo iniciar el bot")
