"""HELICS federate: pre-cooling set-point schedule and time-of-use tariff.

usage: python controller_fed.py [config.json]

Publishes, at every 15-min time t, the cooling set-point and the electricity price that
apply from t on (the same rules as Py-CoSim's PrecoolingController and TimeOfUseTariff).
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import helics as h

HERE = Path(__file__).resolve().parent
config = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "controller.json")
START, END, STEP = datetime(2023, 7, 17), datetime(2023, 7, 24), 900.0


def setpoint(t):
    hour = t.hour + t.minute / 60
    if t.weekday() < 5 and 13 <= hour < 16:
        return 22.0  # pre-cooling
    if t.weekday() < 5 and 16 <= hour < 21:
        return 26.7  # on-peak set-up
    return 24.0


def price(t):
    return 0.30 if t.weekday() < 5 and 16 <= t.hour < 21 else 0.10


fed = h.helicsCreateValueFederateFromConfig(config)
pub_sp = h.helicsFederateGetPublication(fed, "controller/clg_sp")
pub_price = h.helicsFederateGetPublication(fed, "controller/price")
h.helicsFederateEnterInitializingMode(fed)
h.helicsPublicationPublishDouble(pub_sp, setpoint(START))
h.helicsPublicationPublishDouble(pub_price, price(START))
h.helicsFederateEnterExecutingMode(fed)
t = 0.0
while t + STEP < (END - START).total_seconds():
    t = h.helicsFederateRequestTime(fed, t + STEP)
    now = START + timedelta(seconds=t)
    h.helicsPublicationPublishDouble(pub_sp, setpoint(now))
    h.helicsPublicationPublishDouble(pub_price, price(now))
h.helicsFederateDisconnect(fed)
h.helicsFederateFree(fed)
h.helicsCloseLibrary()
