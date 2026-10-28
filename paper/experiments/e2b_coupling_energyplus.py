"""E2b: coupling error of an EnergyPlus + PV + battery + tariff + meter co-simulation vs. a 1-minute reference.

The control actions are schedules (no feedback), so the physical system is the
same whatever the exchange step; differences against the 1-minute reference
are numerical. For exchange steps h of 5, 15, 30 and 60 minutes three
couplings are compared:

* ``naive``   -- Py-CoSim 2.0 style: every input reads the latest value; a
  helper function sums the flows into a net load; EnergyPlus runs at step h.
* ``aligned`` -- TIDE: the meter subscribes to each flow and the price
  with read="past"; EnergyPlus runs at step h.
* ``aligned+sub`` -- as ``aligned`` but EnergyPlus keeps a 1-minute internal
  step and exchanges every h (its outputs aggregated by time meaning).

Output: results/e2b_coupling.csv, results/e2b_series.csv, figures/fig06_coupling_energyplus.png
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from functools import partial

from common import COLORS, FIG, OUT, TUCSON_EPW, fix_sizing_window, make_office, set_style

import pycosim as pc
from pycosim.components import Battery, EPWWeather, GridMeter, PVSystem, TimeOfUseTariff

START, END = "2023-07-17", "2023-07-24"
WORK = OUT / "e2b"
TS_FOR_STEP = {1: 60, 5: 12, 15: 4, 30: 2, 60: 1}


def setpoint(t):
    if t.weekday() < 5 and 13 <= t.hour < 16:
        return 22.0
    if t.weekday() < 5 and 16 <= t.hour < 21:
        return 26.7
    return 24.0


def battery_command(t):
    if 10 <= t.hour < 14:
        return 20000.0       # charge from PV
    if t.weekday() < 5 and 16 <= t.hour < 20:
        return -25000.0      # discharge on peak
    return 0.0


def _sizing(ts, model):
    fix_sizing_window(model, ts)


def build(h: int, mode: str) -> pc.CoSimulation:
    step = f"{h}min"
    ts = 60 if mode == "aligned+sub" else TS_FOR_STEP[h]
    sim = pc.CoSimulation(START, END, step, name=f"{mode}_{h}min", output_dir=WORK, log_level="WARNING")
    # equipment is autosized with the same 90-min load-averaging window for every EnergyPlus step
    sim.add(make_office("office", timestep=ts, step_size=step, preprocess=partial(_sizing, ts)))
    sim.add(EPWWeather("weather", TUCSON_EPW))
    sim.add(PVSystem("pv", capacity_kw=30))
    sim.add(Battery("battery", capacity_kwh=100, max_charge_kw=25, soc_init=0.3))
    sim.add(TimeOfUseTariff("tariff"))
    sim.add(pc.FunctionComponent(setpoint, name="thermostat", outputs={"clg_sp": {"default": 24.0, "kind": "hold"}}))
    sim.add(pc.FunctionComponent(battery_command, name="dispatch",
                                 outputs={"P_set": {"default": 0.0, "kind": "hold"}}))
    sim.connect("thermostat.clg_sp", "office.clg_sp")
    sim.connect("dispatch.P_set", "battery.P_set")
    for f in ("GHI", "DNI", "DHI", "T_out", "solar_zenith", "solar_azimuth"):
        sim.connect(f"weather.{f}", f"pv.{f}")
    if mode == "naive":
        sim.add(GridMeter("grid", aligned=False, demand_window=f"{max(h, 15)}min"))
        sim.add(pc.FunctionComponent(lambda t, P_bldg=0.0, P_pv=0.0, P_bat=0.0: P_bldg - P_pv + P_bat,
                                     name="net", outputs="P_net"))
        sim.connect("office.P_facility", "net.P_bldg")
        sim.connect("pv.P_ac", "net.P_pv")
        sim.connect("battery.P", "net.P_bat")
        sim.connect("net.P_net", "grid.P_net")
    else:
        sim.add(GridMeter("grid", aligned=True, demand_window=f"{max(h, 15)}min"))
        sim.subscribe("grid.P_load", "office.P_facility")
        sim.subscribe("grid.P_gen", "pv.P_ac")
        sim.subscribe("grid.P_bat", "battery.P")
    sim.connect("tariff.price", "grid.price")
    return sim


def kpis(res: pc.Results, h: int) -> dict:
    df = res.df
    t_end = pd.Timestamp(END) - pd.Timedelta("60min")
    t0 = pd.Timestamp(START)
    a, b = df.loc[t0], df.loc[t_end]
    # building energy: the office reports the mean power of the step that ended at each step time
    bldg = df["office.P_facility"].loc[t0 + pd.Timedelta(f"{h}min"): t_end]
    return {
        "grid_import_kWh": float(b["grid.E_import"] - a["grid.E_import"]),
        "grid_export_kWh": float(b["grid.E_export"] - a["grid.E_export"]),
        "cost_usd": float(b["grid.cost"] - a["grid.cost"]),
        "building_kWh": float(bldg.sum() * h * 60 / 3.6e6),
        "peak_kW": float(df["grid.peak"].loc[:t_end].iloc[-1]),
    }


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    rows, series = [], {}
    variants = [(1, "aligned+sub")] + [(h, m) for h in (5, 15, 30, 60) for m in ("naive", "aligned", "aligned+sub")]
    for h, mode in variants:
        label = "reference" if h == 1 else mode
        t0 = time.perf_counter()
        res = build(h, mode).run()
        wall = time.perf_counter() - t0
        row = {"step_min": h, "mode": label, "wall_s": wall, **kpis(res, h)}
        if len(res.alignment):
            row["extrapolated_reads"] = int(res.alignment["extrapolated_reads"].sum())
        rows.append(row)
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}, flush=True)
        net = (res["grid.P_import"] - res["grid.P_export"]) / 1000.0
        series[f"{label}_{h}"] = net
    df = pd.DataFrame(rows)
    ref = df[df["mode"] == "reference"].iloc[0]
    for col in ("grid_import_kWh", "grid_export_kWh", "cost_usd", "building_kWh", "peak_kW"):
        df[f"{col}_err_pct"] = 100 * (df[col] / ref[col] - 1)
    df.to_csv(OUT / "e2b_coupling.csv", index=False)
    pd.DataFrame(series).to_csv(OUT / "e2b_series.csv")
    print(df.round(3).to_string())
    summary = {m: {f"{int(h)}min": {"cost_err_pct": float(g.loc[g["step_min"] == h, "cost_usd_err_pct"].iloc[0]),
                                    "import_err_pct": float(g.loc[g["step_min"] == h, "grid_import_kWh_err_pct"].iloc[0]),
                                    "building_err_pct": float(g.loc[g["step_min"] == h, "building_kWh_err_pct"].iloc[0])}
                   for h in g["step_min"]}
               for m, g in df[df["mode"] != "reference"].groupby("mode")}
    summary["reference"] = {k: float(ref[k]) for k in ("grid_import_kWh", "cost_usd", "building_kWh", "peak_kW", "wall_s")}
    (OUT / "e2b_summary.json").write_text(json.dumps(summary, indent=2))
    plot(df)


def plot(df: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt

    set_style()
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.6))
    modes = [("naive", COLORS[3], "Naive (latest values)"), ("aligned", COLORS[0], "Aligned (TIDE)"),
             ("aligned+sub", COLORS[2], "Aligned + 1-min EnergyPlus")]
    steps = [5, 15, 30, 60]
    x = np.arange(len(steps))
    w = 0.26
    for ax, (col, title) in zip(axes, [("cost_usd_err_pct", "(a) Energy cost"),
                                        ("grid_import_kWh_err_pct", "(b) Grid import"),
                                        ("building_kWh_err_pct", "(c) Building electricity")]):
        for i, (mode, c, lab) in enumerate(modes):
            g = df[df["mode"] == mode].set_index("step_min").reindex(steps)
            ax.bar(x + (i - 1) * w, g[col], w, color=c, label=lab)
        ax.axhline(0, color="k", lw=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{s}" for s in steps])
        ax.set_xlabel("Exchange step [min]")
        ax.set_title(title, loc="left")
    axes[0].set_ylabel("Error vs. 1-min reference [%]")
    axes[0].legend(fontsize=6.5, loc="best")
    fig.tight_layout()
    fig.savefig(FIG / "fig06_coupling_energyplus.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
