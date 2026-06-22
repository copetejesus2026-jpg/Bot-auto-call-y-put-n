import os
import sys
import time
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from iqoptionapi.stable_api import IQ_Option
from strategy import get_reversal_signal

# 🔐 Variables de entorno
TOKEN = os.getenv("BOT_TOKEN")
IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")

if not TOKEN:
    print("❌ Falta BOT_TOKEN")
    sys.exit(1)

if not IQ_EMAIL or not IQ_PASSWORD:
    print("❌ Falta IQ_EMAIL o IQ_PASSWORD")
    sys.exit(1)

# 🔌 Conexión IQ Option
Iq = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
Iq.connect()

if not Iq.check_connect():
    print("❌ Error conectando a IQ Option")
    sys.exit(1)

print("✅ Conectado a IQ Option")

# 📊 Obtener velas
def get_candles(par="EURUSD", timeframe=60, num=50):
    candles = Iq.get_candles(par, timeframe, num, time.time())

    df = pd.DataFrame(candles)
    df.rename(columns={
        "max": "high",
        "min": "low"
    }, inplace=True)

    return df

# 🤖 COMANDOS TELEGRAM

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot IQ Option activo 🚀")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong!")

async def señal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        df = get_candles()

        signal = get_reversal_signal(df)

        if signal:
            direccion, fuerza, tipo = signal
            await update.message.reply_text(
                f"📊 Señal detectada\n\n"
                f"📈 Tipo: {tipo}\n"
                f"📊 Dirección: {direccion.upper()}\n"
                f"💪 Fuerza: {fuerza}%"
            )
        else:
            await update.message.reply_text("❌ Sin señal en este momento")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

def main():
    print("🚀 Iniciando bot...")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("senal", señal))

    print("✅ Bot corriendo...")
    app.run_polling()

if __name__ == "__main__":
    main()
