"""E2c: sensitivity of standalone EnergyPlus to its own time step (no co-simulation involved).

Runs the small office with the pre-cooling schedule hard-coded as an EnergyPlus
schedule, from the EnergyPlus command line, with 60, 12, 4, 2 and 1 time steps
per hour, and reports the facility electricity of the week. This checks that
the step sensitivity seen in E2b is a property of EnergyPlus, not of the
co-simulation.

Output: results/e2c_timestep_cli.csv
"""
from __future__ import annotations

import pandas as pd

from common import OUT, fix_sizing_window, read_sql_series, run_energyplus_cli
from verification import PRECOOL_SCHEDULE, base_model

WORK = OUT / "e2c"


def cooling_capacity(eio) -> float:
    """Sum of the autosized gross rated total cooling capacities [kW] reported in eplusout.eio."""
    total = 0.0
    for line in open(eio, encoding="utf-8", errors="replace"):
        if "Component Sizing Information" in line and "gross_rated_total_cooling_capacity" in line:
            total += float(line.strip().split(",")[-1])
    return total / 1000.0


def main() -> None:
    rows = []
    for sizing in ("default", "fixed-90min"):
        for ts in (60, 12, 4, 2, 1):
            m = base_model()
            m.set_timesteps_per_hour(ts)
            if sizing != "default":
                fix_sizing_window(m, ts)
            sched = m.get("Schedule:Compact", "CLGSETP_SCH")
            sched["data"] = [{"field": v} for v in PRECOOL_SCHEDULE]
            folder = WORK / f"{sizing}_ts{ts}"
            path = m.save(folder / "in.epJSON")
            wall = run_energyplus_cli(path, folder)
            df = read_sql_series(folder / "eplusout.sql", 2023)
            col = [c for c in df.columns if c.startswith("Electricity:Facility")][0]
            kwh = float(df[col].sum() / 3.6e6)
            rows.append({"sizing": sizing, "timesteps_per_hour": ts, "facility_kWh": kwh,
                         "total_cooling_capacity_kW": cooling_capacity(folder / "eplusout.eio"), "wall_s": wall})
            print(rows[-1], flush=True)
    out = pd.DataFrame(rows)
    out["diff_vs_60_pct"] = [
        100 * (r.facility_kWh / out.loc[(out["sizing"] == r.sizing) & (out["timesteps_per_hour"] == 60),
                                        "facility_kWh"].iloc[0] - 1) for r in out.itertuples()]
    out.to_csv(OUT / "e2c_timestep_cli.csv", index=False)
    print(out.round(3).to_string())


if __name__ == "__main__":
    main()
