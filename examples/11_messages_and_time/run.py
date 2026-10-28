"""Example 11 - Messages and time: TIDE in one script (no EnergyPlus, runs in ~15 s).

Components never call each other: they publish *messages* and subscribe to
what they need. Each message says what its time stamp means, so TIDE (the
Time-aware Interval Data Exchange) can line up components that run
at different rates. This example shows the main features on a small house:

    weather --T_out, GHI--> house (RC model, 15 min) --T_zone, P_elec--> ai
    weather --irradiance--> pv (4 kW)
    tariff --price, published on the topic "grid/price"--> ai, meters
    ai (stochastic stand-in, hourly, asynchronous) --clg_sp--> house
                                                   --P_set---> battery
    meter (hourly)   <-- "*.P_elec", "*.P_ac", battery.P, "grid/price"
    meter15 (15 min) <-- the same subscriptions

* The "AI supervisor" is NOT an LLM. It is a stand-in with the properties of
  one that matter here: its answers are random (a new random stream in every
  run), it takes time to answer (time.sleep), and it sometimes answers
  nonsense (a 40 C set-point). It is made asynchronous with
  ``make_async(delay="15min")``: it sees the state at t and its answer applies
  at t + 15 min, however long it took to compute.
* The hourly meter reads the 15-minute house power, the instantaneous PV
  power and the battery power as exact means over its own hour (aligned
  reads), and agrees with a 15-minute meter.
* The AI's set-point has bounds: nonsense is clipped and flagged.
* The live run is recorded (``record=["ai"]``) and replayed: the replay is
  identical bit for bit, while a second live run is not. Replaying the same
  decisions on a house with more PV gives a counterfactual.
* ``trace=True`` lets us trace one battery decision back to its inputs.

Things to try:
* ``--latency 0.2`` (seconds per AI answer) -- the run takes longer, but every
  answer still applies exactly 15 simulated minutes after the question;
* ``--deadline 0.05`` -- answers that take longer than 0.05 s are replaced by
  the previous ones and flagged ``late``. Which ones are late now depends on
  the computer, yet the replay still reproduces the live run exactly;
* add a second ``RCBuilding`` -- the meters pick up its ``P_elec`` through the
  pattern ``"*.P_elec"`` without new connections.
"""
import argparse
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pandas as pd  # noqa: E402

import pycosim as pc  # noqa: E402
from pycosim.components import (  # noqa: E402
    Battery, EPWWeather, GridMeter, PVSystem, RCBuilding, TimeOfUseTariff,
)
from _common import TUCSON_EPW  # noqa: E402

OUTPUT = HERE / "output"
SOLAR = ["GHI", "DNI", "DHI", "T_out", "solar_zenith", "solar_azimuth"]


class StochasticSupervisor(pc.Component):
    """A stand-in for an AI supervisor (NOT a language model).

    Every hour it picks a cooling set-point at random from a few sensible
    options (pre-cool before the price peak, relax during it) and decides the
    battery power. It sleeps to imitate the response time of a remote model
    and, with probability ``p_bad``, returns an implausible 40 C set-point.
    It uses an *unseeded* random generator on purpose, so two live runs
    differ -- like two runs with a real LLM.
    """

    inputs = {"T_zone": 24.0, "price": 0.10, "soc": 0.5, "P_elec": 0.0}
    outputs = {
        # hold: a decision is valid from the moment it is published until the next one
        "clg_sp": {"default": 24.0, "unit": "C", "kind": "hold", "bounds": (22.0, 28.0)},
        "P_set": {"default": 0.0, "unit": "W", "kind": "hold", "bounds": (-5000.0, 5000.0)},
    }

    def __init__(self, name, latency=0.03, jitter=0.03, p_bad=0.05, step_size="1h"):
        super().__init__(name, step_size=step_size)
        self.latency, self.jitter, self.p_bad = latency, jitter, p_bad
        self.rand = random.Random()  # unseeded: a different stream in every run (do NOT do this in real models)

    def step(self, t, inputs):
        time.sleep(self.latency + self.rand.random() * self.jitter)  # "thinking"
        peak = inputs.price >= 0.2
        if peak:
            options = [25.5, 26.0, 26.5]          # relax during the price peak
        elif 13 <= t.hour < 16:
            options = [22.5, 23.0, 23.5]          # pre-cool before it
        else:
            options = [24.0, 24.5, 25.0]
        clg_sp = self.rand.choice(options)
        if self.rand.random() < self.p_bad:
            clg_sp = 40.0                         # nonsense: TIDE clips it to the bounds and flags it
        if peak:
            P_set = -inputs.P_elec                # discharge to cover the house
        elif t.hour < 6 and inputs.soc < 0.9:
            P_set = self.rand.choice([2000.0, 3000.0, 4000.0])  # charge at night
        else:
            P_set = 0.0
        return {"clg_sp": clg_sp, "P_set": P_set}


def build(name, days, pv_kw=4.0, latency=0.03, deadline=None, **sim_kwargs) -> pc.CoSimulation:
    start = pd.Timestamp("2023-07-17")  # a Monday
    sim = pc.CoSimulation(start, start + pd.Timedelta(days=days), "15min", name=name, output_dir=OUTPUT,
                          log_level="WARNING", **sim_kwargs)

    tariff = TimeOfUseTariff("tariff", peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.08)
    tariff.set_port("price", topic="grid/price")       # consumers subscribe to the topic, not to "tariff"

    ai = StochasticSupervisor("ai", latency=latency)
    ai.make_async(delay="15min", deadline=deadline)   # answer applies 15 simulated minutes later

    meter = GridMeter("meter", demand_window="1h")     # aligned=True (default): reads [t - 1 h, t)
    meter.step_size = "1h"
    meter15 = GridMeter("meter15")                     # the same at 15 minutes, for comparison

    sim.add(EPWWeather("weather", TUCSON_EPW), RCBuilding("house", floor_area=150), PVSystem("pv", capacity_kw=pv_kw),
            Battery("battery", capacity_kwh=13.5, max_charge_kw=5), tariff, ai, meter, meter15)

    sim.connect("weather.T_out", "house.T_out")
    sim.connect("weather.GHI", "house.GHI")
    sim.connect_matching("weather", "pv", ports=SOLAR)
    sim.subscribe("ai.T_zone", "house.T_zone")
    sim.subscribe("ai.P_elec", "house.P_elec")
    sim.subscribe("ai.price", "grid/price")            # a topic
    sim.subscribe("ai.soc", "battery.soc")
    sim.subscribe("house.clg_sp", "ai.clg_sp")
    sim.subscribe("battery.P_set", "ai.P_set")
    for m in ("meter", "meter15"):
        sim.subscribe(f"{m}.P_load", "*.P_elec", reduce="sum")  # every building, also ones added later
        sim.subscribe(f"{m}.P_gen", "*.P_ac", reduce="sum")     # every PV system
        sim.subscribe(f"{m}.P_bat", "battery.P")
        sim.subscribe(f"{m}.price", "grid/price")
    return sim


def net_energy(res, meter, t0, t1):
    """Net grid import [kWh] between two times, from a meter's cumulative outputs."""
    net = res[f"{meter}.E_import"] - res[f"{meter}.E_export"]
    return net[t1] - net[t0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--latency", type=float, default=0.03, help="seconds the AI stand-in 'thinks' per answer")
    parser.add_argument("--deadline", type=float, default=None, help="wall-clock seconds before an answer is late")
    args = parser.parse_args()
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 12)

    # 1. Live run, recorded. trace=True also keeps every message and every read (needed for lineage()).
    sim = build("11_live", args.days, latency=args.latency, deadline=args.deadline, record=["ai"], trace=True)
    print(sim.describe())
    live = sim.run(progress=True)
    print(live.summary())
    print("asynchronous AI:", live.meta["asynchronous"]["ai"])

    # 2. Alignment: the hourly meter reads 15-minute, instantaneous and battery power as exact hourly means.
    print("\nAligned reads (one row per input read with at/past/ahead):")
    print(live.alignment.to_string(index=False))
    t0, t1 = live.df.index[4], live.df.index[-4]  # first and last whole hour after the start
    print(f"Net grid import {t0:%m-%d %H:%M} -> {t1:%m-%d %H:%M}: hourly meter "
          f"{net_energy(live, 'meter', t0, t1):.3f} kWh, 15-min meter {net_energy(live, 'meter15', t0, t1):.3f} kWh")

    # 3. Quality: the AI's nonsense answers were clipped and flagged.
    print("\nMessage quality (ports with flagged messages):")
    print(live.quality.to_string(index=False))

    # 4. A second live run differs; a replay of the recording is identical.
    live2 = build("11_live2", args.days, latency=args.latency, deadline=args.deadline).run()
    sim = build("11_replay", args.days)
    sim.replay("ai", OUTPUT / "11_live")  # the run folder holds messages.jsonl
    replay = sim.run()
    identical = replay.df.equals(live.df)
    print(f"\nCost: live ${live['meter15.cost'].iloc[-1]:.3f}, second live run ${live2['meter15.cost'].iloc[-1]:.3f}, "
          f"replay ${replay['meter15.cost'].iloc[-1]:.3f} (replay identical to the live run: {identical})")
    pd.testing.assert_frame_equal(replay.df, live.df)
    print("Replay quality:", replay.quality.groupby("quality")["messages"].sum().to_dict())

    # 5. Counterfactual: exactly the same AI decisions, on a house with 8 kW of PV instead of 4 kW.
    sim = build("11_counterfactual", args.days, pv_kw=8.0)
    sim.replay("ai", live)                # a Results object works too
    cf = sim.run()
    assert cf["ai.clg_sp"].equals(live["ai.clg_sp"]) and cf["ai.P_set"].equals(live["ai.P_set"])
    print(f"Counterfactual (same decisions, 8 kW PV): cost ${cf['meter15.cost'].iloc[-1]:.3f}")

    # 6. Provenance: where did the battery power at 16:15 on the first day come from?
    when = live.df.index[0] + pd.Timedelta(hours=16, minutes=15)
    lin = live.lineage("battery.P", when, depth=3)
    print(f"\nLineage of battery.P at {when}:")
    print(lin[["level", "message", "time", "value", "via_input", "source", "source_time", "source_value"]]
          .to_string(index=False))
    print(f"\nResults in {OUTPUT}")


if __name__ == "__main__":
    main()
