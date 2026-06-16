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
# CONFIGURACIÓN GENERAL
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Parámetros de operación
MONTO = 600
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 98
REINTENTOS = 5
ESPERA = 0.2
MAX_DESFASE_PERMITIDO = 1

# Tiempos sincronizados
SEG_CONEXION = 53
SEG_DETECCION = 54
SEG_INICIO = 56
SEG_FIN = 59

# ✅ ACTIVOS CORREGIDOS CON SUFIJO -OTC
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
ULTIMA_VELA = None
YA_OPERO = {}
CUENTA_ANALISIS = 1
IQ1 = None
IQ2 = None

# Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL_1 = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD_1 = os.getenv("IQ_PASSWORD_1", "")
IQ_EMAIL_2 = os.getenv("IQ_EMAIL_2", "")
IQ_PASSWORD_2 = os.getenv("IQ_PASSWORD_2", "")

# --------------------------
# TELEGRAM - CORREGIDO
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Faltan credenciales de Telegram")
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
        logger.info(f"📤 Mensaje enviado a Telegram: {texto[:60]}...")
    except Exception as e:
        logger.error(f"❌ Error enviando a Telegram: {str(e)}")

def escuchar_comandos():
    global BOT_ACTIVO, OPERACIONES_C1, OPERACIONES_C2, YA_OPERO, ULTIMA_VELA
    OFFSET = 0
    logger.info("🤖 Bot iniciado, esperando comandos...")
    enviar_telegram("🤖 Bot listo ✅\nUsa /start → Conectar cuentas y operar\nUsa /stop → Detener y desconectar")

    while True:
        try:
            if not TELEGRAM_TOKEN:
                time.sleep(5)
                continue
            bot = Bot(token=TELEGRAM_TOKEN)
            updates = bot.get_updates(offset=OFFSET, timeout=10)
            for upd in updates:
                OFFSET = upd.update_id + 1
                if not upd.message or str(upd.message.chat_id) != str(TELEGRAM_CHAT_ID):
                    continue
                comando = upd.message.text.strip().lower()

                if comando == "/start":
                    if not BOT_ACTIVO:
                        OPERACIONES_C1 = 0
                        OPERACIONES_C2 = 0
                        YA_OPERO.clear()
                        ULTIMA_VELA = None
                        if conectar_ambas_cuentas():
                            BOT_ACTIVO = True
                            Thread(target=bucle_principal, daemon=True).start()
                    else:
                        enviar_telegram("ℹ️ El bot ya está activo")

                elif comando == "/stop":
                    if BOT_ACTIVO:
                        BOT_ACTIVO = False
                        desconectar_ambas_cuentas()
                    else:
                        enviar_telegram("ℹ️ El bot ya está detenido")

        except Exception as e:
            logger.error(f"⚠️ Comandos: {str(e)}")
            time.sleep(3)

# --------------------------
# CONEXIÓN DE CUENTAS
# --------------------------
def conectar_cuenta(email, contraseña, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        if not email or not contraseña:
            logger.error(f"❌ {nombre}: Faltan credenciales")
            return None, 0

        iq = IQ_Option(email, contraseña)
        conectado, motivo = iq.connect()
        if conectado:
            time.sleep(0.5)
            iq.change_balance("PRACTICE")
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} conectado | Saldo: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} falló: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error en {nombre}: {str(e)}")
        return None, 0

def conectar_ambas_cuentas():
    global IQ1, IQ2
    enviar_telegram("🔄 CONECTANDO AMBAS CUENTAS...")
    res1 = {}
    res2 = {}

    hilo1 = Thread(target=lambda: res1.update(dict(zip(["iq", "saldo"], conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")))))
    hilo2 = Thread(target=lambda: res2.update(dict(zip(["iq", "saldo"], conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")))))

    hilo1.start()
    hilo2.start()
    hilo1.join()
    hilo2.join()

    IQ1 = res1.get("iq")
    IQ2 = res2.get("iq")
    saldo1 = res1.get("saldo", 0)
    saldo2 = res2.get("saldo", 0)

    if IQ1 and IQ2:
        enviar_telegram(
            f"✅ CONEXIÓN EXITOSA\n"
            f"🔹 Cuenta 1: ${saldo1}\n"
            f"🔹 Cuenta 2: ${saldo2}\n"
            f"🚀 Analizando señales..."
        )
        return True
    else:
        enviar_telegram("❌ No se pudo conectar una o ambas cuentas")
        return False

def desconectar_ambas_cuentas():
    global IQ1, IQ2
    if IQ1:
        try:
            IQ1.disconnect()
        except:
            pass
    if IQ2:
        try:
            IQ2.disconnect()
        except:
            pass
    IQ1 = None
    IQ2 = None
    enviar_telegram("⏹️ Cuentas desconectadas. Bot detenido.")

# --------------------------
# DATOS DE MERCADO
# --------------------------
def obtener_velas(iq, activo):
    try:
        if not iq or not iq.check_connect():
            return None
        velas = iq.get_candles(activo, VELA, 50, time.time())
        if not velas or len(velas) < 30:
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df
    except Exception as e:
        logger.warning(f"⚠️ Velas {activo}: {str(e)}")
        return None

# --------------------------
# EJECUTAR ORDEN - CORREGIDO PARA MISMA DIRECCIÓN EN AMBAS
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela, resultado):
    clave = f"{nombre}_{vela}"
    if YA_OPERO.get(clave, False):
        resultado["ok"] = False
        resultado["razon"] = "Ya operó en esta vela"
        return

    # ✅ MISMA DIRECCIÓN EN AMBAS CUENTAS
    dir_final = direccion

    exito = False
    saldo = None
    id_op = None

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)
            ok, id_op = iq.buy(MONTO, activo, dir_final, EXPIRACION)
            if ok and id_op > 0:
                time.sleep(0.3)
                saldo = round(iq.get_balance(), 2)
                exito = True
                logger.info(f"✅ {nombre} ejecutado | ID: {id_op} | Dirección: {dir_final.upper()}")
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ Intento {intento+1} en {nombre}: {str(e)}")
            time.sleep(ESPERA)

    if exito:
        YA_OPERO[clave] = True
        resultado.update({
            "ok": True,
            "direccion": dir_final.upper(),
            "id": id_op,
            "saldo": saldo
        })
    else:
        resultado["ok"] = False
        resultado["razon"] = "No se pudo ejecutar"

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES_C1, OPERACIONES_C2, CUENTA_ANALISIS, IQ1, IQ2

    while BOT_ACTIVO:
        try:
            if not IQ1 or not IQ2:
                enviar_telegram("❌ Conexión perdida. Deteniendo...")
                BOT_ACTIVO = False
                break

            ts_servidor = IQ1.get_server_timestamp()
            segundos = ts_servidor % 60
            vela_actual = ts_servidor // 60

            if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
                enviar_telegram(
                    f"✅ PROCESO FINALIZADO ✅\n"
                    f"🔹 Cuenta 1: {OPERACIONES_C1}/{MAX_OPER}\n"
                    f"🔹 Cuenta 2: {OPERACIONES_C2}/{MAX_OPER}"
                )
                BOT_ACTIVO = False
                desconectar_ambas_cuentas()
                break

            if vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                YA_OPERO.clear()
                logger.info(f"🔄 Nueva vela iniciada: {vela_actual}")

            senal = None
            if segundos == SEG_DETECCION:
                mejor_senal = None
                fuerza_maxima = 0
                iq_analisis = IQ1 if CUENTA_ANALISIS == 1 else IQ2
                CUENTA_ANALISIS = 2 if CUENTA_ANALISIS == 1 else 1

                for activo in ACTIVOS:
                    df = obtener_velas(iq_analisis, activo)
                    if df is None:
                        continue
                    resultado_senal = get_reversal_signal(df)
                    if resultado_senal:
                        dir_ori, fuerza, _ = resultado_senal
                        if fuerza >= FUERZA_MIN and fuerza > fuerza_maxima:
                            fuerza_maxima = fuerza
                            mejor_senal = (activo, dir_ori, fuerza)

                if mejor_senal:
                    activo, dir_ori, fuerza = mejor_senal
                    senal = (activo, dir_ori, fuerza)
                    enviar_telegram(f"🔔 SEÑAL DETECTADA\n📈 Activo: {activo}\n➡️ Dirección: {dir_ori.upper()}\n💪 Fuerza: {fuerza}%")

            if senal and SEG_INICIO <= segundos <= SEG_FIN:
                activo, dir_ori, fuerza = senal
                res1 = {"ok": False}
                res2 = {"ok": False}

                enviar_telegram(f"⚡ ENVIANDO ORDEN A AMBAS CUENTAS...")

                hilo_op1 = Thread(target=ejecutar_orden, args=(IQ1, "CUENTA 1", activo, dir_ori, vela_actual, res1))
                hilo_op2 = Thread(target=ejecutar_orden, args=(IQ2, "CUENTA 2", activo, dir_ori, vela_actual, res2))
                hilo_op1.start()
                hilo_op2.start()
                hilo_op1.join()
                hilo_op2.join()

                if res1["ok"] and res2["ok"]:
                    OPERACIONES_C1 += 1
                    OPERACIONES_C2 += 1
                    enviar_telegram(
                        f"✅ OPERACIÓN EJECUTADA EN AMBAS CUENTAS\n"
                        f"🔹 Cuenta 1: {res1['direccion']} | ID: {res1['id']} | Saldo: ${res1['saldo']}\n"
                        f"🔹 Cuenta 2: {res2['direccion']} | ID: {res2['id']} | Saldo: ${res2['saldo']}\n"
                        f"📊 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                    )
                else:
                    motivo = ""
                    if not res1["ok"]: motivo += "Cuenta 1: " + res1.get("razon", "Error") + " | "
                    if not res2["ok"]: motivo += "Cuenta 2: " + res2.get("razon", "Error")
                    enviar_telegram(f"❌ OPERACIÓN FALLIDA\n{motivo}")

                senal = None

            time.sleep(0.1)

        except Exception as e:
            logger.error(f"💥 Error en bucle principal: {str(e)}")
            enviar_telegram(f"⚠️ Error en el ciclo: {str(e)}. Reintentando...")
            time.sleep(2)

# --------------------------
# INICIO DEL PROGRAMA
# --------------------------
if __name__ == "__main__":
    escuchar_comandos()
