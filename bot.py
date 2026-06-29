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
sys.stderr = open(os.devnull, "w")

EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_AMOUNT = 10
MAX_LOSS_STREAK = 3

PAIRS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC"
]

trade_open = False
last_trade_time = 0
loss_streak = 0
BOT_RUNNING = True
LAST_UPDATE_ID = None

last_balance = None
last_direction = None
last_pair = None
last_context = None

last_processed_candle = None


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


# ================= IQ =================

def connect_iq():
    iq = IQ_Option(EMAIL, PASSWORD)
    iq.connect()

    if not iq.check_connect():
        raise Exception("Error conectando IQ Option")

    iq.change_balance("PRACTICE")
    return iq


iq = connect_iq()
send("🔥 BOT PRICE ACTION OTC ACTIVO")


# ================= CSV =================

def log_trade(pair, direction, result, pnl, context):
    file_exists = os.path.exists("trades.csv")

    with open("trades.csv", "a", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "timestamp",
                "pair",
                "direction",
                "structure",
                "score",
                "result",
                "pnl"
            ])

        writer.writerow([
            int(time.time()),
            pair,
            direction,
            context.get("structure"),
            context.get("score"),
            result,
            pnl
        ])


# ================= DATA =================

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


# ================= TRADE =================

def wait_new_candle():
    while True:
        server_time = iq.get_server_timestamp()
        sec = int(server_time) % 60
        ms = server_time - int(server_time)

        if sec == 0 and ms < 0.20:
            return

        time.sleep(0.01)


def trade(pair, direction, expiration, context):
    global trade_open
    global last_trade_time
    global last_balance
    global last_direction
    global last_pair
    global last_context

    wait_new_candle()

    try:
        last_balance = iq.get_balance()

        status, trade_id = iq.buy(
            BASE_AMOUNT,
            pair,
            direction,
            expiration
        )

        if status:
            trade_open = True
            last_trade_time = time.time()
            last_direction = direction
            last_pair = pair
            last_context = context

            send(
                f"🎯 {pair} {direction.upper()} {expiration}m\n"
                f"Score: {context['score']}\n"
                f"Structure: {context['structure']}"
            )

    except Exception as e:
        print("Trade error:", e)


# ================= RESULT =================

def check_result():
    global trade_open
    global loss_streak

    if not trade_open:
        return

    if time.time() - last_trade_time < 80:
        return

    try:
        current_balance = iq.get_balance()
        pnl = current_balance - last_balance

        trade_open = False

        if pnl > 0:
            loss_streak = 0
            send(f"✅ WIN +{round(pnl,2)}")
            log_trade(last_pair, last_direction, "WIN", pnl, last_context)

        elif pnl < 0:
            loss_streak += 1
            send(f"❌ LOSS {round(pnl,2)} | Streak {loss_streak}")
            log_trade(last_pair, last_direction, "LOSS", pnl, last_context)

        else:
            send("⚪ DRAW")
            log_trade(last_pair, last_direction, "DRAW", pnl, last_context)

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

        server_time = int(iq.get_server_timestamp())
        current_candle = server_time // 60

        if current_candle == last_processed_candle:
            time.sleep(0.2)
            continue

        if server_time % 60 != 1:
            time.sleep(0.05)
            continue

        last_processed_candle = current_candle

        if loss_streak >= MAX_LOSS_STREAK:
            send("🛑 STOP POR RACHAS")
            time.sleep(300)
            loss_streak = 0
            continue

        for pair in PAIRS:
            df_m1 = get_candles(pair, 60)
            df_m5 = get_candles(pair, 300)

            if df_m1 is None or df_m5 is None:
                continue

            signal, expiration, context = pro_signal(df_m1, df_m5)

            if signal:
                trade(pair, signal, expiration, context)
                break

        time.sleep(0.2)

    except Exception as e:
        print("Loop error:", e)

        try:
            iq = connect_iq()
            send("♻️ RECONNECTED")
        except:
            pass

        time.sleep(2)
