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

# Variables de conexión
IQ1 = None
IQ2 = None

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OFFSET = 0

# --------------------------
# SINCRONIZACIÓN DE TIEMPO
# --------------------------
def obtener_tiempo_servidor(iq):
    try:
        if not iq or not iq.check_connect():
            return None, None
        ts_servidor = iq.get_server_timestamp()
        ts_local = int(time.time())
        desfase = abs(ts_servidor - ts_local)
        return ts_servidor, desfase
    except:
        return None, None

# --------------------------
# CONEXIÓN DE CUENTAS
# --------------------------
def conectar_cuenta(email, password, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, password)
        ok, motivo = iq.connect()
        if ok:
            time.sleep(0.5)
            iq.change_balance("PRACTICE")
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} conectado | Saldo: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} no conectó: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error en {nombre}: {str(e)}")
        return None, 0

def conectar_ambas():
    global IQ1, IQ2
    enviar_telegram("🔄 CONECTANDO AMBAS CUENTAS...")
    res1 = {}
    res2 = {}

    t1 = Thread(target=lambda: res1.update(
        dict(zip(["iq", "saldo"], conectar_cuenta(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA 1")))
    ))
    t2 = Thread(target=lambda: res2.update(
        dict(zip(["iq", "saldo"], conectar_cuenta(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA 2")))
    ))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    IQ1 = res1.get("iq")
    IQ2 = res2.get("iq")
    saldo1 = res1.get("saldo", 0)
    saldo2 = res2.get("saldo", 0)

    if IQ1 and IQ2:
        ts_servidor, desfase = obtener_tiempo_servidor(IQ1)
        if ts_servidor and desfase <= MAX_DESFASE_PERMITIDO:
            enviar_telegram(
                f"✅ AMBAS CUENTAS CONECTADAS\n"
                f"🔹 Cuenta 1: ${saldo1}\n"
                f"🔹 Cuenta 2: ${saldo2}\n"
                f"⏱️ Desfase con servidor: {desfase}s\n"
                f"🚀 Iniciando análisis de señales..."
            )
            return True
        else:
            enviar_telegram(f"⚠️ Desfase muy alto ({desfase}s). No se operará.")
            return False
    else:
        enviar_telegram("❌ Error: una o ambas cuentas no conectaron.")
        return False

def desconectar_ambas():
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
# TELEGRAM
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=texto, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"⚠️ Telegram: {e}")

def limpiar_telegram():
    global OFFSET
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        updates = bot.get_updates(offset=-1, timeout=1)
        if updates:
            OFFSET = updates[-1].update_id + 1
    except:
        OFFSET = 0

def escuchar_comandos():
    global BOT_ACTIVO, OPERACIONES_C1, OPERACIONES_C2, YA_OPERO, ULTIMA_VELA
    limpiar_telegram()
    enviar_telegram("🤖 Bot listo.\nUsa /start → Conectar cuentas y operar\nUsa /stop → Detener y desconectar")

    while True:
        try:
            bot = Bot(token=TELEGRAM_TOKEN)
            updates = bot.get_updates(offset=OFFSET, timeout=10)
            for upd in updates:
                OFFSET = upd.update_id + 1
                if not upd.message or str(upd.message.chat_id) != str(TELEGRAM_CHAT_ID):
                    continue
                cmd = upd.message.text.strip().lower()

                if cmd == "/start":
                    if not BOT_ACTIVO:
                        OPERACIONES_C1 = 0
                        OPERACIONES_C2 = 0
                        YA_OPERO.clear()
                        ULTIMA_VELA = None
                        if conectar_ambas():
                            BOT_ACTIVO = True
                            Thread(target=bucle_principal, daemon=True).start()
                    else:
                        enviar_telegram("ℹ️ El bot ya está activo.")

                elif cmd == "/stop":
                    if BOT_ACTIVO:
                        BOT_ACTIVO = False
                        desconectar_ambas()
                    else:
                        enviar_telegram("ℹ️ El bot ya está detenido.")

        except Exception as e:
            time.sleep(3)

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
    except:
        return None

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela, resultado):
    clave = f"{nombre}_{vela}"
    if YA_OPERO.get(clave, False):
        resultado["ok"] = False
        resultado["razon"] = "Ya operó esta vela"
        return

    dir_final = direccion
    if nombre == "CUENTA 2":
        dir_final = "put" if direccion == "call" else "call"

    exito = False
    id_op = None
    saldo = None

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
                break
            time.sleep(ESPERA)
        except:
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
            seg = ts_servidor % 60
            vela_actual = ts_servidor // 60

            # Verificar límite de operaciones
            if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
                enviar_telegram(
                    f"✅ PROCESO FINALIZADO\n"
                    f"🔹 Cuenta 1: {OPERACIONES_C1}/{MAX_OPER}\n"
                    f"🔹 Cuenta 2: {OPERACIONES_C2}/{MAX_OPER}"
                )
                BOT_ACTIVO = False
                desconectar_ambas()
                break

            # Nueva vela
            if vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                logger.info(f"🔄 Nueva vela: {vela_actual}")

            # Detectar señal
            senal = None
            if seg == SEG_DETECCION:
                mejor = None
                fuerza_max = 0
                iq_analisis = IQ1 if CUENTA_ANALISIS == 1 else IQ2
                CUENTA_ANALISIS = 2 if CUENTA_ANALISIS == 1 else 1

                for act in ACTIVOS:
                    df = obtener_velas(iq_analisis, act)
                    if df is None:
                        continue
                    res = get_reversal_signal(df)
                    if res:
                        dir_ori, fuerza, _ = res
                        if fuerza >= FUERZA_MIN and fuerza > fuerza_max:
                            fuerza_max = fuerza
                            mejor = (act, dir_ori, fuerza)

                if mejor:
                    act, dir_ori, fuerza = mejor
                    senal = (act, dir_ori, fuerza)
                    enviar_telegram(f"🔔 SEÑAL DETECTADA\n📈 {act} | {dir_ori.upper()} | Fuerza: {fuerza}%")

            # Ejecutar operación
            if senal and SEG_INICIO <= seg <= SEG_FIN:
                act, dir_ori, fuerza = senal
                res1 = {"ok": False}
                res2 = {"ok": False}

                t1 = Thread(target=ejecutar_orden, args=(IQ1, "CUENTA 1", act, dir_ori, vela_actual, res1))
                t2 = Thread(target=ejecutar_orden, args=(IQ2, "CUENTA 2", act, dir_ori, vela_actual, res2))
                t1.start()
                t2.start()
                t1.join()
                t2.join()

                if res1["ok"] and res2["ok"]:
                    OPERACIONES_C1 += 1
                    OPERACIONES_C2 += 1
                    enviar_telegram(
                        f"✅ OPERACIÓN EJECUTADA\n"
                        f"🔹 Cuenta 1: {res1['direccion']} | Saldo: ${res1['saldo']}\n"
                        f"🔹 Cuenta 2: {res2['direccion']} | Saldo: ${res2['saldo']}\n"
                        f"📊 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                    )
                else:
                    enviar_telegram("❌ No se ejecutó en ambas cuentas. Operación cancelada.")

                senal = None

            time.sleep(0.05)

        except Exception as e:
            enviar_telegram(f"💥 Error: {str(e)} | Reintentando...")
            time.sleep(2)

# --------------------------
# INICIO
# --------------------------
if __name__ == "__main__":
    escuchar_comandos()
