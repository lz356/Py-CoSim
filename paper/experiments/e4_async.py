"""E4: slow AI components -- blocking calls vs. SMMM deadline-aware asynchronous delivery.

N EnergyPlus offices (N = 1, 2, 4, 8), each supervised hourly by an
LLMSupervisor whose backend answers deterministically but with a random,
log-normally distributed latency (median 0.5 s), standing in for a hosted LLM
(not a language model). We compare

* ``blocking``: the master waits for each answer; decisions apply at once;
* ``async``: the call runs in the background; its answer applies 15 min later
  in simulation time (``make_async(delay="15min")``), so calls overlap with each
  other and with EnergyPlus.

Each asynchronous run is repeated with new random latencies to check that the
results do not depend on them.

Output: results/e4_async.csv, results/e4_summary.json, figures/fig08_async.png
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from common import COLORS, FIG, OUT, make_office, set_style

import pycosim as pc
from pycosim.components import GridMeter, LLMSupervisor, StochasticStandInBackend, TimeOfUseTariff

START, END = "2023-07-18", "2023-07-20"
WORK = OUT / "e4"
NS = [1, 2, 4, 8]
LATENCY = 0.5
INTENT = "Office building: keep people comfortable during working hours."


def build(n: int, mode: str, name: str) -> pc.CoSimulation:
    sim = pc.CoSimulation(START, END, "15min", name=name, output_dir=WORK, log_level="WARNING")
    sim.add(TimeOfUseTariff("tariff"))
    sim.add(GridMeter("grid"))
    for i in range(n):
        b = f"b{i}"
        sim.add(make_office(b))
        ai = LLMSupervisor(f"ai{i}", StochasticStandInBackend(spread=0.0, latency_s=LATENCY, seed=None), INTENT,
                           setpoints={"clg_sp": (22.0, 29.0, 24.0)},
                           observations={"T_zone": "core zone air temperature [C]", "price": "price [$/kWh]"},
                           step_size="1h", occupied_hours=(7, 19), timeout=60.0)
        if mode == "async":
            ai.make_async(delay="15min")
        sim.add(ai)
        sim.connect(f"{b}.T_core_zn", f"ai{i}.T_zone")
        sim.connect("tariff.price", f"ai{i}.price")
        sim.connect(f"ai{i}.clg_sp", f"{b}.clg_sp")
    sim.subscribe("grid.P_load", "b*.P_facility", reduce="sum")
    sim.connect("tariff.price", "grid.price")
    return sim


def physical(res: pc.Results) -> pd.DataFrame:
    cols = [c for c in res.df.columns if not c.endswith((".latency", ".reason"))]
    return res.df[cols]


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in NS:
        runs = {}
        for mode, rep in (("blocking", 1), ("async", 1), ("async", 2)):
            name = f"{mode}{rep}_n{n}"
            t0 = time.perf_counter()
            res = build(n, mode, name).run()
            wall = time.perf_counter() - t0
            runs[(mode, rep)] = res
            calls = sum(int(res.timing.set_index("component").loc[f"ai{i}", "calls"]) for i in range(n))
            ai_time = sum(float(res.timing.set_index("component").loc[f"ai{i}", "total_s"]) for i in range(n))
            rows.append({"n_buildings": n, "mode": mode, "repeat": rep, "wall_s": wall, "ai_calls": calls,
                         "ai_wall_s_sum": ai_time, "energy_kWh": float(res["grid.E_import"].iloc[-1]),
                         "cost_usd": float(res["grid.cost"].iloc[-1])})
            print(rows[-1], flush=True)
        a1, a2 = physical(runs[("async", 1)]), physical(runs[("async", 2)])
        rows[-1]["identical_to_repeat_1"] = bool(a1.equals(a2))
        diff = float(np.nanmax(np.abs((a1.select_dtypes("number") - a2.select_dtypes("number")).to_numpy())))
        rows[-1]["max_abs_diff_to_repeat_1"] = diff
        print("n", n, "async repeats identical:", a1.equals(a2), diff, flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "e4_async.csv", index=False)
    piv = df[df["repeat"] == 1].pivot(index="n_buildings", columns="mode", values="wall_s")
    summary = {
        "latency_median_s": LATENCY,
        "wall_s": piv.to_dict(),
        "speedup": (piv["blocking"] / piv["async"]).to_dict(),
        "async_repeats_identical": {int(r.n_buildings): bool(r.identical_to_repeat_1)
                                    for r in df[df["repeat"] == 2].itertuples()},
        "energy_change_async_vs_blocking_pct": {
            int(n): float(100 * (g.loc[g["mode"] == "async", "energy_kWh"].iloc[0]
                                 / g.loc[g["mode"] == "blocking", "energy_kWh"].iloc[0] - 1))
            for n, g in df[df["repeat"] == 1].groupby("n_buildings")},
    }
    (OUT / "e4_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    plot(df)


def plot(df: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt

    set_style()
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    for mode, c, lab in (("blocking", COLORS[3], "Blocking calls"), ("async", COLORS[0], "Asynchronous (SMMM)")):
        g = df[(df["mode"] == mode) & (df["repeat"] == 1)]
        ax.plot(g["n_buildings"], g["wall_s"], "o-", color=c, label=lab)
    ax.set_xlabel("Buildings, each with one AI supervisor")
    ax.set_ylabel("Wall-clock time [s]")
    ax.set_xticks(NS)
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG / "fig08_async.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
