import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import logging

from iqoptionapi.stable_api import IQ_Option

# ================= CONFIG =================

logging.getLogger().setLevel(logging.CRITICAL)
sys.stderr = open(os.devnull, 'w')

EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME = 60
EXPIRATION = 1
BASE_AMOUNT = 2000
MAX_LOSS_STREAK = 3

PAIRS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

# ================= ESTADO =================

trade_open = False
last_trade_time = 0
last_trade_candle = None
loss_streak = 0
BOT_RUNNING = True
LAST_UPDATE_ID = None

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

iq = IQ_Option(EMAIL, PASSWORD)
iq.connect()

if not iq.check_connect():
    print("❌ Error conectando a IQ Option")
    exit()

iq.change_balance("PRACTICE")

print("🔥 BOT PRO ACTIVO")
send("🔥 BOT PRO ACTIVO")


# ================= INDICADORES =================

def indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift()),
            abs(df["low"] - df["close"].shift())
        )
    )

    df["atr"] = df["tr"].rolling(14).mean()
    return df


# ================= CANDLES =================

def get_candles(pair, tf):
    try:
        data = iq.get_candles(pair, tf, 100, time.time())

        df = pd.DataFrame(data)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)

        return indicators(df)
    except:
        return None


# ================= ESTRATEGIA =================

def sniper_pro(df_m1, df_m5):

    # USAR VELAS CERRADAS
    last = df_m1.iloc[-2]
    prev = df_m1.iloc[-3]

    trend_up = df_m5.iloc[-2]["ema20"] > df_m5.iloc[-2]["ema50"]
    trend_down = df_m5.iloc[-2]["ema20"] < df_m5.iloc[-2]["ema50"]

    if last["atr"] < df_m1["atr"].mean():
        return None

    body = abs(last["close"] - last["open"])
    range_ = last["high"] - last["low"]

    if range_ == 0:
        return None

    strength = body / range_

    # PUT
    if (
        prev["close"] > prev["open"] and
        last["close"] < last["open"] and
        strength > 0.7 and
        last["close"] < prev["low"] and
        trend_down
    ):
        return "put"

    # CALL
    if (
        prev["close"] < prev["open"] and
        last["close"] > last["open"] and
        strength > 0.7 and
        last["close"] > prev["high"] and
        trend_up
    ):
        return "call"

    return None


# ================= ESPERAR APERTURA EXACTA =================

def wait_candle_open():
    while True:
        server_time = iq.get_server_timestamp()
        seconds = int(server_time) % 60
        milliseconds = server_time - int(server_time)

        if seconds == 0 and milliseconds < 0.15:
            return

        time.sleep(0.02)


# ================= TRADE =================

def trade(pair, direction):
    global trade_open, last_trade_time

    try:
        wait_candle_open()

        status, trade_id = iq.buy(
            BASE_AMOUNT,
            pair,
            direction,
            EXPIRATION
        )

        if status:
            trade_open = True
            last_trade_time = time.time()

            msg = f"🎯 {pair} {direction.upper()}"
            print(msg)
            send(msg)
    except Exception as e:
        print("Trade error:", e)


# ================= RESULTADO =================

def check_result():
    global trade_open

    try:
        if not trade_open:
            return

        if time.time() - last_trade_time < 65:
            return

        balance = iq.get_balance()
        trade_open = False

    except:
        trade_open = False


# ================= LOOP PRINCIPAL =================

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

        if last_trade_candle == current_candle:
            time.sleep(0.2)
            continue

        for pair in PAIRS:
            df_m1 = get_candles(pair, 60)
            df_m5 = get_candles(pair, 300)

            if df_m1 is None or df_m5 is None:
                continue

            signal = sniper_pro(df_m1, df_m5)

            if signal:
                if loss_streak >= MAX_LOSS_STREAK:
                    send("🛑 STOP POR RACHAS")
                    time.sleep(120)
                    loss_streak = 0
                    break

                trade(pair, signal)
                last_trade_candle = current_candle
                break

        time.sleep(0.2)

    except Exception as e:
        print("Error:", e)
        time.sleep(1)
