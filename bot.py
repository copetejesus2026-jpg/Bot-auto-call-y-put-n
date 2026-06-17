import time
import os
import pandas as pd
import logging
import sys

try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("❌ Instala dependencias: pip install git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git")
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

# Parámetros ajustados para que sí encuentre señales
MONTO = 1200
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 80          # Bajamos un poco para que sí detecte
SEG_INICIO = 0
SEG_FIN = 9              # Ventana completa
REINTENTOS = 8
ESPERA_INTENTO = 0.03
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
# NOTIFICACIONES
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Sin credenciales de Telegram")
        return
    try:
        Bot(token=TELEGRAM_TOKEN).send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# --------------------------
# CONEXIÓN
# --------------------------
def conectar_cuenta():
    if not IQ_EMAIL or not IQ_PASSWORD:
        logger.error("❌ Faltan correo o contraseña de IQ Option")
        return None, 0.0
    for intento in range(10):
        try:
            iq = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
            ok, motivo = iq.connect()
            if ok:
                time.sleep(0.5)
                iq.change_balance("PRACTICE")  # Cambia a "REAL" si usas dinero real
                saldo = round(iq.get_balance(), 2)
                logger.info(f"✅ Conectado correctamente | Saldo: ${saldo}")
                enviar_telegram(f"✅ BOT INICIADO\n💵 Saldo: ${saldo}\n⚙️ Fuerza mínima: {FUERZA_MIN}%")
                return iq, saldo
            else:
                logger.warning(f"Intento {intento+1} falló: {motivo}")
        except Exception as e:
            logger.error(f"Error conexión: {e}")
        time.sleep(1)
    return None, 0.0

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(activo, direccion, vela_id):
    clave = f"orden_{vela_id}"
    if YA_EJECUTADO.get(clave):
        return False, None, 0.0

    for intento in range(REINTENTOS):
        try:
            if not IQ.check_connect():
                logger.info("🔄 Reconectando para ejecutar...")
                IQ.connect()
                time.sleep(0.05)

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

            logger.info(f"⏳ Reintento {intento+1}...")
            time.sleep(ESPERA_INTENTO)
        except Exception as e:
            logger.warning(f"Error en orden: {e}")
            time.sleep(ESPERA_INTENTO)

    enviar_telegram(f"❌ No se pudo ejecutar en {activo}")
    return False, None, 0.0

# --------------------------
# OBTENER VELAS
# --------------------------
def obtener_velas(activo):
    try:
        ts = int(time.time()) - 2
        velas = IQ.get_candles(activo, VELA, 50, ts)
        if not velas or len(velas) < 40:
            logger.info(f"⚠️ Pocos datos para {activo}")
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df
    except Exception as e:
        logger.warning(f"Error obteniendo velas {activo}: {e}")
        return None

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES, IQ, mejor_senal
    logger.info("🚀 BOT INICIADO - Analizando activos...")

    while BOT_ACTIVO:
        try:
            if not IQ or not IQ.check_connect():
                logger.warning("⚠️ Sin conexión, reconectando...")
                IQ, _ = conectar_cuenta()
                time.sleep(1)
                continue

            ts = IQ.get_server_timestamp()
            segundos = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1

            if OPERACIONES >= MAX_OPER:
                saldo_final = round(IQ.get_balance(), 2)
                enviar_telegram(f"✅ SESIÓN FINALIZADA\n📊 Operaciones: {OPERACIONES}\n💵 Saldo final: ${saldo_final}")
                BOT_ACTIVO = False
                break

            # Analizar solo cuando cierra la vela
            if vela_cerrada != ULTIMA_VELA:
                ULTIMA_VELA = vela_cerrada
                YA_EJECUTADO.clear()
                mejor_fuerza = 0
                mejor_senal = None
                logger.info(f"🔍 Analizando vela {vela_cerrada}...")

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

            # Ejecutar dentro de la ventana
            if mejor_senal and SEG_INICIO <= segundos <= SEG_FIN:
                activo, direccion, fuerza, tipo = mejor_senal
                logger.info(f"⚡ Ejecutando {activo} a los {segundos} seg")
                ok, id_op, saldo = ejecutar_orden(activo, direccion, vela_actual)

                if ok:
                    OPERACIONES += 1
                    enviar_telegram(
                        f"✅ OPERACIÓN REALIZADA\n"
                        f"📌 {activo} | {direccion.upper()}\n"
                        f"🆔 ID: {id_op}\n"
                        f"💵 Saldo: ${saldo}\n"
                        f"📈 Progreso: {OPERACIONES}/{MAX_OPER}"
                    )

                mejor_senal = None

            time.sleep(0.1)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {e}")
            enviar_telegram(f"⚠️ Error: {str(e)}")
            time.sleep(2)

# --------------------------
# ARRANQUE
# --------------------------
if __name__ == "__main__":
    logger.info("🤖 INICIANDO BOT DE TRADING")
    IQ, _ = conectar_cuenta()
    if IQ:
        bucle_principal()
    else:
        logger.critical("❌ No se pudo conectar a la cuenta")
