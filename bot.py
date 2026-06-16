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
ESPERA = 0.2
# Tiempos sincronizados con servidor
SEG_CONEXION = 53       # Segundo exacto para reconectar ambas cuentas
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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OFFSET = 0

# --------------------------
# CONEXIÓN SINCRONIZADA
# --------------------------
def conectar_cuenta(email, password, nombre):
    """Conexión independiente por cuenta"""
    try:
        iq = IQ_Option(email, password)
        ok, motivo = iq.connect()
        if ok:
            time.sleep(0.5)
            iq.change_balance("PRACTICE")
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} conectado | Saldo: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre}: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error {nombre}: {str(e)}")
        return None, 0

def conectar_ambas_sincronizado():
    """Conecta CUENTA 1 y CUENTA 2 AL MISMO TIEMPO, en paralelo"""
    logger.info("🔄 Conectando ambas cuentas al mismo tiempo...")
    res1 = {}
    res2 = {}

    t1 = Thread(target=lambda: res1.update(dict(zip(["iq","saldo"],
        conectar_cuenta(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")))))
    t2 = Thread(target=lambda: res2.update(dict(zip(["iq","saldo"],
        conectar_cuenta(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")))))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    iq1 = res1.get("iq")
    iq2 = res2.get("iq")
    saldo1 = res1.get("saldo",0)
    saldo2 = res2.get("saldo",0)

    if iq1 and iq2:
        enviar_telegram(f"✅ AMBAS CUENTAS CONECTADAS SINCRONIZADAS\nC1: ${saldo1} | C2: ${saldo2}")
    else:
        enviar_telegram("❌ Error: una o ambas cuentas no conectaron")

    return iq1, iq2

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
    enviar_telegram("🤖 Bot listo. /start = iniciar | /stop = detener")

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
                            "• Conexión sincronizada en cada minuto\n"
                            "• 1 operación en cada cuenta, mismo activo\n"
                            "• Cuenta 2 dirección invertida\n"
                            "• Solo ejecuta si ambas entran"
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
            return None
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
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela, resultado):
    if YA_OPERO.get(f"{nombre}_{vela}", False):
        resultado["ok"] = False
        resultado["razon"] = "Ya operó esta vela"
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
        YA_OPERO[f"{nombre}_{vela}"] = True
        resultado.update({"ok": True, "direccion": dir_final.upper(), "id": id_op, "saldo": saldo})
    else:
        resultado["ok"] = False
        resultado["razon"] = "No se pudo ejecutar"

# --------------------------
# BUCLE PRINCIPAL SINCRONIZADO
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES_C1, OPERACIONES_C2, CUENTA_ANALISIS
    iq1 = iq2 = None

    while BOT_ACTIVO:
        try:
            # Obtener tiempo EXACTO del servidor
            ts = int(time.time())
            seg = ts % 60
            vela_actual = ts // 60

            # 🔹 CONEXIÓN SINCRONIZADA: justo en el segundo 53 de cada minuto
            if seg == SEG_CONEXION:
                iq1, iq2 = conectar_ambas_sincronizado()
                if not iq1 or not iq2:
                    enviar_telegram("❌ Sin conexión en ambas cuentas. Esperando siguiente minuto...")
                    time.sleep(1)
                    continue

            # Verificar límite de operaciones
            if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
                enviar_telegram(
                    f"✅ FINALIZADO\n"
                    f"C1: {OPERACIONES_C1}/{MAX_OPER} | C2: {OPERACIONES_C2}/{MAX_OPER}\n"
                    "Bot detenido"
                )
                BOT_ACTIVO = False
                break

            # Reiniciar control al cambiar de vela
            if vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                logger.info(f"🔄 Nueva vela: {vela_actual}")

            # 🔹 DETECCIÓN DE SEÑAL en segundo 54
            senal = None
            if seg == SEG_DETECCION and iq1 and iq2:
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
                    enviar_telegram(f"🔔 SEÑAL: {act} | {dir_ori.upper()} | Fuerza: {fuerza}%")

            # 🔹 EJECUCIÓN en segundos 56 a 59
            if senal and SEG_INICIO <= seg <= SEG_FIN and iq1 and iq2:
                act, dir_ori, fuerza = senal
                res1 = {"ok": False}
                res2 = {"ok": False}

                # Ejecutar ambas al mismo tiempo
                t1 = Thread(target=ejecutar_orden, args=(iq1, "CUENTA_1", act, dir_ori, vela_actual, res1))
                t2 = Thread(target=ejecutar_orden, args=(iq2, "CUENTA_2", act, dir_ori, vela_actual, res2))
                t1.start()
                t2.start()
                t1.join()
                t2.join()

                # Solo contar si ambas entraron
                if res1["ok"] and res2["ok"]:
                    OPERACIONES_C1 += 1
                    OPERACIONES_C2 += 1
                    enviar_telegram(
                        f"✅ OPERACIÓN COMPLETA\n"
                        f"🔹 C1: {res1['direccion']} | Saldo: ${res1['saldo']}\n"
                        f"🔹 C2: {res2['direccion']} (INVERTIDA) | Saldo: ${res2['saldo']}\n"
                        f"📈 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                    )
                else:
                    enviar_telegram("❌ No entró en ambas cuentas. Operación cancelada.")

                senal = None

            time.sleep(0.05)

        except Exception as e:
            enviar_telegram(f"💥 Error: {str(e)} | Esperando siguiente minuto...")
            time.sleep(1)

# --------------------------
# EJECUCIÓN
# --------------------------
if __name__ == "__main__":
    escuchar_comandos()
