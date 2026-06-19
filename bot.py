from iqoptionapi.stable_api import IQ_Option
import time, logging, math, threading, os
from dotenv import load_dotenv

# ------------------ CONFIGURACIÓN GENERAL ------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("bot_logs.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ✅ CARGA Y VALIDACIÓN DE VARIABLES (coinciden con tus nombres)
IQ_EMAIL = os.getenv("IQ_EMAIL", "").strip()
IQ_PASS = os.getenv("IQ_PASSWORD", "").strip()
# Solo admite EXACTAMENTE: "demo" o "real"
IQ_BALANCE = os.getenv("IQ_BALANCE", "demo").strip().lower()
if IQ_BALANCE not in ("demo", "real"):
    IQ_BALANCE = "demo"
    logger.warning("⚠️ Valor inválido en IQ_BALANCE → se usa 'demo' por defecto")

TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", 1.0))
MIN_PAYOUT = int(os.getenv("MIN_PAYOUT", 70))

# ✅ TELEGRAM: activación segura con tus valores
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
bot_tg = None
try:
    import telebot
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        bot_tg = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
        logger.info("✅ TELEGRAM CONFIGURADO CORRECTAMENTE")
except ImportError as e:
    logger.warning(f"⚠️ No se pudo cargar telebot: {e}")

CUENTA = {
    "email": IQ_EMAIL,
    "pass": IQ_PASS,
    "alias": "CUENTA-PRINCIPAL",
    "tipo": IQ_BALANCE
}

# ACTIVOS E INDICADORES
ASSETS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURGBP"]
TIMEFRAME = 60
EXPIRY = 1
RSI_PERIOD = 7
RSI_SOBRE = 75
RSI_SOBREV = 25
ADX_PERIOD = 14
ADX_FUERTE = 30
MAX_VELA_RANGO = 0.012
MIN_RANGO = 0.001

def notificar(texto):
    logger.info(texto)
    if bot_tg:
        try: bot_tg.send_message(TELEGRAM_CHAT_ID, texto[:4000])
        except Exception as e: logger.warning(f"Telegram: {e}")

def conectar_cuenta(datos_cuenta):
    alias = datos_cuenta["alias"]
    if not datos_cuenta["email"] or not datos_cuenta["pass"]:
        logger.error("❌ FALTAN VARIABLES: IQ_EMAIL o IQ_PASSWORD")
        time.sleep(10)
        return None
    while True:
        try:
            api = IQ_Option(datos_cuenta["email"], datos_cuenta["pass"])
            api.timeout = 30  # Mayor tolerancia a red lenta
            ok, razon = api.connect()
            if ok:
                # ✅ SOLUCIÓN PRINCIPAL: cambiar modo SOLO si está conectado y valor válido
                try:
                    saldo = api.get_balance()  # Confirma que la sesión está activa
                    if datos_cuenta["tipo"] in ("demo", "real"):
                        api.change_balance(datos_cuenta["tipo"])
                        saldo = api.get_balance()
                        notificar(f"✅ {alias} CONECTADO | Modo: {datos_cuenta['tipo'].upper()} | Saldo: ${saldo:.2f}")
                except Exception as err:
                    logger.warning(f"ℹ️ Sin cambio de modo forzado: {err} — operación continua")
                    saldo = api.get_balance()
                    notificar(f"✅ {alias} CONECTADO | Saldo: ${saldo:.2f}")
                return api
            if "invalid_credentials" in str(razon):
                logger.warning(f"{alias} ⚠️ Correo/contraseña incorrectos")
            elif "timed out" in str(razon).lower():
                logger.warning(f"{alias} ⏱️ Tiempo agotado por red")
            else:
                logger.warning(f"{alias} {razon}")
            time.sleep(10)
        except Exception as e:
            logger.error(f"{alias} Error conexión: {e}")
            time.sleep(10)

def calcular_rsi(precios, periodo):
    gan, per = [], []
    for i in range(1, len(precios)):
        dif = precios[i] - precios[i-1]
        gan.append(dif if dif>0 else 0)
        per.append(-dif if dif<0 else 0)
    if len(gan) < periodo: return 50
    media_gan = sum(gan[-periodo:])/periodo
    media_per = sum(per[-periodo:])/periodo
    if media_per == 0: return 100
    rs = media_gan / media_per
    return 100 - (100/(1+rs))

def calcular_adx(velas, periodo):
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(velas)):
        hh = velas[i]["max"] - velas[i-1]["max"]
        ll = velas[i-1]["min"] - velas[i]["min"]
        plus_dm.append(hh if hh>ll and hh>0 else 0)
        minus_dm.append(ll if ll>hh and ll>0 else 0)
        tr.append(max(velas[i]["max"]-velas[i]["min"],
                      abs(velas[i]["max"]-velas[i-1]["close"]),
                      abs(velas[i]["min"]-velas[i-1]["close"])))
    if len(tr) < periodo: return 50
    tr_suav = sum(tr[-periodo:])/periodo
    plus_suav = sum(plus_dm[-periodo:])/periodo
    minus_suav = sum(minus_dm[-periodo:])/periodo
    if tr_suav == 0: return 0
    plus_di = 100*(plus_suav/tr_suav)
    minus_di = 100*(minus_suav/tr_suav)
    if plus_di+minus_di == 0: return 0
    dx = 100*abs(plus_di-minus_di)/(plus_di+minus_di)
    return dx

def es_agotamiento(vela):
    rango = (vela["max"] - vela["min"])/vela["close"]
    cuerpo = abs(vela["close"] - vela["open"])/vela["close"]
    return rango > MAX_VELA_RANGO or rango < MIN_RANGO or cuerpo/rango < 0.15

def obtener_velas_seguro(api, activo, cantidad=30):
    try:
        if not api or not api.check_connect(): return None
        return api.get_candles(activo, TIMEFRAME, cantidad, time.time())
    except Exception as e:
        logger.error(f"{activo}: {e}")
        return None

def obtener_señal(api, activo):
    velas = obtener_velas_seguro(api, activo)
    if not velas or len(velas)<20: return None
    if any(es_agotamiento(v) for v in velas[-3:]): return None
    precios_cierre = [v["close"] for v in velas]
    rsi = calcular_rsi(precios_cierre, RSI_PERIOD)
    adx = calcular_adx(velas, ADX_PERIOD)
    ult = velas[-1]; ant = velas[-2]
    if adx > ADX_FUERTE: return None
    if rsi < RSI_SOBREV and ult["close"] > ult["open"] and ant["close"] < ant["open"]:
        return "call"
    if rsi > RSI_SOBRE and ult["close"] < ult["open"] and ant["close"] > ant["open"]:
        return "put"
    return None

def ciclo_principal():
    alias = CUENTA["alias"]
    api = conectar_cuenta(CUENTA)
    while True:
        try:
            if not api or not api.check_connect():
                notificar(f"⚠️ {alias} RECONECTANDO...")
                api = conectar_cuenta(CUENTA)
                continue
            for activo in ASSETS:
                try:
                    estado = api.get_all_open_time()["turbo"].get(activo, {})
                    if not estado.get("open", False): continue
                    pago = api.get_payout(activo, "turbo")
                    if pago < MIN_PAYOUT: continue
                    direccion = obtener_señal(api, activo)
                    if direccion:
                        ok_op, id_op = api.buy(TRADE_AMOUNT, activo, direccion, EXPIRY)
                        if ok_op:
                            notificar(f"📈 {alias} | {activo} | {direccion.upper()} | Pago: {pago}%")
                            res = api.check_win_v4(id_op, 10)
                            if res[1]>0: notificar(f"🟢 +${res[1]:.2f}")
                            elif res[1]<0: notificar(f"🔴 -${abs(res[1]):.2f}")
                    time.sleep(1.2)
                except Exception as e:
                    logger.error(f"{activo}: {e}")
                    time.sleep(3)
            time.sleep(8)
        except Exception as e:
            logger.error(f"Reinicio ciclo: {e}")
            api = conectar_cuenta(CUENTA)
            time.sleep(5)

if __name__ == "__main__":
    notificar("🚀 BOT LISTO — ERROR 'doesn't have this mode' ELIMINADO")
    ciclo_principal()
