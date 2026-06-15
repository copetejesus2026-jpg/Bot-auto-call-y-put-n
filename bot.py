import time
import os
import pandas as pd
import logging
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

logging.basicConfig(level=logging.INFO)

# CONFIG
BASE_AMOUNT = 600
EXPIRATION = 1
TIMEFRAME = 60
MIN_FORCE = 98

PARES = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC"
]

CUENTAS = []
SEÑAL = None
ULTIMA_VELA = None

# ==============================
# CONEXIÓN MULTI CUENTA
# ==============================
def connect_accounts():
    cuentas = []

    credenciales = [
        (os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1")),
        (os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2")),
    ]

    for email, password in credenciales:
        if not email or not password:
            continue

        iq = IQ_Option(email, password)
        iq.connect()

        if iq.check_connect():
            iq.change_balance("PRACTICE")
            print(f"✅ Conectado: {email}")
            cuentas.append(iq)
        else:
            print(f"❌ Error: {email}")

    return cuentas

# ==============================
# DATA
# ==============================
def get_df(iq, par):
    candles = iq.get_candles(par, TIMEFRAME, 50, time.time())
    df = pd.DataFrame(candles)

    df.rename(columns={"max": "high", "min": "low"}, inplace=True)
    return df

# ==============================
# EJECUTAR
# ==============================
def ejecutar(cuentas, par, direccion):
    for iq in cuentas:
        try:
            estado, _ = iq.buy(BASE_AMOUNT, par, direccion, EXPIRATION)

            if estado:
                print(f"✅ {direccion.upper()} {par}")
            else:
                print("❌ Error ejecución")

        except Exception as e:
            print(f"💥 Error cuenta: {e}")

# ==============================
# BOT PRINCIPAL
# ==============================
def run():
    global SEÑAL, ULTIMA_VELA, CUENTAS

    CUENTAS = connect_accounts()

    if not CUENTAS:
        print("❌ Sin cuentas")
        return

    print("🚀 BOT SNIPER SEGUNDO 58")

    while True:
        try:
            iq = CUENTAS[0]

            server_time = iq.get_server_timestamp()
            segundos = int(server_time % 60)
            vela = int(server_time // 60)

            # =========================
            # DETECTAR SEÑAL (ANTES)
            # =========================
            if segundos == 56:
                mejor = None

                for par in PARES:
                    df = get_df(iq, par)
                    resultado = get_reversal_signal(df)

                    if resultado:
                        direccion, fuerza, tipo = resultado

                        if fuerza >= MIN_FORCE:
                            mejor = (par, direccion, fuerza)

                if mejor:
                    SEÑAL = mejor
                    print(f"🔍 Señal: {mejor}")

            # =========================
            # EJECUTAR EXACTO EN 58
            # =========================
            if (
                SEÑAL
                and segundos == 58
                and vela != ULTIMA_VELA
            ):
                ULTIMA_VELA = vela

                par, direccion, fuerza = SEÑAL
                SEÑAL = None

                # 🔁 INVERTIDO
                direccion = "put" if direccion == "call" else "call"

                print(f"🎯 ENTRADA EXACTA 58 | {par} | {direccion}")

                ejecutar(CUENTAS, par, direccion)

            time.sleep(0.05)

        except Exception as e:
            print(f"💥 Error: {e}")
            time.sleep(2)
            CUENTAS = connect_accounts()

# ==============================
# START
# ==============================
if __name__ == "__main__":
    run()
