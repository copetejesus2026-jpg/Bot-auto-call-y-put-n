import os
import sys
import time
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from iqoptionapi.stable_api import IQ_Option
from strategy import get_reversal_signal

TOKEN = os.getenv("BOT_TOKEN")
IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")

if not TOKEN:
    print("❌ Falta BOT_TOKEN")
    sys.exit(1)

if not IQ_EMAIL or not IQ_PASSWORD:
    print("❌ Falta IQ_EMAIL o IQ_PASSWORD")
    sys.exit(1)

PAR = "EURUSD"
TIMEFRAME = 60
CANTIDAD = 1
EXPIRACION = 1

Iq = None

def conectar_iq():
    try:
        print("🔌 Conectando a IQ Option...")
        iq = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
        iq.connect()

        if not iq.check_connect():
            return None

        print("✅ Conectado a IQ Option")
        return iq
    except Exception as e:
        print(f"Error IQ: {e}")
        return None

def get_candles():
    candles = Iq.get_candles(PAR, TIMEFRAME, 50, time.time())
    df = pd.DataFrame(candles)
    df.rename(columns={"max": "high", "min": "low"}, inplace=True)
    return df

def ejecutar_trade(direccion):
    try:
        status, trade_id = Iq.buy(CANTIDAD, PAR, direccion, EXPIRACION)
        return status, trade_id
    except:
        return False, None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot activo 🚀")

async def operar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global Iq

    if Iq is None or not Iq.check_connect():
        Iq = conectar_iq()
        if Iq is None:
            await update.message.reply_text("❌ Error conexión IQ")
            return

    df = get_candles()
    signal = get_reversal_signal(df)

    if not signal:
        await update.message.reply_text("❌ Sin señal")
        return

    direccion, fuerza, tipo = signal

    ok, trade_id = ejecutar_trade(direccion)

    if ok:
        await update.message.reply_text(f"✅ Trade ejecutado ID: {trade_id}")
    else:
        await update.message.reply_text("❌ Error trade")

def main():
    global Iq

    print("🚀 Iniciando bot...")

    Iq = conectar_iq()
    if Iq is None:
        time.sleep(5)
        Iq = conectar_iq()

    if Iq is None:
        sys.exit(1)

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("operar", operar))

    app.run_polling()

if __name__ == "__main__":
    main()
