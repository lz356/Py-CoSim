"""Is Py-CoSim's overhead per run (fixed) or per exchanged step?

Runs the small office from the EnergyPlus command line and through Py-CoSim (worker
process, Python controller actuating every step) for 1, 7 and 28 days, three times
each, and fits wall = a + b * steps for both. The difference of the intercepts is the
fixed cost, the difference of the slopes the cost per exchanged step.
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from common import OUT, SMALL_OFFICE, TUCSON_EPW, run_energyplus_cli

import pycosim as pc
from pycosim.energyplus import EnergyPlusModel

WORK = OUT / "overhead"
START = pd.Timestamp("2023-07-03")


def cli_run(days: int, rep: int) -> float:
    m = EnergyPlusModel.load(SMALL_OFFICE)
    m.remove_daylight_saving()
    m.set_timesteps_per_hour(4)
    m.set_run_period(START.date(), (START + pd.Timedelta(days=days - 1)).date())
    path = m.save(WORK / f"cli_{days}_{rep}" / "in.epJSON")
    return run_energyplus_cli(path, WORK / f"cli_{days}_{rep}")


def pycosim_run(days: int, rep: int) -> float:
    sim = pc.CoSimulation(START, START + pd.Timedelta(days=days), "15min", name=f"py_{days}_{rep}",
                          output_dir=WORK, save=False, log_level="WARNING")
    sim.add(pc.EnergyPlusComponent("b", SMALL_OFFICE, TUCSON_EPW, daylight_saving=False,
                                   sensors={"T": ("Zone Mean Air Temperature", "Core_ZN")},
                                   actuators={"sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")}))
    sim.add(pc.component(lambda t: 24.0, name="ctrl", outputs="sp"))
    sim.connect("ctrl.sp", "b.sp")
    t0 = time.perf_counter()
    sim.run()
    return time.perf_counter() - t0


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    rows = []
    for rep in range(3):
        for days in (1, 7, 28):
            rows.append({"days": days, "steps": days * 96, "rep": rep, "mode": "cli", "wall_s": cli_run(days, rep)})
            rows.append({"days": days, "steps": days * 96, "rep": rep, "mode": "pycosim",
                         "wall_s": pycosim_run(days, rep)})
            print(rows[-2], rows[-1], flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "overhead_breakdown.csv", index=False)
    fit = {}
    for mode, sub in df.groupby("mode"):
        b, a = np.polyfit(sub.steps, sub.wall_s, 1)
        fit[mode] = {"intercept_s": a, "per_step_ms": b * 1000}
    fit["fixed_overhead_s"] = fit["pycosim"]["intercept_s"] - fit["cli"]["intercept_s"]
    fit["per_step_overhead_ms"] = fit["pycosim"]["per_step_ms"] - fit["cli"]["per_step_ms"]
    (OUT / "overhead_breakdown.json").write_text(json.dumps(fit, indent=2))
    print(json.dumps(fit, indent=2))
    print(df.groupby(["mode", "days"])["wall_s"].median())


if __name__ == "__main__":
    main()
