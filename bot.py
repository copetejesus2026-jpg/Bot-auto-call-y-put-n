import time
import os
import csv
import requests
import pandas as pd
import sys
import logging

from iqoptionapi.stable_api import IQ_Option
from strategy import add_indicators, pro_signal

# ================= CONFIG =================

logging.getLogger().setLevel(logging.CRITICAL)
sys.stderr = open(os.devnull, 'w')

EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

USE_DYNAMIC_STAKE = False
BASE_AMOUNT = 10000
RISK_PERCENT = 0.02
MAX_LOSS_STREAK = 3
TRADE_COOLDOWN = 90

PAIRS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC"
]

# ================= ESTADO =================

trade_open = False
last_trade_time = 0
last_trade_candle = None
last_trade_pair = None
loss_streak = 0
last_balance = None
BOT_RUNNING = True
LAST_UPDATE_ID = None
last_context = None

# ================= TELEGRAM =================

def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except:
        pass


def check_telegram():
    global BOT_RUNNING, LAST_UPDATE_ID

    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        params = {}

        if LAST_UPDATE_ID is not None:
            params["offset"] = LAST_UPDATE_ID

        res = requests.get(url, params=params, timeout=5).json()

        for update in res.get("result", []):
            LAST_UPDATE_ID = update["update_id"] + 1

            if "message" not in update:
                continue

            text = update["message"].get("text", "").lower()
            chat = str(update["message"]["chat"]["id"])

            if chat != str(CHAT_ID):
                continue

            if text == "/stop":
                BOT_RUNNING = False
                send("🛑 BOT DETENIDO")

            elif text == "/start":
                BOT_RUNNING = True
                send("🚀 BOT ACTIVADO")

    except:
        pass


# ================= IQ OPTION =================

def connect_iq():
    iq = IQ_Option(EMAIL, PASSWORD)
    iq.connect()

    if not iq.check_connect():
        raise Exception("Error conectando")

    iq.change_balance("PRACTICE")
    return iq


iq = connect_iq()
send("🔥 BOT MARKET CONTEXT ACTIVO")


# ================= CSV LOG =================

def log_trade(pair, direction, result, pnl, context):
    file_exists = os.path.exists("trades.csv")

    with open("trades.csv", "a", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "timestamp",
                "pair",
                "direction",
                "score",
                "structure",
                "result",
                "pnl"
            ])

        writer.writerow([
            int(time.time()),
            pair,
            direction,
            context["score"] if context else "",
            context["structure"] if context else "",
            result,
            pnl
        ])


# ================= STAKE =================

def get_trade_amount():
    if not USE_DYNAMIC_STAKE:
        return BASE_AMOUNT

    balance = iq.get_balance()
    return round(balance * RISK_PERCENT, 2)


# ================= CANDLES =================

def get_candles(pair, tf):
    try:
        data = iq.get_candles(pair, tf, 120, time.time())

        if not data:
            return None

        df = pd.DataFrame(data)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)

        return add_indicators(df)

    except:
        return None


# ================= TIMING =================

def wait_candle_open():
    while True:
        server_time = iq.get_server_timestamp()
        sec = int(server_time) % 60
        ms = server_time - int(server_time)

        if sec == 0 and ms < 0.30:
            return

        time.sleep(0.02)


# ================= TRADE =================

def trade(pair, direction, expiration, context):
    global trade_open, last_trade_time
    global last_balance, last_trade_pair, last_context

    try:
        wait_candle_open()

        amount = get_trade_amount()
        last_balance = iq.get_balance()

        status, trade_id = iq.buy(
            amount,
            pair,
            direction,
            expiration
        )

        if status:
            trade_open = True
            last_trade_time = time.time()
            last_trade_pair = pair
            last_context = context

            msg = (
                f"🎯 {pair} {direction.upper()} | {expiration}m\n"
                f"Score: {context['score']}\n"
                f"Structure: {context['structure']}"
            )

            print(msg)
            send(msg)

    except Exception as e:
        print("Trade error:", e)


# ================= RESULTADOS =================

def check_result():
    global trade_open, loss_streak

    if not trade_open:
        return

    if time.time() - last_trade_time < 140:
        return

    try:
        current_balance = iq.get_balance()
        pnl = current_balance - last_balance

        trade_open = False

        if pnl > 0:
            loss_streak = 0
            send(f"✅ WIN +{round(pnl,2)}")
            log_trade(last_trade_pair, "win", "WIN", pnl, last_context)

        elif pnl < 0:
            loss_streak += 1
            send(f"❌ LOSS {round(pnl,2)} | Racha {loss_streak}")
            log_trade(last_trade_pair, "loss", "LOSS", pnl, last_context)

        else:
            send("⚪ EMPATE")
            log_trade(last_trade_pair, "draw", "DRAW", pnl, last_context)

    except:
        trade_open = False


# ================= LOOP =================

while True:
    try:
        check_telegram()

        if not BOT_RUNNING:
            time.sleep(1)
            continue

        check_result()

        if trade_open:
            time.sleep(0.5)
            continue

        if time.time() - last_trade_time < TRADE_COOLDOWN:
            time.sleep(0.2)
            continue

        server_time = int(iq.get_server_timestamp())
        current_candle = server_time // 60

        if last_trade_candle == current_candle:
            time.sleep(0.2)
            continue

        for pair in PAIRS:
            df_m1 = get_candles(pair, 60)
            df_m5 = get_candles(pair, 300)

            if df_m1 is None or df_m5 is None:
                continue

            signal, expiration, context = pro_signal(df_m1, df_m5)

            if signal:
                if loss_streak >= MAX_LOSS_STREAK:
                    send("🛑 STOP POR RACHAS")
                    time.sleep(300)
                    loss_streak = 0
                    break

                trade(pair, signal, expiration, context)
                last_trade_candle = current_candle
                break

        time.sleep(0.2)

    except Exception as e:
        print("Error:", e)

        try:
            iq = connect_iq()
            send("♻️ Reconectado")
        except:
            pass

        time.sleep(2)
