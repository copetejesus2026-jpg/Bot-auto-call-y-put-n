import time
import os
import pandas as pd
import logging
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

# --------------------------
# CONFIGURACIÓN DE LOGS
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
REINTENTOS_MAX = 8
ESPERA_REINTENTO = 0.1
ESPERA_ENTRE_CUENTAS = 0.1  # Menor espera para mayor sincronía
SEGUNDO_DETECCION = 55
SEGUNDO_INICIO = 56
SEGUNDO_FIN = 59

ACTIVOS = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]

# Control de ejecución
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION = {"CUENTA_1": None, "CUENTA_2": None}

# --------------------------
# CONEXIÓN 100% INDEPENDIENTE
# --------------------------
def conectar_cuenta(email, password, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, password)
        conectado, motivo = iq.connect()

        if conectado:
            time.sleep(0.6)
            iq.change_balance("PRACTICE")
            time.sleep(0.6)
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} | Correo: {email} | Saldo inicial: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} no conectó: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error en {nombre}: {str(e)}")
        return None, 0

def desconectar_todas(cuentas):
    for cuenta in cuentas:
        try:
            cuenta["conexion"].close_connect()
            logger.info(f"🔌 Sesión cerrada: {cuenta['nombre']}")
        except:
            pass

def conectar_cuentas():
    cuentas = []
    credenciales = [
        ("CUENTA_1", os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1")),
        ("CUENTA_2", os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"))
    ]

    for nombre, correo, clave in credenciales:
        if not correo or not clave:
            logger.error(f"{nombre}: Faltan credenciales en variables de entorno")
            continue
        iq, saldo = conectar_cuenta(correo, clave, nombre)
        if iq and saldo >= MONTO_POR_OPERACION:
            cuentas.append({"nombre": nombre, "conexion": iq, "saldo": saldo})
        time.sleep(0.8)

    if len(cuentas) != 2:
        logger.critical(f"⚠️ Solo {len(cuentas)} cuentas conectadas. Se requieren 2 para operar")
    return cuentas

# --------------------------
# OBTENER DATOS DE VELAS
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
        if df.empty:
            return None
        return df
    except Exception as e:
        logger.error(f"⚠️ Error en {activo}: {e}")
        return None

# --------------------------
# EJECUTAR ORDEN EN CUENTA
# --------------------------
def ejecutar_orden(cuenta, activo, direccion, vela_id):
    nombre = cuenta["nombre"]
    iq = cuenta["conexion"]

    if ULTIMA_OPERACION[nombre] == vela_id:
        logger.info(f"🔒 {nombre}: Ya operó en esta vela, omitiendo")
        return False, None

    logger.info(f"📤 Enviando orden a {nombre}: {activo} | {direccion} | ${MONTO_POR_OPERACION}")

    for intento in range(REINTENTOS_MAX):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)

            activos_disponibles = iq.get_all_ACTIVES_OPCODE()
            if activo not in activos_disponibles:
                logger.warning(f"⚠️ {nombre}: {activo} no disponible, reintentando...")
                time.sleep(0.2)
                continue

            estado, id_op = iq.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_op > 0:
                ULTIMA_OPERACION[nombre] = vela_id
                time.sleep(0.3)
                saldo_actual = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre} | Ejecutado | ID: {id_op} | Saldo: ${saldo_actual}")
                return True, saldo_actual

            time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"⚠️ {nombre} | Intento {intento+1}: {str(e)}")
            time.sleep(ESPERA_REINTENTO)

    logger.error(f"❌ {nombre} | NO SE PUDO EJECUTAR ORDEN en {activo}")
    return False, None

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def iniciar_bot():
    global ULTIMA_VELA_PROCESADA, ULTIMA_OPERACION
    CUENTAS = conectar_cuentas()

    if len(CUENTAS) != 2:
        logger.critical("❌ NO SE PUEDE OPERAR: Faltan cuentas")
        return

    logger.info("="*70)
    logger.info("🤖 BOT ACTIVO | MISMA ORDEN A AMBAS CUENTAS | BLOQUEO SI FALTA UNA")
    logger.info(f"⚙️ Fuerza mínima: {FUERZA_MINIMA} | Entrada: {SEGUNDO_INICIO}-{SEGUNDO_FIN}s")
    logger.info("="*70)

    senal_guardada = None

    while True:
        try:
            iq_ref = CUENTAS[0]["conexion"]
            tiempo_servidor = iq_ref.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            # Reiniciar al cambiar de vela
            if vela_actual != ULTIMA_VELA_PROCESADA:
                ULTIMA_OPERACION = {"CUENTA_1": None, "CUENTA_2": None}
                ULTIMA_VELA_PROCESADA = vela_actual
                senal_guardada = None

            # Detectar señal
            if segundos == SEGUNDO_DETECCION:
                mejor = None
                mayor_fuerza = 0
                logger.info("🔍 Buscando señales...")
                for activo in ACTIVOS:
                    df = obtener_datos(iq_ref, activo)
                    if df is None or df.empty:
                        continue
                    resultado = get_reversal_signal(df)
                    if resultado:
                        dir_ori, fuerza, _ = resultado
                        if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor = (activo, dir_ori, fuerza)

                if mejor:
                    activo, dir_ori, fuerza = mejor
                    dir_final = "put" if dir_ori == "call" else "call"
                    senal_guardada = (activo, dir_final, fuerza)
                    logger.info(f"✅ Señal confirmada: {activo} | {dir_final} | Fuerza: {fuerza}")

            # Ejecutar solo si hay señal y estamos en el rango
            if senal_guardada and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN:
                activo, dir_final, fuerza = senal_guardada
                logger.info(f"🚀 ENVIANDO MISMA ORDEN A AMBAS CUENTAS: {activo} | {dir_final}")

                # Ejecutar en cuenta 1
                ok1, saldo1 = ejecutar_orden(CUENTAS[0], activo, dir_final, vela_actual)
                time.sleep(ESPERA_ENTRE_CUENTAS)

                # Ejecutar en cuenta 2
                ok2, saldo2 = ejecutar_orden(CUENTAS[1], activo, dir_final, vela_actual)

                # Verificación final
                if ok1 and ok2:
                    logger.info("="*70)
                    logger.info("✅✅ ÉXITO: AMBAS CUENTAS RECIBIERON LA MISMA ORDEN")
                    logger.info(f"💵 CUENTA 1: ${saldo1} | CUENTA 2: ${saldo2}")
                    logger.info("="*70)
                elif ok1:
                    logger.critical("⛔ BLOQUEADO: SOLO SE EJECUTÓ EN CUENTA 1 - NO SE CONSIDERA VÁLIDA")
                elif ok2:
                    logger.critical("⛔ BLOQUEADO: SOLO SE EJECUTÓ EN CUENTA 2 - NO SE CONSIDERA VÁLIDA")
                else:
                    logger.critical("⛔ BLOQUEADO: NO SE EJECUTÓ EN NINGUNA CUENTA")

                senal_guardada = None  # Limpiar para siguiente vela

            time.sleep(0.05)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {str(e)}")
            desconectar_todas(CUENTAS)
            time.sleep(2)
            CUENTAS = conectar_cuentas()

if __name__ == "__main__":
    iniciar_bot()
