import time
import os
import pandas as pd
import logging
import sys
import threading

try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("❌ Instalar: pip install git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git")
    sys.exit(1)

try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    print("❌ Instalar: pip install python-telegram-bot==13.15")
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

# PARÁMETROS
MONTO = 600
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 75
SEG_INICIO = 1
SEG_FIN = 6
REINTENTOS = 5
ESPERA_INTENTO = 0.15
MAX_OPER = 20

ACTIVOS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURJPY-OTC",
    "USDCHF-OTC", "AUDCAD-OTC"
]

# ✅ LEE VARIABLES DE RAILWAY
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL_1 = os.getenv("IQ_EMAIL_1", "")
IQ_PASS_1 = os.getenv("IQ_PASSWORD_1", "")
IQ_EMAIL_2 = os.getenv("IQ_EMAIL_2", "")
IQ_PASS_2 = os.getenv("IQ_PASSWORD_2", "")

# Variables globales
IQ1 = None
IQ2 = None
OPER_C1 = 0
OPER_C2 = 0
BOT_ACTIVO = True
ULTIMA_VELA = None
SEÑAL_ACTUAL = None
YA_OPERO = {}

# --------------------------
# NOTIFICACIONES
# --------------------------
def telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram no configurado")
        return
    try:
        Bot(token=TELEGRAM_TOKEN).send_message(
            chat_id=int(TELEGRAM_CHAT_ID),
            text=texto,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# --------------------------
# CONEXIÓN CUENTA
# --------------------------
def conectar(email, passw, nombre):
    if not email or not passw:
        logger.error(f"{nombre}: Faltan credenciales en variables")
        return None, 0.0
    for intento in range(5):
        try:
            iq = IQ_Option(email, passw)
            ok, msg = iq.connect()
            if ok:
                time.sleep(0.5)
                iq.change_balance("PRACTICE")  # ⚠️ Cambia a "REAL" si usas cuenta real
                saldo = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre} CONECTADO | Saldo: ${saldo}")
                return iq, saldo
            else:
                logger.warning(f"{nombre} fallo: {msg}")
        except Exception as e:
            logger.error(f"{nombre} error: {e}")
        time.sleep(2)
    return None, 0.0

def conectar_ambas():
    global IQ1, IQ2
    telegram("🔄 CONECTANDO AMBAS CUENTAS...")
    IQ1, saldo1 = conectar(IQ_EMAIL_1, IQ_PASS_1, "CUENTA 1")
    IQ2, saldo2 = conectar(IQ_EMAIL_2, IQ_PASS_2, "CUENTA 2")

    # Si alguna falla, intenta hasta conectar
    while IQ1 is None or IQ2 is None:
        time.sleep(3)
        if IQ1 is None: IQ1, saldo1 = conectar(IQ_EMAIL_1, IQ_PASS_1, "CUENTA 1")
        if IQ2 is None: IQ2, saldo2 = conectar(IQ_EMAIL_2, IQ_PASS_2, "CUENTA 2")

    telegram(
        f"✅ BOT LISTO\n"
        f"🔹 Cuenta 1: ${saldo1}\n"
        f"🔹 Cuenta 2: ${saldo2}\n"
        f"⏱️ Entrada: seg {SEG_INICIO}-{SEG_FIN}"
    )
    return True

# --------------------------
# EJECUTAR ORDEN (función para cada hilo)
# --------------------------
def orden_hilo(iq, nombre, activo, direccion, vela_id, resultado):
    clave = f"{nombre}_{vela_id}"
    if YA_OPERO.get(clave):
        resultado["ok"] = False
        resultado["msg"] = "Ya operó esta vela"
        return

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)
            saldo = round(iq.get_balance(), 2)
            if saldo < MONTO:
                resultado["ok"] = False
                resultado["msg"] = f"Saldo insuficiente ${saldo}"
                return
            ok, id_op = iq.buy(MONTO, activo, direccion, EXPIRACION)
            if ok and id_op > 0:
                saldo_final = round(iq.get_balance(), 2)
                YA_OPERO[clave] = True
                resultado["ok"] = True
                resultado["id"] = id_op
                resultado["saldo"] = saldo_final
                logger.info(f"✅ {nombre} | {activo} | ID {id_op}")
                return
            time.sleep(ESPERA_INTENTO)
        except Exception as e:
            logger.warning(f"{nombre} intento {intento+1}: {e}")
            time.sleep(ESPERA_INTENTO)

    resultado["ok"] = False
    resultado["msg"] = f"No ejecutado tras {REINTENTOS} intentos"

# --------------------------
# OBTENER VELAS
# --------------------------
def velas(iq, activo):
    try:
        if not iq or not iq.check_connect():
            return None
        datos = iq.get_candles(activo, VELA, 50, int(time.time())-2)
        if not datos or len(datos) < 30:
            return None
        df = pd.DataFrame(datos)
        df.rename(columns={"max":"high", "min":"low"}, inplace=True)
        df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
        return df
    except Exception as e:
        logger.warning(f"Error velas {activo}: {e}")
        return None

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle():
    global BOT_ACTIVO, ULTIMA_VELA, SEÑAL_ACTUAL, OPER_C1, OPER_C2
    logger.info("🚀 BUCLE ACTIVO")

    while BOT_ACTIVO:
        try:
            # Comprobar conexión cada ciclo
            if not IQ1 or not IQ2 or not IQ1.check_connect() or not IQ2.check_connect():
                logger.warning("⚠️ Reconexión necesaria")
                conectar_ambas()
                continue

            ts = IQ1.get_server_timestamp()
            seg = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1

            # Detener al llegar al límite
            if OPER_C1 >= MAX_OPER or OPER_C2 >= MAX_OPER:
                s1 = round(IQ1.get_balance(),2)
                s2 = round(IQ2.get_balance(),2)
                telegram(f"✅ FIN SESION\n1: {OPER_C1}/{MAX_OPER} | ${s1}\n2: {OPER_C2}/{MAX_OPER} | ${s2}")
                BOT_ACTIVO = False
                break

            # Analizar vela nueva
            if vela_cerrada != ULTIMA_VELA:
                ULTIMA_VELA = vela_cerrada
                SEÑAL_ACTUAL = None
                YA_OPERO.clear()
                logger.info(f"🔍 Analizando vela {vela_cerrada}")

                mejor = None
                fuerza_max = 0
                for activo in ACTIVOS:
                    df = velas(IQ1, activo)
                    if df is None or df.empty:
                        continue
                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, _ = senal
                        if fuerza >= FUERZA_MIN and fuerza > fuerza_max:
                            fuerza_max = fuerza
                            mejor = (activo, dirr, fuerza)

                if mejor:
                    SEÑAL_ACTUAL = mejor
                    telegram(f"📊 SEÑAL\n{mejor[0]} | {mejor[1].upper()} | {mejor[2]}%")

            # ✅ EJECUCIÓN EN PARALELO REAL
            if SEÑAL_ACTUAL and SEG_INICIO <= seg <= SEG_FIN:
                activo, direccion, fuerza = SEÑAL_ACTUAL
                telegram(f"⚡ EJECUTANDO EN AMBAS: {activo} | {direccion.upper()}")

                res1 = {"ok": False, "msg": ""}
                res2 = {"ok": False, "msg": ""}

                # Crear hilos separados para cada cuenta
                hilo1 = threading.Thread(target=orden_hilo, args=(IQ1, "CUENTA 1", activo, direccion, vela_actual, res1))
                hilo2 = threading.Thread(target=orden_hilo, args=(IQ2, "CUENTA 2", activo, direccion, vela_actual, res2))

                # Iniciar los dos al mismo tiempo
                hilo1.start()
                hilo2.start()

                # Esperar a que terminen ambos
                hilo1.join(timeout=3)
                hilo2.join(timeout=3)

                # Contar operaciones
                if res1["ok"]: OPER_C1 += 1
                if res2["ok"]: OPER_C2 += 1

                # Resumen final
                msg = (
                    f"✅ OPERACIÓN FINALIZADA\n"
                    f"📌 {activo} | {direccion.upper()}\n"
                    f"🔹 Cuenta 1: {'✅ ID '+str(res1['id'])+' $'+str(res1['saldo']) if res1['ok'] else '❌ '+res1['msg']}\n"
                    f"🔹 Cuenta 2: {'✅ ID '+str(res2['id'])+' $'+str(res2['saldo']) if res2['ok'] else '❌ '+res2['msg']}\n"
                    f"📊 Progreso: {OPER_C1}/{MAX_OPER}"
                )
                telegram(msg)
                logger.info(msg)

                SEÑAL_ACTUAL = None

            time.sleep(0.2)

        except Exception as e:
            logger.error(f"💥 Error bucle: {e}")
            telegram(f"⚠️ Error: {e}")
            time.sleep(2)

# --------------------------
# ARRANQUE
# --------------------------
if __name__ == "__main__":
    logger.info("🤖 BOT INICIANDO...")
    if conectar_ambas():
        bucle()
