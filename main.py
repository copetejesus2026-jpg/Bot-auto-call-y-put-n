from iqoptionapi.stable_api import IQ_Option
import time, logging, math, telebot
from datetime import datetime

# ------------------ CONFIGURACIÓN ------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("bot_logs.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# CUENTAS (2 cuentas separadas)
ACCOUNTS = [
    {"email": "cuenta1@correo.com", "pass": "clave1", "alias": "CUENTA-1", "tipo": "demo"},
    {"email": "cuenta2@correo.com", "pass": "clave2", "alias": "CUENTA-2", "tipo": "demo"}
]
# ACTIVOS A OPERAR — usa pares con buena liquidez
ASSETS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
TIMEFRAME = 60       # 1min
EXPIRY = 1           # min
MONTO = 1.0          # por operación
PAGO_MIN = 70        # % mínimo aceptable
# INDICADORES
RSI_PERIOD = 7
RSI_SOBRE = 75
RSI_SOBREV = 25
ADX_PERIOD = 14
ADX_FUERTE = 28      # >28 = tendencia fuerte; <25 = rango
# FILTRO VELAS AGOTAMIENTO
MAX_VELA_RANGO = 0.008   # % máximo cuerpo/sombra
MIN_RANGO = 0.001
# TELEGRAM
TELEGRAM_TOKEN = "TU_TOKEN_BOT"
TELEGRAM_CHAT_ID = "TU_ID_CHAT"
bot_tg = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML") if TELEGRAM_TOKEN else None
# -----------------------------------------------------

def notificar(texto):
    logger.info(texto)
    if bot_tg:
        try: bot_tg.send_message(TELEGRAM_CHAT_ID, texto[:4000])
        except Exception as e: logger.warning(f"Telegram error: {e}")

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
    # Vela muy grande o muy pequeña = agotamiento/ruido
    rango = (vela["max"] - vela["min"])/vela["close"]
    cuerpo = abs(vela["close"] - vela["open"])/vela["close"]
    return rango > MAX_VELA_RANGO or rango < MIN_RANGO or cuerpo/rango < 0.15

def obtener_señal(api, activo):
    try:
        velas = api.get_candles(activo, TIMEFRAME, 30, time.time())
        if not velas or len(velas)<20: return None
        # Filtro velas agotamiento
        if any(es_agotamiento(v) for v in velas[-3:]): return None
        precios_cierre = [v["close"] for v in velas]
        rsi = calcular_rsi(precios_cierre, RSI_PERIOD)
        adx = calcular_adx(velas, ADX_PERIOD)
        ult = velas[-1]; ant = velas[-2]
        # Evitar señales en tendencia muy fuerte
        if adx > ADX_FUERTE: return None
        # CALL (subida)
        if rsi < RSI_SOBREV and ult["close"] > ult["open"] and ant["close"] < ant["open"]:
            return "call"
        # PUT (bajada)
        if rsi > RSI_SOBRE and ult["close"] < ult["open"] and ant["close"] > ant["open"]:
            return "put"
        return None
    except Exception as e:
        logger.error(f"Error señal {activo}: {e}")
        return None

def ciclo_cuenta(datos_cuenta):
    alias = datos_cuenta["alias"]
    api = IQ_Option(datos_cuenta["email"], datos_cuenta["pass"])
    ok, razon = api.connect()
    if not ok:
        notificar(f"❌ {alias} NO CONECTÓ: {razon}")
        return
    api.change_balance(datos_cuenta["tipo"])
    saldo = api.get_balance()
    notificar(f"✅ {alias} CONECTADO | Saldo: ${saldo:.2f}")
    sin_señales = 0
    while True:
        for activo in ASSETS:
            try:
                abierto = api.get_all_open_time()["turbo"].get(activo, {}).get("open", False)
                pago = api.get_payout(activo, "turbo")
                if not abierto or pago < PAGO_MIN: continue
                direccion = obtener_señal(api, activo)
                if direccion:
                    sin_señales = 0
                    ok_op, id_op = api.buy(MONTO, activo, direccion, EXPIRY)
                    if ok_op:
                        notificar(f"📈 {alias} | {activo} | {direccion.upper()} | Pago: {pago}% | Monto: ${MONTO}")
                        # Esperar resultado
                        res = api.check_win_v4(id_op, 10)
                        if res[1]>0: notificar(f"🟢 {alias} GANANCIA: +${res[1]:.2f}")
                        elif res[1]<0: notificar(f"🔴 {alias} PÉRDIDA: ${abs(res[1]):.2f}")
                        else: notificar(f"⚪ {alias} EMPATE")
                    else:
                        logger.warning(f"{alias} No pudo abrir operación {activo}")
                else:
                    sin_señales += 1
                # Reinicio log si 30min sin nada
                if sin_señales > 30:
                    logger.info(f"{alias} Sin señales recientes — continuando")
                    sin_señales = 0
                time.sleep(2)
            except Exception as e:
                logger.error(f"{alias} Error ciclo: {e}")
                time.sleep(5)
        time.sleep(10)

if __name__ == "__main__":
    notificar("🚀 BOT INICIADO — 2 CUENTAS + FILTRO AGOTAMIENTO")
    # Ejecutar cuentas en hilos separados
    import threading
    hilos = []
    for cta in ACCOUNTS:
        t = threading.Thread(target=ciclo_cuenta, args=(cta,), daemon=True)
        hilos.append(t)
        t.start()
    for t in hilos: t.join()

