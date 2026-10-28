"""HELICS federate: energy meter with hand-coded interval alignment.

usage: python meter_fed_aligned.py [config.json] [result.json]

The power received at t is the mean over [t - 15 min, t) (EnergyPlus reports step averages),
while the price received at t applies from t on. The meter therefore keeps the price of the
previous step and pairs it with the power of the step that just ended (no price is known
before the start, so the first, pre-start interval is not charged -- as in Py-CoSim).
"""
import json
import sys
from pathlib import Path

import helics as h

HERE = Path(__file__).resolve().parent
config = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "meter.json")
result = sys.argv[2] if len(sys.argv) > 2 else "meter_result.json"
END, STEP = 7 * 86400.0, 900.0

fed = h.helicsCreateValueFederateFromConfig(config)
sub_power = h.helicsFederateGetInputByTarget(fed, "building/P_facility")
sub_price = h.helicsFederateGetInputByTarget(fed, "controller/price")
h.helicsFederateEnterExecutingMode(fed)
t, energy, cost, steps = 0.0, 0.0, 0.0, []
price_before = 0.0  # price that applied during the step that just ended
while True:
    power, price = h.helicsInputGetDouble(sub_power), h.helicsInputGetDouble(sub_price)
    energy += power * STEP / 3.6e6
    cost += power * price_before * STEP / 3.6e6
    steps.append((t, power, price_before))
    price_before = price
    if t + STEP >= END:
        break
    t = h.helicsFederateRequestTime(fed, t + STEP)
with open(result, "w") as fh:
    json.dump({"energy_kWh": energy, "cost_usd": cost, "steps": steps}, fh)
h.helicsFederateDisconnect(fed)
h.helicsFederateFree(fed)
h.helicsCloseLibrary()
