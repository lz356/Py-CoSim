"""HELICS federate: energy meter, written the way HELICS value federates are usually written.

usage: python meter_fed.py [config.json] [result.json]

At every granted time it multiplies the latest facility power by the latest price.
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
while True:
    power, price = h.helicsInputGetDouble(sub_power), h.helicsInputGetDouble(sub_price)
    energy += power * STEP / 3.6e6
    cost += power * price * STEP / 3.6e6
    steps.append((t, power, price))
    if t + STEP >= END:
        break
    t = h.helicsFederateRequestTime(fed, t + STEP)
with open(result, "w") as fh:
    json.dump({"energy_kWh": energy, "cost_usd": cost, "steps": steps}, fh)
h.helicsFederateDisconnect(fed)
h.helicsFederateFree(fed)
h.helicsCloseLibrary()
