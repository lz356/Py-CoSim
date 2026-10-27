"""Verification of the EnergyPlus wrapper (paper Section 4.1).

V1  non-intrusiveness: EnergyPlus run inside Py-CoSim (sensors only) vs the
    same model run from the EnergyPlus command line -> identical SQL outputs.
V2  exchange timing: the value Py-CoSim publishes at master time t equals the
    value EnergyPlus reports with time stamp t.
V3  actuation equivalence: a Python controller writing the cooling set-point
    through an actuator vs the same set-point profile hard-coded as an
    EnergyPlus schedule -> identical results.
"""
from __future__ import annotations

import copy
import json
import time

import numpy as np
import pandas as pd

from common import FIG, OFFICE_ZONES, OUT, SMALL_OFFICE, TUCSON_EPW, read_sql_series, run_energyplus_cli, set_style

import pycosim as pc
from pycosim.energyplus import EnergyPlusModel

START, DAYS, YEAR = "2023-07-17", 7, 2023
WORK = OUT / "verification"


def precool_value(t) -> float:
    """Python version of the pre-cooling schedule (identical to PRECOOL_SCHEDULE below)."""
    h = t.hour
    wd = t.weekday()
    if wd < 5:
        if h < 6:
            return 26.7
        if h < 13:
            return 24.0
        if h < 16:
            return 22.0
        if h < 21:
            return 26.5
        if h < 22:
            return 24.0
        return 26.7
    if wd == 5:
        return 24.0 if 6 <= h < 18 else 26.7
    return 26.7


PRECOOL_SCHEDULE = [
    "Through: 12/31",
    "For: SummerDesignDay", "Until: 06:00", 26.7, "Until: 22:00", 24.0, "Until: 24:00", 26.7,
    "For: Weekdays", "Until: 06:00", 26.7, "Until: 13:00", 24.0, "Until: 16:00", 22.0, "Until: 21:00", 26.5,
    "Until: 22:00", 24.0, "Until: 24:00", 26.7,
    "For: Saturday", "Until: 06:00", 26.7, "Until: 18:00", 24.0, "Until: 24:00", 26.7,
    "For: AllOtherDays", "Until: 24:00", 26.7,
]


def base_model() -> EnergyPlusModel:
    m = EnergyPlusModel.load(SMALL_OFFICE)
    m.remove_daylight_saving()  # Tucson, AZ: no DST; keeps EnergyPlus schedules on standard time
    m.set_timesteps_per_hour(4)
    end = (pd.Timestamp(START) + pd.Timedelta(days=DAYS - 1)).date()
    m.set_run_period(pd.Timestamp(START).date(), end)
    for z in OFFICE_ZONES:
        m.add_output_variable("Zone Mean Air Temperature", z, "Timestep")
    m.add_output_variable("Site Outdoor Air Drybulb Temperature", "Environment", "Timestep")
    m.add_output_meter("Electricity:Facility", "Timestep")
    m.enable_sqlite()
    return m


def pycosim_run(name: str, model: EnergyPlusModel, controller=None):
    sim = pc.CoSimulation(START, pd.Timestamp(START) + pd.Timedelta(days=DAYS), "15min", name=name,
                          output_dir=WORK, log_level="WARNING")
    sensors = {f"T_{z}": ("Zone Mean Air Temperature", z) for z in OFFICE_ZONES}
    sensors["T_out"] = ("Site Outdoor Air Drybulb Temperature", "Environment")
    actuators = {"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")} if controller else {}
    sim.add(pc.EnergyPlusComponent("office", model, TUCSON_EPW, sensors=sensors,
                                   meters={"E_elec": ("Electricity:Facility", "J")}, actuators=actuators))
    if controller is not None:
        sim.add(pc.component(controller, name="ctrl", outputs="clg_sp"))
        sim.connect("ctrl.clg_sp", "office.clg_sp")
    t0 = time.perf_counter()
    res = sim.run()
    return res, time.perf_counter() - t0, WORK / name / "office" / "eplus" / "eplusout.sql"


def compare(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    cols = sorted(set(a.columns) & set(b.columns))
    idx = a.index.intersection(b.index)
    rows = []
    for c in cols:
        d = (a.loc[idx, c] - b.loc[idx, c]).abs()
        rows.append({"series": c, "n": len(idx), "max_abs_diff": d.max(), "mean_abs": a.loc[idx, c].abs().mean()})
    return pd.DataFrame(rows)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    summary = {}
    # ---------------- V1 + V2: passthrough
    m0 = base_model()
    p0 = m0.save(WORK / "cli_base" / "in.epJSON")
    t_cli = run_energyplus_cli(p0, WORK / "cli_base")
    res1, t_py, sql1 = pycosim_run("pycosim_base", m0)
    cli = read_sql_series(WORK / "cli_base" / "eplusout.sql", YEAR)
    wrapped = read_sql_series(sql1, YEAR)
    v1 = compare(cli, wrapped)
    v1.to_csv(OUT / "verification_v1.csv", index=False)
    summary["V1_max_abs_diff"] = float(v1["max_abs_diff"].max())
    summary["V1_n_series"] = int(len(v1))
    summary["wall_cli_s"], summary["wall_pycosim_s"] = t_cli, t_py
    summary["n_steps"] = int(res1.meta["n_steps"])

    # V2: published value at t == EnergyPlus report stamped t (skip t0, which is the warm-up state)
    obs = res1.df.iloc[1:]
    pairs = {f"office.T_{z}": f"Zone Mean Air Temperature:{z.upper()}" for z in OFFICE_ZONES}
    pairs["office.E_elec"] = "Electricity:Facility"
    pairs["office.T_out"] = "Site Outdoor Air Drybulb Temperature:Environment"
    v2 = []
    for ours, theirs in pairs.items():
        ref = cli[theirs].reindex(obs.index)
        d = (obs[ours] - ref).abs()
        v2.append({"pycosim": ours, "energyplus": theirs, "n": int(ref.notna().sum()), "max_abs_diff": float(d.max())})
    v2 = pd.DataFrame(v2)
    v2.to_csv(OUT / "verification_v2.csv", index=False)
    summary["V2_max_abs_diff"] = float(v2["max_abs_diff"].max())

    # ---------------- V3: actuation equivalence
    m_hard = base_model()
    sched = m_hard.get("Schedule:Compact", "CLGSETP_SCH")
    sched["data"] = [{"field": f} for f in PRECOOL_SCHEDULE]
    p_hard = m_hard.save(WORK / "cli_precool" / "in.epJSON")
    run_energyplus_cli(p_hard, WORK / "cli_precool")
    m_split = base_model()  # same design-day profile as the hard-coded model -> identical sizing
    s2 = m_split.get("Schedule:Compact", "CLGSETP_SCH")
    s2["data"] = [{"field": f} for f in PRECOOL_SCHEDULE]
    res3, _, sql3 = pycosim_run("pycosim_precool", m_split, controller=lambda t: precool_value(t))
    hard = read_sql_series(WORK / "cli_precool" / "eplusout.sql", YEAR)
    py3 = read_sql_series(sql3, YEAR)
    v3 = compare(hard, py3)
    v3.to_csv(OUT / "verification_v3.csv", index=False)
    summary["V3_max_abs_diff"] = float(v3["max_abs_diff"].max())
    base = read_sql_series(WORK / "cli_base" / "eplusout.sql", YEAR)
    summary["precool_effect_max_dT"] = float((hard["Zone Mean Air Temperature:CORE_ZN"]
                                              - base["Zone Mean Air Temperature:CORE_ZN"]).abs().max())
    (OUT / "verification_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(v1.sort_values("max_abs_diff").tail(3).to_string())
    print(v2.to_string())
    print(v3.sort_values("max_abs_diff").tail(3).to_string())

    # ---------------- figure
    import matplotlib.pyplot as plt

    set_style()
    fig, axes = plt.subplots(3, 1, figsize=(7.0, 6.2), sharex=True)
    day = slice("2023-07-18", "2023-07-19")
    c = "Zone Mean Air Temperature:CORE_ZN"
    ax = axes[0]
    ax.plot(cli.loc[day].index, cli.loc[day, c], lw=2.2, alpha=0.5, label="EnergyPlus CLI (reference)")
    ax.plot(obs.loc[day].index, obs.loc[day, "office.T_Core_ZN"], "k.", ms=2.5, label="Py-CoSim published value")
    ax.set_ylabel("Core zone T [°C]")
    ax.set_title("(a) Pass-through: values exchanged by Py-CoSim vs. EnergyPlus report", loc="left")
    ax.legend(loc="lower right")
    ax = axes[1]
    ax.plot(hard.loc[day].index, hard.loc[day, c], lw=2.2, alpha=0.5, label="Schedule hard-coded in EnergyPlus")
    ax.plot(py3.loc[day].index, py3.loc[day, c], "k.", ms=2.5, label="Python controller via actuator")
    ax.plot(base.loc[day].index, base.loc[day, c], ":", lw=1, color="gray", label="Original schedule")
    ax.set_ylabel("Core zone T [°C]")
    ax.set_title("(b) Actuation: Python pre-cooling controller vs. equivalent EnergyPlus schedule", loc="left")
    ax.legend(loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.0), fontsize=7)
    ax.set_ylim(21.5, 27.6)
    ax = axes[2]
    e = "Electricity:Facility"
    ax.plot(hard.loc[day].index, hard.loc[day, e] / 900 / 1000, lw=2.2, alpha=0.5, label="Hard-coded schedule")
    ax.plot(py3.loc[day].index, py3.loc[day, e] / 900 / 1000, "k.", ms=2.5, label="Python controller")
    ax.set_ylabel("Facility power [kW]")
    ax.set_title("(c) Facility electricity under the two implementations", loc="left")
    ax.legend(loc="upper left")
    fig.autofmt_xdate()
    fig.savefig(FIG / "fig04_verification.png")
    print("figure written")


if __name__ == "__main__":
    main()
