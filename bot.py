import time
import os
import pandas as pd
import logging
from threading import Thread
from iqoptionapi.stable_api import IQ_Option

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

# Parámetros
MONTO = 600
EXPIRACION = 1
VELA_SEGUNDOS = 60
FUERZA_MIN = 98
REINTENTOS = 10
ESPERA = 0.2
SEG_DETECCION = 54
SEG_INICIO = 56
SEG_FIN = 59

ACTIVOS = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]

# Control
ULTIMA_VELA = None
OP_C1 = None
OP_C2 = None

# --------------------------
# CONEXIÓN 100% SEPARADA
# --------------------------
def conectar(email, passw, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, passw)
        ok, razon = iq.connect()
        if ok:
            time.sleep(1)
            iq.change_balance("PRACTICE")
            time.sleep(1)
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} | Saldo: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre}: {razon}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error {nombre}: {e}")
        return None, 0

def conectar_ambas():
    iq1, _ = conectar(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
    time.sleep(2)
    iq2, _ = conectar(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
    time.sleep(2)
    return (iq1, iq2) if (iq1 and iq2) else (None, None)

# --------------------------
# DATOS DE MERCADO
# --------------------------
def velas(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.2)
        datos = iq.get_candles(activo, VELA_SEGUNDOS, 50, time.time())
        if not datos or len(datos) < 30:
            return None
        df = pd.DataFrame(datos)
        df.rename(columns={"max":"high", "min":"low"}, inplace=True)
        df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
        return df
    except Exception as e:
        logger.error(f"⚠️ {activo}: {e}")
        return None

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def orden(iq, nombre, activo, dir, vela, res):
    global OP_C1, OP_C2
    if (nombre == "CUENTA_1" and OP_C1 == vela) or (nombre == "CUENTA_2" and OP_C2 == vela):
        res["ok"] = False
        return

    logger.info(f"📤 Enviando a {nombre}: {activo} {dir} ${MONTO}")
    ok = False
    saldo = id_op = None

    for i in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)
            if activo not in iq.get_all_ACTIVES_OPCODE():
                time.sleep(0.3)
                continue
            estado, id_op = iq.buy(MONTO, activo, dir, EXPIRACION)
            if estado and id_op > 0:
                time.sleep(0.4)
                saldo = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre} | ID: {id_op} | Saldo: ${saldo}")
                ok = True
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ {nombre} intento {i+1}: {e}")
            time.sleep(ESPERA)

    if ok:
        if nombre == "CUENTA_1":
            OP_C1 = vela
        else:
            OP_C2 = vela
        res.update({"ok":True, "id":id_op, "saldo":saldo})
    else:
        res["ok"] = False

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def iniciar():
    global ULTIMA_VELA, OP_C1, OP_C2
    iq1, iq2 = conectar_ambas()
    if not iq1 or not iq2:
        logger.critical("❌ No se conectaron las cuentas")
        return

    logger.info("="*60)
    logger.info("🤖 BOT | MISMA ORDEN EN 2 CUENTAS | RAILWAY COMPATIBLE")
    logger.info("="*60)

    senal = None

    while True:
        try:
            ts = iq1.get_server_timestamp()
            seg = int(ts % 60)
            vela_act = int(ts // 60)

            if vela_act != ULTIMA_VELA:
                ULTIMA_VELA = vela_act
                OP_C1 = OP_C2 = None
                senal = None

            if seg == SEG_DETECCION:
                mejor = None
                fuerza_max = 0
                for act in ACTIVOS:
                    df = velas(iq1, act)
                    if not df:
                        continue
                    s = get_reversal_signal(df)
                    if s:
                        d, f, _ = s
                        if f >= FUERZA_MIN and f > fuerza_max:
                            fuerza_max = f
                            mejor = (act, d, f)
                if mejor:
                    act, d, f = mejor
                    dir_final = "put" if d == "call" else "call"
                    senal = (act, dir_final, f)
                    logger.info(f"✅ Señal: {act} {dir_final} | Fuerza: {f}")

            if senal and SEG_INICIO <= seg <= SEG_FIN:
                act, dir_final, f = senal
                logger.info("🚀 ENVIANDO A AMBAS CUENTAS")

                r1 = {"ok":False}
                r2 = {"ok":False}

                t1 = Thread(target=orden, args=(iq1, "CUENTA_1", act, dir_final, vela_act, r1))
                t2 = Thread(target=orden, args=(iq2, "CUENTA_2", act, dir_final, vela_act, r2))
                t1.start(); t2.start(); t1.join(); t2.join()

                if r1["ok"] and r2["ok"]:
                    logger.info("✅✅ AMBAS OPERARON CORRECTAMENTE")
                    logger.info(f"💵 C1: ${r1['saldo']} | C2: ${r2['saldo']}")
                elif r1["ok"]:
                    logger.warning("⚠️ Solo C1, reconectando C2")
                    iq2, _ = conectar(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
                elif r2["ok"]:
                    logger.warning("⚠️ Solo C2, reconectando C1")
                    iq1, _ = conectar(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
                else:
                    logger.error("❌ Ninguna operó")

                senal = None

            time.sleep(0.05)

        except Exception as e:
            logger.error(f"💥 Error: {e}")
            iq1, iq2 = conectar_ambas()
            time.sleep(3)

if __name__ == "__main__":
    iniciar()
