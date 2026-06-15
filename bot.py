import time
import os
import pandas as pd
import logging
from threading import Thread
from iqoptionapi.stable_api import IQ_Option

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

# --------------------------
# PARÁMETROS DE OPERACIÓN
# --------------------------
MONTO_POR_OPERACION = 600
EXPIRACION = 1
TIEMPO_VELA = 60
FUERZA_MINIMA = 98
REINTENTOS_MAX = 10
ESPERA_REINTENTO = 0.15
SEGUNDO_DETECCION = 54
SEGUNDO_INICIO = 56
SEGUNDO_FIN = 59

ACTIVOS = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]

# Control de ejecución por cuenta
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION_C1 = None
ULTIMA_OPERACION_C2 = None

# --------------------------
# CONEXIÓN 100% INDEPENDIENTE POR CUENTA
# --------------------------
def conectar_cuenta(email, password, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        # Instancia NUEVA y exclusiva para cada cuenta
        iq = IQ_Option(email, password)
        conectado, motivo = iq.connect()

        if conectado:
            time.sleep(1)
            iq.change_balance("PRACTICE")
            time.sleep(1)
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} | Correo: {email} | Saldo inicial: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} no conectó: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error en {nombre}: {str(e)}")
        return None, 0

def conectar_ambas_cuentas():
    # Conexión separada para CUENTA 1
    iq1, saldo1 = conectar_cuenta(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
    time.sleep(2)  # Espera para evitar mezcla
    # Conexión separada para CUENTA 2
    iq2, saldo2 = conectar_cuenta(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
    time.sleep(2)

    if not iq1 or not iq2:
        logger.critical("❌ No se pudieron conectar las 2 cuentas")
        return None, None
    return iq1, iq2

# --------------------------
# OBTENER DATOS DE MERCADO
# --------------------------
def obtener_datos(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.2)
        velas = iq.get_candles(activo, TIEMPO_VELA, 50, time.time())
        if not velas or len(velas) < 30:
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df if not df.empty else None
    except Exception as e:
        logger.error(f"⚠️ Error al obtener datos de {activo}: {e}")
        return None

# --------------------------
# FUNCIÓN PARA EJECUTAR ORDEN EN UNA CUENTA
# --------------------------
def ejecutar_orden_en_cuenta(iq, nombre_cuenta, activo, direccion, vela_id, resultado):
    global ULTIMA_OPERACION_C1, ULTIMA_OPERACION_C2

    # Evitar repetir en la misma vela
    if nombre_cuenta == "CUENTA_1" and ULTIMA_OPERACION_C1 == vela_id:
        resultado["ok"] = False
        return
    if nombre_cuenta == "CUENTA_2" and ULTIMA_OPERACION_C2 == vela_id:
        resultado["ok"] = False
        return

    logger.info(f"📤 Enviando orden DUPLICADA a {nombre_cuenta}: {activo} | {direccion} | ${MONTO_POR_OPERACION}")

    exito = False
    id_operacion = None
    saldo_final = None

    for intento in range(REINTENTOS_MAX):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)

            activos_disponibles = iq.get_all_ACTIVES_OPCODE()
            if activo not in activos_disponibles:
                logger.warning(f"⚠️ {nombre_cuenta}: {activo} no disponible, reintentando...")
                time.sleep(0.3)
                continue

            estado, id_op = iq.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_op > 0:
                time.sleep(0.4)
                saldo_final = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre_cuenta} | Ejecutada | ID: {id_op} | Saldo: ${saldo_final}")
                exito = True
                id_operacion = id_op
                break

            time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"⚠️ {nombre_cuenta} | Intento {intento+1}: {str(e)}")
            time.sleep(ESPERA_REINTENTO)

    if exito:
        if nombre_cuenta == "CUENTA_1":
            ULTIMA_OPERACION_C1 = vela_id
        else:
            ULTIMA_OPERACION_C2 = vela_id
        resultado["ok"] = True
        resultado["id"] = id_operacion
        resultado["saldo"] = saldo_final
    else:
        logger.error(f"❌ {nombre_cuenta} | No se pudo ejecutar la orden")
        resultado["ok"] = False

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def iniciar_bot():
    global ULTIMA_VELA_PROCESADA, ULTIMA_OPERACION_C1, ULTIMA_OPERACION_C2

    # Conectar ambas cuentas al inicio
    iq1, iq2 = conectar_ambas_cuentas()
    if not iq1 or not iq2:
        return

    logger.info("="*70)
    logger.info("🤖 BOT ACTIVO | MISMA ORDEN DUPLICADA PARA LAS 2 CUENTAS")
    logger.info(f"⚙️ Fuerza mínima: {FUERZA_MINIMA} | Entrada: {SEGUNDO_INICIO}-{SEGUNDO_FIN}s")
    logger.info("="*70)

    senal_guardada = None

    while True:
        try:
            # Usamos la cuenta 1 para obtener la hora del servidor
            tiempo_servidor = iq1.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            # Reiniciar control al cambiar de vela
            if vela_actual != ULTIMA_VELA_PROCESADA:
                ULTIMA_VELA_PROCESADA = vela_actual
                ULTIMA_OPERACION_C1 = None
                ULTIMA_OPERACION_C2 = None
                senal_guardada = None

            # PASO 1: Detectar la señal UNA SOLA VEZ
            if segundos == SEGUNDO_DETECCION:
                mejor = None
                mayor_fuerza = 0
                logger.info("🔍 Buscando señales...")
                for activo in ACTIVOS:
                    df = obtener_datos(iq1, activo)
                    if df is None or df.empty:
                        continue
                    resultado_señal = get_reversal_signal(df)
                    if resultado_señal:
                        direccion_original, fuerza, _ = resultado_señal
                        if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor = (activo, direccion_original, fuerza)

                if mejor:
                    activo, direccion_original, fuerza = mejor
                    direccion_final = "put" if direccion_original == "call" else "call"
                    senal_guardada = (activo, direccion_final, fuerza)
                    logger.info(f"✅ Señal generada: {activo} | {direccion_final} | Fuerza: {fuerza}")

            # PASO 2: DUPLICAR LA ORDEN Y ENVIAR A AMBAS CUENTAS
            if senal_guardada and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN:
                activo, direccion_final, fuerza = senal_guardada
                logger.info(f"🚀 ENVIANDO LA MISMA ORDEN DUPLICADA A LAS 2 CUENTAS")

                # Variables para guardar el resultado de cada una
                res_c1 = {"ok": False, "id": None, "saldo": None}
                res_c2 = {"ok": False, "id": None, "saldo": None}

                # Ejecutar en hilos separados para ir al mismo tiempo
                hilo1 = Thread(target=ejecutar_orden_en_cuenta, args=(iq1, "CUENTA_1", activo, direccion_final, vela_actual, res_c1))
                hilo2 = Thread(target=ejecutar_orden_en_cuenta, args=(iq2, "CUENTA_2", activo, direccion_final, vela_actual, res_c2))

                # Iniciar los dos al mismo tiempo
                hilo1.start()
                hilo2.start()

                # Esperar a que terminen ambas
                hilo1.join()
                hilo2.join()

                # Verificación final
                if res_c1["ok"] and res_c2["ok"]:
                    logger.info("="*70)
                    logger.info("✅✅ ÉXITO: LA MISMA ORDEN DUPLICADA SE EJECUTÓ EN AMBAS CUENTAS")
                    logger.info(f"💵 CUENTA 1: ${res_c1['saldo']} | CUENTA 2: ${res_c2['saldo']}")
                    logger.info("="*70)
                elif res_c1["ok"]:
                    logger.warning("⚠️ Solo se ejecutó en CUENTA 1 - reconectando CUENTA 2")
                    iq2, _ = conectar_cuenta(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
                elif res_c2["ok"]:
                    logger.warning("⚠️ Solo se ejecutó en CUENTA 2 - reconectando CUENTA 1")
                    iq1, _ = conectar_cuenta(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
                else:
                    logger.error("❌ No se ejecutó en ninguna cuenta")

                senal_guardada = None  # Limpiar para siguiente vela

            time.sleep(0.05)

        except Exception as e:
            logger.error(f"💥 Error en el bucle: {str(e)}")
            # Si falla todo, reconectar las dos cuentas
            iq1, iq2 = conectar_ambas_cuentas()
            time.sleep(3)

if __name__ == "__main__":
    iniciar_bot()
