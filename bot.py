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
REINTENTOS = 5
ESPERA = 0.3
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
YA_OPERO = {}  # clave: vela, valor: True/False para no repetir
CUENTA_ANALISIS = 1

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OFFSET = 0

# --------------------------
# CONEXIÓN SEPARADA POR CUENTA
# --------------------------
def conectar_cuenta(email, password, nombre):
    """Conexión independiente y única para cada cuenta"""
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, password)
        conectado, motivo = iq.connect()
        if conectado:
            time.sleep(1)
            iq.change_balance("PRACTICE")
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} conectado | Saldo: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} NO conectado: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error al conectar {nombre}: {str(e)}")
        return None, 0

def verificar_ambas_cuentas():
    """Sigue ejecutando solo si ambas están activas"""
    iq1, saldo1 = conectar_cuenta(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
    time.sleep(2)
    iq2, saldo2 = conectar_cuenta(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
    return iq1, saldo1, iq2, saldo2

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
        else:
            OFFSET = 0
    except:
        OFFSET = 0

def escuchar_comandos():
    global BOT_ACTIVO, OPERACIONES_C1, OPERACIONES_C2, YA_OPERO, ULTIMA_VELA
    limpiar_telegram()
    enviar_telegram("🤖 Bot listo. Usa /start para operar, /stop para detener.")

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
                        BOT_ACTIVO = True
                        Thread(target=bucle_principal, daemon=True).start()
                        enviar_telegram(
                            "✅ BOT INICIADO\n"
                            "• Solo opera si ambas cuentas están activas\n"
                            "• Cuenta 1: dirección original\n"
                            "• Cuenta 2: dirección invertida\n"
                            "• 1 operación por vela, nunca repite"
                        )
                    else:
                        enviar_telegram("ℹ️ Ya está activo")

                elif cmd == "/stop":
                    BOT_ACTIVO = False
                    enviar_telegram("⏹️ Bot detenido")

        except Exception as e:
            time.sleep(3)

# --------------------------
# DATOS DE MERCADO
# --------------------------
def obtener_velas(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.2)
        velas = iq.get_candles(activo, VELA, 50, time.time())
        if not velas or len(velas) < 30:
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max":"high", "min":"low"}, inplace=True)
        df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
        return df
    except:
        return None

# --------------------------
# EJECUTAR ORDEN (UNA SOLA POR CUENTA)
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela, resultado):
    """Solo 1 intento exitoso por cuenta y vela"""
    if YA_OPERO.get(f"{nombre}_{vela}", False):
        resultado["ok"] = False
        resultado["mensaje"] = f"Ya operó en esta vela"
        return

    dir_final = direccion
    if nombre == "CUENTA_2":
        dir_final = "put" if direccion == "call" else "call"

    exito = False
    id_op = None
    saldo = None

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)
            ok, id_op = iq.buy(MONTO, activo, dir_final, EXPIRACION)
            if ok and id_op > 0:
                time.sleep(0.5)
                saldo = round(iq.get_balance(), 2)
                exito = True
                break
            time.sleep(ESPERA)
        except:
            time.sleep(ESPERA)

    if exito:
        YA_OPERO[f"{nombre}_{vela}"] = True
        resultado.update({
            "ok": True,
            "direccion": dir_final.upper(),
            "id": id_op,
            "saldo": saldo
        })
    else:
        resultado["ok"] = False
        resultado["mensaje"] = "No se pudo ejecutar"

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES_C1, OPERACIONES_C2, CUENTA_ANALISIS

    while BOT_ACTIVO:
        # Verificar conexiones en cada ciclo
        iq1, saldo_c1, iq2, saldo_c2 = verificar_ambas_cuentas()
        if not iq1 or not iq2:
            enviar_telegram("❌ Una o ambas cuentas no conectadas. Se detiene operación.")
            time.sleep(5)
            continue

        if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
            enviar_telegram(
                f"✅ FINALIZADO\n"
                f"C1: {OPERACIONES_C1} | C2: {OPERACIONES_C2}\n"
                "Bot detenido"
            )
            BOT_ACTIVO = False
            break

        ts = iq1.get_server_timestamp()
        seg = int(ts % 60)
        vela_actual = int(ts // 60)

        if vela_actual != ULTIMA_VELA:
            ULTIMA_VELA = vela_actual
            logger.info(f"🔄 Nueva vela: {vela_actual}")

        # Detectar señal
        senal = None
        if seg == SEG_DETECCION:
            mejor = None
            fuerza_max = 0
            iq_analisis = iq1 if CUENTA_ANALISIS == 1 else iq2
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
                enviar_telegram(f"🔔 Señal: {act} | {dir_ori.upper()} | Fuerza: {fuerza}%")

        # Ejecutar solo si hay señal y estamos en el rango
        if senal and SEG_INICIO <= seg <= SEG_FIN:
            act, dir_ori, fuerza = senal
            res1 = {"ok": False}
            res2 = {"ok": False}

            # Ejecutar en hilos separados
            t1 = Thread(target=ejecutar_orden, args=(iq1, "CUENTA_1", act, dir_ori, vela_actual, res1))
            t2 = Thread(target=ejecutar_orden, args=(iq2, "CUENTA_2", act, dir_ori, vela_actual, res2))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # Solo contar si ambas se ejecutaron
            if res1["ok"] and res2["ok"]:
                OPERACIONES_C1 += 1
                OPERACIONES_C2 += 1
                enviar_telegram(
                    f"📊 OPERACIÓN COMPLETA\n"
                    f"🔹 Cuenta 1: {res1['direccion']} | Saldo: ${res1['saldo']}\n"
                    f"🔹 Cuenta 2: {res2['direccion']} (INVERTIDA) | Saldo: ${res2['saldo']}\n"
                    f"📈 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                )
            else:
                enviar_telegram("❌ No se ejecutó en ambas cuentas. Operación cancelada.")

            senal = None

        time.sleep(0.1)

# --------------------------
# EJECUCIÓN
# --------------------------
if __name__ == "__main__":
    escuchar_comandos()
