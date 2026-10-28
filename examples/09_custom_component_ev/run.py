"""Example 09 - Tutorial: write your own component (a home EV charger).

This example walks through everything a custom component can do:

* declare inputs/outputs as class attributes,
* keep internal state between steps,
* use ``ctx.rng`` for reproducible randomness (same seed -> same run),
* use ``ctx.log`` to write structured events to the run's event log,
* use ``ctx.step_size`` for the component's own time step.

The charger is then plugged into a house + PV + time-of-use tariff, and
"plug-and-charge" is compared with "smart" (off-peak) charging.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pandas as pd  # noqa: E402

import pycosim as pc  # noqa: E402
from pycosim.components import EPWWeather, GridMeter, RCBuilding, TimeOfUseTariff  # noqa: E402
from _common import TUCSON_EPW  # noqa: E402


class EVCharger(pc.Component):
    """A home EV charger with stochastic arrivals and daily driving needs.

    Inputs:  ``price`` [$/kWh] (used by smart charging)
    Outputs: ``P_ev`` [W], ``soc`` [-], ``connected`` [0/1]
    """

    inputs = {"price": 0.10}
    outputs = {"P_ev": 0.0, "soc": 0.8, "connected": 0}

    def __init__(self, name, battery_kwh=60.0, charger_kw=7.2, smart=False, price_threshold=0.15,
                 arrival_hour=(16.5, 19.5), departure_hour=(6.5, 8.0), daily_km=(15, 90), kwh_per_km=0.18):
        super().__init__(name)
        self.capacity = battery_kwh
        self.charger_w = charger_kw * 1000
        self.smart = smart
        self.price_threshold = price_threshold
        self.arrival_hour, self.departure_hour = arrival_hour, departure_hour
        self.daily_km, self.kwh_per_km = daily_km, kwh_per_km
        self.output_ports["P_ev"].unit = "W"

    def setup(self, ctx):
        # Called once before the first step: initialise state here.
        self.soc = 0.8
        self.connected = True  # the car is home at midnight
        self._plan_day(ctx.start)

    def _plan_day(self, day):
        rng = self.ctx.rng  # reproducible random numbers
        self.depart = day.replace(hour=0, minute=0) + pd.Timedelta(hours=rng.uniform(*self.departure_hour))
        self.arrive = day.replace(hour=0, minute=0) + pd.Timedelta(hours=rng.uniform(*self.arrival_hour))
        self.trip_kwh = rng.uniform(*self.daily_km) * self.kwh_per_km
        self.planned_for = day.date()

    def step(self, t, inputs):
        if t.date() != self.planned_for:
            self._plan_day(t)
        if self.connected and self.depart <= t < self.arrive:
            self.connected = False
            self.ctx.log(f"EV departs with SOC {self.soc:.2f}", kind="ev", soc=self.soc)
        elif not self.connected and t >= self.arrive:
            self.soc = max(self.soc - self.trip_kwh / self.capacity, 0.0)
            self.connected = True
            self.ctx.log(f"EV arrives, SOC {self.soc:.2f} after {self.trip_kwh:.1f} kWh trip", kind="ev",
                         soc=self.soc)
        power = 0.0
        if self.connected and self.soc < 1.0:
            allowed = not self.smart or (inputs.price is not None and inputs.price <= self.price_threshold)
            if allowed:
                dt_h = self.ctx.step_size / 3600
                power = min(self.charger_w, (1.0 - self.soc) * self.capacity * 1000 / dt_h)
                self.soc += power * dt_h / 1000 / self.capacity
        return {"P_ev": power, "soc": self.soc, "connected": int(self.connected)}


def run(smart: bool, days: int) -> pc.Results:
    start = pd.Timestamp("2023-07-17")
    sim = pc.CoSimulation(start, start + pd.Timedelta(days=days), "15min", name=f"09_{'smart' if smart else 'plug'}",
                          output_dir=HERE / "output", seed=42, log_level="WARNING")
    sim.add(EPWWeather("weather", TUCSON_EPW), RCBuilding("house", floor_area=160),
            TimeOfUseTariff("tariff", peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.08),
            EVCharger("ev", smart=smart), GridMeter("meter"))
    sim.connect("weather.T_out", "house.T_out")
    sim.connect("weather.GHI", "house.GHI")
    sim.connect("tariff.price", "ev.price")
    sim.connect(["house.P_elec", "ev.P_ev"], "meter.P_net", reduce="sum")
    sim.connect("tariff.price", "meter.price")
    return sim.run()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    rows = {}
    for smart in (False, True):
        res = run(smart, args.days)
        rows["smart" if smart else "plug-and-charge"] = {
            "EV energy kWh": res["ev.P_ev"].sum() * 0.25 / 1000,
            "household cost $": res["meter.cost"].iloc[-1],
            "peak kW": res["meter.peak"].iloc[-1],
            "min EV SOC": res["ev.soc"].min(),
        }
        print(res.events[res.events.kind == "ev"][["time", "message"]].head(4).to_string(index=False))
    print(pd.DataFrame(rows).T.round(2).to_string())


if __name__ == "__main__":
    main()
