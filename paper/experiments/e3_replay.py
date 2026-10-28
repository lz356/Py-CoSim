"""E3: reproducibility of a co-simulation with a non-deterministic AI supervisor (record/replay, counterfactuals).

An EnergyPlus office is supervised hourly by an LLMSupervisor whose backend is a
*stochastic stand-in* (sampled answers, like a hosted LLM at temperature > 0;
not a language model). We

1. run the live co-simulation five times (different answers each time),
2. replay run 1 from its recorded messages and compare it with the original,
3. estimate the savings of a lighting retrofit (-30 % lighting power) in two
   ways: (a) live base vs. live retrofit runs, (b) live base vs. the same
   decisions replayed into the retrofitted building.

Output: results/e3_replay.csv, results/e3_summary.json, figures/fig07_replay.png
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from common import COLORS, OUT, TEXT_WIDTH_IN, make_office, paper_style, save_paper_figure

import pycosim as pc
from pycosim.components import GridMeter, LLMSupervisor, StochasticStandInBackend, TimeOfUseTariff

START, END = "2023-07-17", "2023-07-24"
WORK = OUT / "e3"
N_LIVE = 5
INTENT = "Office building: keep people comfortable during working hours."


def retrofit(model):
    for name, obj in model.objects("Lights").items():
        obj["watts_per_zone_floor_area"] = float(obj["watts_per_zone_floor_area"]) * 0.7


def build(name: str, retrofitted: bool = False, record: bool = False) -> pc.CoSimulation:
    sim = pc.CoSimulation(START, END, "15min", name=name, output_dir=WORK, log_level="WARNING",
                          record=["ai"] if record else None)
    sim.add(make_office("office", preprocess=retrofit if retrofitted else None))
    sim.add(TimeOfUseTariff("tariff"))
    sim.add(LLMSupervisor("ai", StochasticStandInBackend(spread=1.0, seed=None), INTENT,
                          setpoints={"clg_sp": (22.0, 29.0, 24.0)},
                          observations={"T_zone": "core zone air temperature [C]",
                                        "T_out": "outdoor air temperature [C]",
                                        "price": "electricity price [$/kWh]"},
                          step_size="1h", occupied_hours=(7, 19)))
    sim.add(GridMeter("grid"))
    sim.connect("office.T_core_zn", "ai.T_zone")
    sim.connect("office.T_out", "ai.T_out")
    sim.connect("tariff.price", "ai.price")
    sim.connect("ai.clg_sp", "office.clg_sp")
    sim.subscribe("grid.P_load", "office.P_facility")
    sim.connect("tariff.price", "grid.price")
    return sim


def kpis(res: pc.Results) -> dict:
    df = res.df
    occ = df.index.to_series().apply(lambda t: t.weekday() < 5 and 7 <= t.hour < 19)
    t_core = df["office.T_core_zn"]
    return {
        "energy_kWh": float(df["grid.E_import"].iloc[-1]),
        "cost_usd": float(df["grid.cost"].iloc[-1]),
        "mean_clg_sp_occupied": float(df.loc[occ, "ai.clg_sp"].mean()),
        "overheat_Kh": float((t_core[occ] - 26.0).clip(lower=0).sum() * 0.25),
    }


PHYSICAL = ["office.P_facility", "office.T_core_zn", "ai.clg_sp", "grid.E_import", "grid.cost"]


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    rows = []
    live = {}
    for i in range(1, N_LIVE + 1):
        t0 = time.perf_counter()
        res = build(f"live_{i}", record=True).run()
        live[i] = res
        rows.append({"run": f"live_{i}", "building": "base", "decisions": f"live {i}",
                     "wall_s": time.perf_counter() - t0, **kpis(res)})
        print(rows[-1], flush=True)
    # 1) exact replay of run 1
    sim = build("replay_1")
    sim.replay("ai", WORK / "live_1")
    t0 = time.perf_counter()
    rep = sim.run()
    rows.append({"run": "replay_1", "building": "base", "decisions": "replay of live 1",
                 "wall_s": time.perf_counter() - t0, **kpis(rep)})
    diffs = {c: float(np.nanmax(np.abs(rep[c].to_numpy() - live[1][c].to_numpy()))) for c in PHYSICAL}
    live_spread = {c: float(np.nanmax(np.abs(live[2][c].to_numpy() - live[1][c].to_numpy()))) for c in PHYSICAL}
    print("replay max abs diff:", diffs, flush=True)
    # 2) retrofit savings: live pairs vs. replayed decisions
    for i in range(1, N_LIVE + 1):
        res = build(f"retro_live_{i}", retrofitted=True).run()
        rows.append({"run": f"retro_live_{i}", "building": "retrofit", "decisions": f"live (new draw) {i}",
                     **kpis(res)})
        sim = build(f"retro_replay_{i}", retrofitted=True)
        sim.replay("ai", WORK / f"live_{i}")
        res = sim.run()
        rows.append({"run": f"retro_replay_{i}", "building": "retrofit", "decisions": f"replay of live {i}",
                     **kpis(res)})
        print(rows[-2], rows[-1], flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "e3_replay.csv", index=False)
    base = df[df["run"].str.startswith("live_")].set_index("run")
    sav_live, sav_replay = [], []
    for i in range(1, N_LIVE + 1):
        b = base.loc[f"live_{i}", "energy_kWh"]
        sav_live.append(100 * (1 - df.loc[df["run"] == f"retro_live_{i}", "energy_kWh"].iloc[0] / b))
        sav_replay.append(100 * (1 - df.loc[df["run"] == f"retro_replay_{i}", "energy_kWh"].iloc[0] / b))
    summary = {
        "live_runs": N_LIVE,
        "live_energy_kWh_mean": float(base["energy_kWh"].mean()),
        "live_energy_kWh_std": float(base["energy_kWh"].std(ddof=1)),
        "live_energy_kWh_range": float(base["energy_kWh"].max() - base["energy_kWh"].min()),
        "live_cost_range_usd": float(base["cost_usd"].max() - base["cost_usd"].min()),
        "live_clg_sp_mean_range": float(base["mean_clg_sp_occupied"].max() - base["mean_clg_sp_occupied"].min()),
        "replay_max_abs_diff": diffs,
        "live1_vs_live2_max_abs_diff": live_spread,
        "retrofit_savings_live_pct": sav_live,
        "retrofit_savings_replay_pct": sav_replay,
        "retrofit_savings_live_mean": float(np.mean(sav_live)),
        "retrofit_savings_live_std": float(np.std(sav_live, ddof=1)),
        "retrofit_savings_replay_mean": float(np.mean(sav_replay)),
        "retrofit_savings_replay_std": float(np.std(sav_replay, ddof=1)),
        "messages_recorded_live_1": int(len(live[1].messages)) if live[1].messages is not None else 0,
    }
    (OUT / "e3_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    plot(live, rep, sav_live, sav_replay)


def plot(live, rep, sav_live, sav_replay) -> None:
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    paper_style()
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH_IN, 3.0), layout="constrained",
                             gridspec_kw={"width_ratios": [2.2, 1]})
    ax = axes[0]
    day = slice("2023-07-18 06:00", "2023-07-18 20:00")
    for i, res in live.items():
        s = res["ai.clg_sp"].loc[day]
        ax.step(s.index, s, where="post", lw=0.9, color=COLORS[i % len(COLORS)], alpha=0.8, label=f"Live run {i}")
    s = rep["ai.clg_sp"].loc[day]
    ax.step(s.index, s, where="post", lw=2.2, color="k", ls=":", label="Replay of run 1")
    ax.set_ylabel("Cooling set-point [°C]")
    ax.set_title("(a) Sampled AI decisions on 18 July", loc="left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.1), frameon=False, columnspacing=1.2)
    ax = axes[1]
    ax.boxplot([sav_live, sav_replay], widths=0.5)
    for j, vals in enumerate([sav_live, sav_replay], start=1):
        ax.scatter(np.full(len(vals), j) + np.linspace(-0.08, 0.08, len(vals)), vals, s=10, color=COLORS[j - 1],
                   zorder=3)
    ax.set_xticks([1, 2])
    ax.set_xticklabels(["Live vs.\nlive", "Live vs.\nreplayed"])
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("Estimated retrofit savings [%]")
    ax.set_title("(b) Retrofit savings estimate", loc="left")
    save_paper_figure(fig, "fig07_replay.png")
    plt.close(fig)


if __name__ == "__main__":
    import sys

    if "--plot" in sys.argv or "--plot-only" in sys.argv:  # re-draw from the saved runs
        live = {i: pc.Results.load(WORK / f"live_{i}") for i in range(1, N_LIVE + 1)}
        summary = json.loads((OUT / "e3_summary.json").read_text())
        plot(live, pc.Results.load(WORK / "replay_1"), summary["retrofit_savings_live_pct"],
             summary["retrofit_savings_replay_pct"])
    else:
        main()
