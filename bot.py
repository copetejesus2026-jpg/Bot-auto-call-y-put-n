import os
import sys
import time
import pandas as pd

from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

from iqoptionapi.stable_api import IQ_Option
from strategy import get_reversal_signal

# 🔐 VARIABLES
TOKEN = os.getenv("BOT_TOKEN")
IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")

if not TOKEN:
    print("❌ Falta BOT_TOKEN")
    sys.exit(1)

if not IQ_EMAIL or not IQ_PASSWORD:
    print("❌ Falta IQ_EMAIL o IQ_PASSWORD")
    sys.exit(1)

# ⚙️ CONFIG
PAR = "EURUSD"
TIMEFRAME = 60
CANTIDAD = 1
EXPIRACION = 1

Iq = None

# 🔌 CONEXIÓN IQ OPTION
def conectar_iq():
    try:
        print("🔌 Conectando a IQ Option...")
        iq = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
        iq.connect()

        if not iq.check_connect():
            print("❌ No se pudo conectar")
            return None

        print("✅ Conectado a IQ Option")
        return iq

    except Exception as e:
        print(f"❌ Error IQ: {e}")
        return None

# 📊 OBTENER DATOS
def get_candles():
    candles = Iq.get_candles(PAR, TIMEFRAME, 50, time.time())
    df = pd.DataFrame(candles)
    df.rename(columns={"max": "high", "min": "low"}, inplace=True)
    return df

# 💰 EJECUTAR TRADE
def ejecutar_trade(direccion):
    try:
        status, trade_id = Iq.buy(CANTIDAD, PAR, direccion, EXPIRACION)
        return status, trade_id
    except Exception as e:
        print(f"Error trade: {e}")
        return False, None

# 🤖 COMANDOS TELEGRAM

def start(update: Update, context: CallbackContext):
    update.message.reply_text("🤖 Bot activo en Railway 🚀")

def operar(update: Update, context: CallbackContext):
    global Iq

    if Iq is None or not Iq.check_connect():
        Iq = conectar_iq()
        if Iq is None:
            update.message.reply_text("❌ Error conexión IQ")
            return

    df = get_candles()
    signal = get_reversal_signal(df)

    if not signal:
        update.message.reply_text("❌ Sin señal")
        return

    direccion, fuerza, tipo = signal

    ok, trade_id = ejecutar_trade(direccion)

    if ok:
        update.message.reply_text(
            f"✅ Trade ejecutado\nPar: {PAR}\nDirección: {direccion}\nFuerza: {fuerza}\nID: {trade_id}"
        )
    else:
        update.message.reply_text("❌ Error al ejecutar trade")

# 🚀 MAIN
def main():
    global Iq

    print("🚀 Iniciando bot...")

    Iq = conectar_iq()

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("operar", operar))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
