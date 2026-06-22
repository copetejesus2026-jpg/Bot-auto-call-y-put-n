import os
import sys
import time
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

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

# ⚙️ CONFIGURACIÓN (editable en runtime)
PAR = "EURUSD"
TIMEFRAME = 60
CANTIDAD = 1
EXPIRACION = 1

Iq = None

# 🔌 CONEXIÓN SEGURA IQ OPTION
def conectar_iq():
    try:
        print("🔌 Conectando a IQ Option...")

        iq = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
        iq.connect()

        if not iq.check_connect():
            print("❌ No se pudo conectar a IQ Option")
            return None

        print("✅ Conectado a IQ Option")
        return iq

    except Exception as e:
        print(f"❌ Error conexión IQ: {e}")
        return None

# 📊 OBTENER VELAS
def get_candles():
    global Iq
    candles = Iq.get_candles(PAR, TIMEFRAME, 50, time.time())

    df = pd.DataFrame(candles)
    df.rename(columns={"max": "high", "min": "low"}, inplace=True)

    return df

# 💰 EJECUTAR TRADE
def ejecutar_trade(direccion):
    global Iq
    try:
        status, trade_id = Iq.buy(
            CANTIDAD,
            PAR,
            direccion,
            EXPIRACION
        )

        return status, trade_id
    except Exception as e:
        print(f"❌ Error trade: {e}")
        return False, None

# 🤖 COMANDOS TELEGRAM

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot IQ Option listo 🚀")

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"⚙️ Configuración actual:\n\n"
        f"Par: {PAR}\n"
        f"Importe: ${CANTIDAD}\n"
        f"Expiración: {EXPIRACION} min"
    )

# 🔧 CAMBIAR IMPORTE
async def set_monto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CANTIDAD
    try:
        CANTIDAD = float(context.args[0])
        await update.message.reply_text(f"💰 Importe actualizado: ${CANTIDAD}")
    except:
        await update.message.reply_text("Uso correcto: /monto 5")

# 🔧 CAMBIAR EXPIRACIÓN
async def set_exp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global EXPIRACION
    try:
        EXPIRACION = int(context.args[0])
        await update.message.reply_text(f"⏱️ Expiración: {EXPIRACION} min")
    except:
        await update.message.reply_text("Uso correcto: /exp 1")

# 🔥 ANALIZAR + OPERAR
async def operar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global Iq

    try:
        if Iq is None or not Iq.check_connect():
            await update.message.reply_text("🔄 Reconectando IQ Option...")
            Iq = conectar_iq()

            if Iq is None:
                await update.message.reply_text("❌ No se pudo conectar a IQ Option")
                return

        await update.message.reply_text("🔎 Analizando mercado...")

        df = get_candles()
        signal = get_reversal_signal(df)

        if not signal:
            await update.message.reply_text("❌ Sin señal")
            return

        direccion, fuerza, tipo = signal

        await update.message.reply_text(
            f"📊 Señal detectada\n\n"
            f"Tipo: {tipo}\n"
            f"Dirección: {direccion.upper()}\n"
            f"Fuerza: {fuerza}%"
        )

        ok, trade_id = ejecutar_trade(direccion)

        if ok:
            await update.message.reply_text(f"✅ Trade ejecutado\nID: {trade_id}")
        else:
            await update.message.reply_text("❌ Error al ejecutar trade")

    except Exception as e:
        print(f"ERROR OPERAR: {e}")
        await update.message.reply_text(f"⚠️ Error: {e}")

# 🚀 MAIN
def main():
    global Iq

    print("🚀 Iniciando bot...")

    # 🔌 Primera conexión
    Iq = conectar_iq()

    if Iq is None:
        print("❌ Falló conexión inicial, reintentando en 10s...")
        time.sleep(10)
        Iq = conectar_iq()

    if Iq is None:
        print("❌ No se pudo conectar a IQ Option. Cerrando.")
        sys.exit(1)

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("config", config))
    app.add_handler(CommandHandler("monto", set_monto))
    app.add_handler(CommandHandler("exp", set_exp))
    app.add_handler(CommandHandler("operar", operar))

    print("✅ Bot corriendo...")
    app.run_polling()

if __name__ == "__main__":
    main()
