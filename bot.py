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
    print("❌ Ejecuta: pip install git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git@v5.8.0")
    sys.exit(1)

try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    print("❌ Ejecuta: pip install python-telegram-bot==13.15")
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

MONTO = 600
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 80
SEG_INICIO = 0
SEG_FIN = 9
REINTENTOS = 10
ESPERA_INTENTO = 0.2
MAX_OPER = 20

ACTIVOS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURJPY-OTC",
    "USDCHF-OTC", "AUDUSD-OTC", "GBPJPY-OTC"
]

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD = os.getenv("IQ_PASSWORD_1", "")

IQ = None
OPERACIONES = 0
BOT_ACTIVO = True
ULTIMA_VELA = None
YA_EJECUTADO = {}
mejor_senal = None

# --------------------------
# TELEGRAM
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        Bot(token=TELEGRAM_TOKEN).send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Telegram: {e}")

# --------------------------
# CONEXIÓN FORZADA Y LIMPIA
# --------------------------
def conectar_nuevo():
    global IQ
    if not IQ_EMAIL or not IQ_PASSWORD:
        logger.error("❌ Faltan credenciales")
        return None, 0.0

    # Eliminamos sesión anterior completamente
    try:
        if IQ:
            del IQ
            time.sleep(1)
    except:
        pass

    IQ = None
    for intento in range(10):
        try:
            logger.info(f"🔄 Conexión intento {intento+1}")
            IQ = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
            IQ.connect()
            time.sleep(2)

            if IQ.check_connect():
                IQ.change_balance("PRACTICE")  # ⚠️ Cambia a "REAL" si usas dinero real
                saldo = round(IQ.get_balance(), 2)
                logger.info(f"✅ Conectado | Saldo: ${saldo}")
                enviar_telegram(f"✅ BOT CONECTADO\n💵 Saldo: ${saldo}")
                return IQ, saldo
            else:
                logger.warning(f"Intento {intento+1} falló")
        except Exception as e:
            logger.error(f"Error conexión: {e}")
            IQ = None
        time.sleep(2)

    return None, 0.0

def verificar_y_reconectar():
    global IQ
    try:
        if not IQ or not IQ.check_connect():
            logger.warning("🔁 Reiniciando sesión completa")
            IQ, _ = conectar_nuevo()
            time.sleep(1.5)
        return IQ and IQ.check_connect()
    except:
        IQ, _ = conectar_nuevo()
        return IQ and IQ.check_connect()

# --------------------------
# OBTENER VELAS SIN ERROR
# --------------------------
def obtener_velas(activo):
    """Método reforzado para evitar error get_candles"""
    for intento in range(6):
        if not verificar_y_reconectar():
            time.sleep(1)
            continue
        try:
            # Espera extra para estabilizar la conexión
            time.sleep(0.4)
            ts = int(time.time())
            # Solicitamos menos velas para reducir carga
            velas = IQ.get_candles(activo, VELA, 50, ts)

            if velas and len(velas) >= 40:
                df = pd.DataFrame(velas)
                df.rename(columns={"max": "high", "min": "low"}, inplace=True)
                df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
                return df

            logger.info(f"⚠️ Datos insuficientes {activo}")
        except Exception as e:
            logger.warning(f"Error velas {activo}: {str(e)}")
            verificar_y_reconectar()
        time.sleep(0.6)

    return None

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(activo, direccion, vela_id):
    clave = f"orden_{vela_id}"
    if YA_EJECUTADO.get(clave):
        return False, None, 0.0

    for intento in range(REINTENTOS):
        if not verificar_y_reconectar():
            time.sleep(0.5)
            continue
        try:
            saldo = round(IQ.get_balance(), 2)
            if saldo < MONTO:
                enviar_telegram(f"❌ Saldo insuficiente: ${saldo}")
                return False, None, saldo

            ok, id_op = IQ.buy(MONTO, activo, direccion, EXPIRACION)
            time.sleep(0.2)

            if ok and id_op > 0:
                saldo_final = round(IQ.get_balance(), 2)
                YA_EJECUTADO[clave] = True
                logger.info(f"✅ Orden ejecutada | {activo} | {direccion} | ID: {id_op}")
                return True, id_op, saldo_final
        except Exception as e:
            logger.warning(f"Error orden: {str(e)}")
            verificar_y_reconectar()
        time.sleep(ESPERA_INTENTO)

    enviar_telegram(f"❌ No se pudo operar en {activo}")
    return False, None, 0.0

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES, IQ, mejor_senal
    logger.info("🚀 BOT INICIADO - Versión estable para Railway")
    enviar_telegram("🤖 BOT LISTO PARA OPERAR")

    while BOT_ACTIVO:
        try:
            if not verificar_y_reconectar():
                time.sleep(2)
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

            if vela_cerrada != ULTIMA_VELA:
                ULTIMA_VELA = vela_cerrada
                YA_EJECUTADO.clear()
                mejor_fuerza = 0
                mejor_senal = None
                logger.info(f"🔍 Analizando vela {vela_cerrada}")

                for activo in ACTIVOS:
                    df = obtener_velas(activo)
                    if df is None:
                        continue
                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, tipo = senal
                        logger.info(f"📈 {activo} → {dirr.upper()} | {fuerza}%")
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
                    logger.info("ℹ️ Sin señales válidas")

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

            time.sleep(0.3)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {str(e)}")
            enviar_telegram(f"⚠️ Error: {str(e)}")
            verificar_y_reconectar()
            time.sleep(3)

# --------------------------
# ARRANQUE
# --------------------------
if __name__ == "__main__":
    IQ, _ = conectar_nuevo()
    if IQ:
        bucle_principal()
    else:
        logger.critical("❌ No se pudo iniciar el bot")
