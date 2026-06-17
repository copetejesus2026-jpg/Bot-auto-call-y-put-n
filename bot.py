import time
import os
import pandas as pd
import logging
import sys
import requests

try:
    from telegram import Bot
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

MONTO = 600
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 80
SEG_INICIO = 0
SEG_FIN = 9
REINTENTOS = 10
MAX_OPER = 20

ACTIVOS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURJPY-OTC",
    "USDCHF-OTC", "AUDUSD-OTC", "GBPJPY-OTC"
]

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD = os.getenv("IQ_PASSWORD_1", "")

# Variables globales
TOKEN = None
USER_ID = None
OPERACIONES = 0
BOT_ACTIVO = True
ULTIMA_VELA = None
YA_EJECUTADO = {}
mejor_senal = None

# --------------------------
# TELEGRAM
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        Bot(token=TELEGRAM_TOKEN).send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
    except Exception as e: logger.error(f"Telegram: {e}")

# --------------------------
# LOGIN CON URL ACTUALIZADA
# --------------------------
def obtener_token():
    global TOKEN, USER_ID
    try:
        # ✅ URL CORREGIDA (la anterior ya no existe)
        resp = requests.post(
            "https://api.iqoption.com/v2/user/login",
            json={"email": IQ_EMAIL, "password": IQ_PASSWORD},
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        if resp.status_code == 200:
            datos = resp.json()
            TOKEN = datos.get("token")
            USER_ID = datos.get("user_id")
            saldo = round(datos.get("balance", 0), 2)
            logger.info(f"✅ CONECTADO | Saldo: ${saldo}")
            enviar_telegram(f"✅ BOT ACTIVO\n💵 Saldo: ${saldo}\n🎯 Solo reversión exacta")
            return True
        logger.error(f"Error login: Código {resp.status_code} | {resp.text[:100]}...")
    except Exception as e: logger.error(f"Error conexión login: {str(e)}")
    return False

def verificar_token():
    global TOKEN
    if not TOKEN: return obtener_token()
    try:
        resp = requests.get(
            "https://api.iqoption.com/v2/user/profile",
            headers={"Authorization": TOKEN},
            timeout=10
        )
        if resp.status_code == 200: return True
        logger.warning("Token expirado o inválido — renovando")
        return obtener_token()
    except Exception as e:
        logger.warning(f"Verificación token: {e}")
        return obtener_token()

# --------------------------
# OBTENER VELAS — URL CORREGIDA
# --------------------------
def obtener_velas(activo, cantidad=50):
    if not verificar_token(): return None
    try:
        resp = requests.get(
            f"https://api.iqoption.com/v2/candles/{activo}/{VELA}/0/{cantidad}",
            headers={"Authorization": TOKEN},
            timeout=12
        )
        if resp.status_code == 200:
            datos = resp.json().get("candles", [])
            if len(datos) >= 40:
                df = pd.DataFrame(datos)
                df.rename(columns={"max":"high", "min":"low", "start":"time"}, inplace=True)
                df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
                return df
    except Exception as e: logger.warning(f"Velas {activo}: {e}")
    return None

# --------------------------
# EJECUTAR OPERACIÓN — URL CORREGIDA
# --------------------------
def ejecutar_orden(activo, direccion, vela_id):
    clave = f"orden_{vela_id}"
    if YA_EJECUTADO.get(clave): return False, None, 0.0
    if not verificar_token(): return False, None, 0.0

    tipo = "call" if direccion == "call" else "put"
    for intento in range(REINTENTOS):
        try:
            resp = requests.post(
                "https://api.iqoption.com/v2/trade/buy",
                headers={"Authorization": TOKEN, "Content-Type": "application/json"},
                json={
                    "instrument_id": activo,
                    "instrument_type": "binary-option",
                    "amount": MONTO,
                    "direction": tipo,
                    "expiration": EXPIRACION
                },
                timeout=15
            )
            if resp.status_code == 200:
                res = resp.json()
                if res.get("success"):
                    id_op = res.get("id")
                    saldo_actual = round(res.get("balance", 0), 2)
                    YA_EJECUTADO[clave] = True
                    logger.info(f"✅ ORDEN {tipo.upper()} | {activo} | ID:{id_op}")
                    return True, id_op, saldo_actual
            logger.warning(f"Intento {intento+1} falló: {resp.text[:100]}...")
        except Exception as e: logger.warning(f"Orden: {e}")
        time.sleep(0.5)
    return False, None, 0.0

# --------------------------
# TIEMPO SERVIDOR
# --------------------------
def tiempo_servidor():
    try:
        resp = requests.get("https://api.iqoption.com/v2/time", timeout=8)
        if resp.status_code == 200: return int(resp.json().get("timestamp", time.time()))
    except: pass
    return int(time.time())

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES, mejor_senal
    logger.info("🚀 BOT: 100% API REST — SIN WEBSOCKET")
    while BOT_ACTIVO:
        try:
            if not verificar_token(): time.sleep(3); continue
            ts = tiempo_servidor()
            segundos = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1

            if OPERACIONES >= MAX_OPER:
                try:
                    saldo_final = round(requests.get(
                        "https://api.iqoption.com/v2/user/profile",
                        headers={"Authorization": TOKEN}, timeout=8
                    ).json().get("balance",0), 2)
                    enviar_telegram(f"✅ FIN\n📊 Operaciones: {OPERACIONES}\n💵 Saldo: ${saldo_final}")
                except: pass
                BOT_ACTIVO = False; break

            if vela_cerrada != ULTIMA_VELA:
                ULTIMA_VELA = vela_cerrada
                YA_EJECUTADO.clear()
                mejor_fuerza = 0; mejor_senal = None
                logger.info(f"🔍 Vela {vela_cerrada}")

                for activo in ACTIVOS:
                    df = obtener_velas(activo)
                    if df is None: continue
                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, tipo = senal
                        logger.info(f"🎯 {activo} → {dirr} | {fuerza}% | {tipo}")
                        if fuerza >= FUERZA_MIN and fuerza > mejor_fuerza:
                            mejor_fuerza = fuerza; mejor_senal = (activo, dirr, fuerza, tipo)

                if mejor_senal:
                    a,d,f,t = mejor_senal
                    enviar_telegram(f"📊 REVERSIÓN\n📌 {a}\n➡️ {d.upper()}\n💪 {f}%\n📍 {t}")

            if mejor_senal and SEG_INICIO <= segundos <= SEG_FIN:
                a,d,f,t = mejor_senal
                ok,id_op,saldo = ejecutar_orden(a,d,vela_actual)
                if ok:
                    OPERACIONES += 1
                    enviar_telegram(f"✅ OPERACIÓN\n📌 {a} | {d.upper()}\n🆔 {id_op}\n💵 ${saldo}")
                mejor_senal = None

            time.sleep(0.5)
        except Exception as e: logger.error(f"💥 {e}"); verificar_token(); time.sleep(3)

if __name__ == "__main__":
    # Verificar variables antes de iniciar
    if not IQ_EMAIL or not IQ_PASSWORD:
        logger.critical("❌ Faltan variables: IQ_EMAIL_1 o IQ_PASSWORD_1")
        sys.exit(1)
    if obtener_token():
        bucle_principal()
    else:
        logger.critical("❌ No se pudo iniciar — revisa credenciales y conexión")
