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
REINTENTOS = 8
ESPERA = 0.2
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

# Límites: 1 operación por señal, máximo 15 totales por cuenta
MAX_OPER_C1 = 15
MAX_OPER_C2 = 15
OPERACIONES_C1 = 0
OPERACIONES_C2 = 0

BOT_ACTIVO = False
ULTIMA_VELA = None
YA_OPERO_C1 = False  # Control estricto: ya operó en esta vela
YA_OPERO_C2 = False
CUENTA_ANALISIS = 1

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OFFSET = 0

# --------------------------
# CONEXIÓN CUENTAS
# --------------------------
def conectar(email, clave, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, clave)
        ok, motivo = iq.connect()
        if ok:
            time.sleep(1)
            iq.change_balance("PRACTICE")
            saldo = round(iq.get_balance(), 2)
            mensaje = f"✅ {nombre} conectado | Saldo: ${saldo}"
            logger.info(mensaje)
            enviar_mensaje_telegram(mensaje)
            return iq, saldo
        else:
            mensaje = f"❌ Error {nombre}: {motivo}"
            logger.error(mensaje)
            enviar_mensaje_telegram(mensaje)
            return None, 0
    except Exception as e:
        mensaje = f"❌ Fallo {nombre}: {str(e)}"
        logger.error(mensaje)
        enviar_mensaje_telegram(mensaje)
        return None, 0

def conectar_ambas():
    iq1, _ = conectar(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
    time.sleep(2)
    iq2, _ = conectar(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
    return iq1, iq2

# --------------------------
# TELEGRAM SIN ERRORES
# --------------------------
def enviar_mensaje_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=texto, parse_mode="HTML")
    except TelegramError as e:
        logger.warning(f"⚠️ Telegram: {e}")

def limpiar_mensajes_antiguos():
    global OFFSET
    if not TELEGRAM_TOKEN:
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        updates = bot.get_updates(offset=-1, timeout=1)
        if updates:
            OFFSET = updates[-1].update_id + 1
        else:
            OFFSET = 0
        logger.info("📡 Telegram limpio y listo")
        enviar_mensaje_telegram("🤖 Bot listo. Usa /start para operar y /stop para detener.")
    except Exception as e:
        logger.warning(f"⚠️ Limpieza Telegram: {e}")

def escuchar_comandos():
    global BOT_ACTIVO, OPERACIONES_C1, OPERACIONES_C2, OFFSET, YA_OPERO_C1, YA_OPERO_C2, ULTIMA_VELA
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Sin credenciales de Telegram")
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    limpiar_mensajes_antiguos()

    while True:
        try:
            updates = bot.get_updates(offset=OFFSET, timeout=10)
            for upd in updates:
                OFFSET = upd.update_id + 1
                if not upd.message or str(upd.message.chat_id) != str(TELEGRAM_CHAT_ID):
                    continue
                texto = upd.message.text.strip().lower()

                if texto == "/start":
                    if not BOT_ACTIVO:
                        # Reiniciar TODO al iniciar
                        OPERACIONES_C1 = 0
                        OPERACIONES_C2 = 0
                        YA_OPERO_C1 = False
                        YA_OPERO_C2 = False
                        ULTIMA_VELA = None
                        BOT_ACTIVO = True
                        Thread(target=bucle_principal, daemon=True).start()
                        enviar_mensaje_telegram(
                            "✅ BOT INICIADO ✅\n"
                            "• CUENTA 1: 1 sola operación por señal (dirección original)\n"
                            "• CUENTA 2: 1 sola operación por señal (dirección INVERTIDA)\n"
                            "• No permite duplicados en ninguna cuenta\n"
                            "• Se muestra saldo actualizado de ambas"
                        )
                    else:
                        enviar_mensaje_telegram("ℹ️ El bot ya está activo.")

                elif texto == "/stop":
                    BOT_ACTIVO = False
                    enviar_mensaje_telegram("⏹️ Bot DETENIDO.")

        except TelegramError as e:
            if "Conflict" in str(e):
                OFFSET = 0
                time.sleep(2)
            else:
                logger.warning(f"⚠️ Telegram: {e}")
                time.sleep(3)
        except Exception as e:
            logger.warning(f"⚠️ Comandos: {e}")
            time.sleep(3)

# --------------------------
# DATOS DE MERCADO
# --------------------------
def obtener_velas(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.2)
        datos = iq.get_candles(activo, VELA, 50, time.time())
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
# EJECUTAR ORDEN (CONTROL ESTRICTO DE 1 POR CUENTA)
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, resultado):
    global YA_OPERO_C1, YA_OPERO_C2, OPERACIONES_C1, OPERACIONES_C2

    # ✅ BLOQUEO ABSOLUTO: solo 1 operación por cuenta por vela
    if nombre == "CUENTA_1":
        if YA_OPERO_C1 or OPERACIONES_C1 >= MAX_OPER_C1:
            resultado["ok"] = False
            resultado["razon"] = "Ya realizó su operación en esta vela"
            return
        dir_final = direccion  # Dirección original
    else:
        if YA_OPERO_C2 or OPERACIONES_C2 >= MAX_OPER_C2:
            resultado["ok"] = False
            resultado["razon"] = "Ya realizó su operación en esta vela"
            return
        dir_final = "put" if direccion == "call" else "call"  # Dirección invertida

    logger.info(f"📤 Enviando a {nombre}: {activo} {dir_final.upper()} | Monto: ${MONTO}")
    exito = False
    saldo_final = id_op = None

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)
            disponibles = iq.get_all_ACTIVES_OPCODE()
            if activo not in disponibles:
                logger.warning(f"⚠️ {activo} no disponible")
                time.sleep(0.3)
                continue
            estado, id_op = iq.buy(MONTO, activo, dir_final, EXPIRACION)
            if estado and id_op > 0:
                time.sleep(0.5)
                saldo_final = round(iq.get_balance(), 2)
                exito = True
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ {nombre} intento {intento+1}: {str(e)}")
            time.sleep(ESPERA)

    if exito:
        # Marcar como ya operó para esta vela
        if nombre == "CUENTA_1":
            YA_OPERO_C1 = True
            OPERACIONES_C1 += 1
        else:
            YA_OPERO_C2 = True
            OPERACIONES_C2 += 1

        resultado.update({
            "ok": True,
            "direccion": dir_final.upper(),
            "id": id_op,
            "saldo": saldo_final,
            "razon": "Operación ejecutada correctamente"
        })
    else:
        resultado["ok"] = False
        resultado["direccion"] = None
        resultado["saldo"] = None
        resultado["razon"] = "No se pudo abrir la operación"

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, YA_OPERO_C1, YA_OPERO_C2, OPERACIONES_C1, OPERACIONES_C2, CUENTA_ANALISIS
    iq1, iq2 = conectar_ambas()
    if not iq1 or not iq2:
        enviar_mensaje_telegram("❌ No se pudieron conectar ambas cuentas. Bot detenido.")
        BOT_ACTIVO = False
        return

    enviar_mensaje_telegram("🤖 BOT ACTIVO | Analizando señales...")
    senal = None

    while BOT_ACTIVO:
        try:
            # Detener al completar el límite de operaciones
            if OPERACIONES_C1 >= MAX_OPER_C1 and OPERACIONES_C2 >= MAX_OPER_C2:
                enviar_mensaje_telegram(
                    "✅ FINALIZADO ✅\n"
                    f"• Cuenta 1: {OPERACIONES_C1}/15 operaciones\n"
                    f"• Cuenta 2: {OPERACIONES_C2}/15 operaciones\n"
                    "Bot detenido automáticamente."
                )
                BOT_ACTIVO = False
                break

            # Obtener tiempo del servidor
            ts = iq1.get_server_timestamp()
            seg = int(ts % 60)
            vela_actual = int(ts // 60)

            # ✅ Reiniciar TODO al cambiar de vela
            if vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                YA_OPERO_C1 = False
                YA_OPERO_C2 = False
                senal = None
                CUENTA_ANALISIS = 2 if CUENTA_ANALISIS == 1 else 1
                logger.info(f"🔄 Nueva vela iniciada: {vela_actual}")

            # Detectar señal
            if seg == SEG_DETECCION:
                mejor = None
                fuerza_max = 0
                logger.info(f"🔍 Analizando con CUENTA_{CUENTA_ANALISIS}")
                iq_analisis = iq1 if CUENTA_ANALISIS == 1 else iq2

                for activo in ACTIVOS:
                    df = obtener_velas(iq_analisis, activo)
                    if df is None or df.empty:
                        continue
                    resultado_senal = get_reversal_signal(df)
                    if resultado_senal:
                        dir_ori, fuerza, _ = resultado_senal
                        if fuerza >= FUERZA_MIN and fuerza > fuerza_max:
                            fuerza_max = fuerza
                            mejor = (activo, dir_ori, fuerza)

                if mejor:
                    activo, dir_ori, fuerza = mejor
                    senal = (activo, dir_ori, fuerza)
                    enviar_mensaje_telegram(
                        f"🔔 SEÑAL DETECTADA\n"
                        f"📈 Activo: {activo}\n"
                        f"➡️ Dirección base: {dir_ori.upper()}\n"
                        f"💪 Fuerza: {fuerza}%"
                    )

            # Ejecutar en el rango de tiempo permitido
            if senal and SEG_INICIO <= seg <= SEG_FIN:
                activo, dir_ori, fuerza = senal
                logger.info("🚀 Ejecutando operaciones en ambas cuentas")

                res_c1 = {"ok": False}
                res_c2 = {"ok": False}

                # Ejecutar Cuenta 1 (solo una vez por vela)
                if OPERACIONES_C1 < MAX_OPER_C1:
                    hilo_c1 = Thread(target=ejecutar_orden, args=(iq1, "CUENTA_1", activo, dir_ori, res_c1))
                    hilo_c1.start()
                    hilo_c1.join()

                # Ejecutar Cuenta 2 (solo una vez por vela, dirección invertida)
                if OPERACIONES_C2 < MAX_OPER_C2:
                    hilo_c2 = Thread(target=ejecutar_orden, args=(iq2, "CUENTA_2", activo, dir_ori, res_c2))
                    hilo_c2.start()
                    hilo_c2.join()

                # Enviar resumen completo
                enviar_mensaje_telegram(
                    f"📊 RESUMEN OPERACIÓN 📊\n"
                    f"🔹 CUENTA 1: {res_c1.get('direccion','NO EJECUTADA')}\n"
                    f"   ID: {res_c1.get('id','-')} | Saldo: ${res_c1.get('saldo','-')}\n"
                    f"🔹 CUENTA 2: {res_c2.get('direccion','NO EJECUTADA')} (INVERTIDA)\n"
                    f"   ID: {res_c2.get('id','-')} | Saldo: ${res_c2.get('saldo','-')}\n"
                    f"📌 Progreso: C1 {OPERACIONES_C1}/15 | C2 {OPERACIONES_C2}/15"
                )

                # Limpiar señal para no repetir
                senal = None

            time.sleep(0.05)

        except Exception as e:
            error_msg = f"💥 Error: {str(e)} | Reconectando..."
            logger.error(error_msg)
            enviar_mensaje_telegram(error_msg)
            iq1, iq2 = conectar_ambas()
            time.sleep(3)

# --------------------------
# EJECUCIÓN
# --------------------------
if __name__ == "__main__":
    escuchar_comandos()
