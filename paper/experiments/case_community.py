"""Case study: grid-interactive community of ten EnergyPlus offices, naive vs. aligned metering.

Example 04 is run for its four strategies (baseline, pre-cooling, battery,
coordinated) twice: with the Py-CoSim 2.0 wiring (latest values multiplied)
and with TIDE subscriptions (pattern subscription of all buildings, power
and price read over the interval that just ended).

Output: results/case_community.csv, results/case_community.json, figures/fig09_case_a.png
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from common import COLORS, OUT, TEXT_WIDTH_IN, load_example, paper_style, save_paper_figure

ex = load_example("04_community_pv_battery")
WORK = OUT / "case_community"
N, DAYS = 10, 7


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    rows = []
    for aligned in (True, False):
        for strategy in ex.STRATEGIES:
            t0 = time.perf_counter()
            sim = ex.build(strategy, n_buildings=N, days=DAYS, output_dir=WORK, aligned=aligned)
            sim.name = f"{strategy}_{'aligned' if aligned else 'naive'}"
            res = sim.run()
            row = {"strategy": strategy, "coupling": "aligned" if aligned else "naive",
                   "wall_s": time.perf_counter() - t0, **ex.kpis(res),
                   "components": len(res.meta["components"]), "connections": len(res.meta["connections"])}
            rows.append(row)
            print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}, flush=True)
    df = pd.DataFrame(rows)
    for col in ("import_MWh", "on_peak_import_MWh", "peak_import_kW", "energy_cost_$"):
        base = df[df["strategy"] == "baseline"].set_index("coupling")[col]
        df[f"{col}_vs_baseline_%"] = [100 * (r[col] / base[r["coupling"]] - 1) for _, r in df.iterrows()]
    df.to_csv(OUT / "case_community.csv", index=False)
    a = df[df["coupling"] == "aligned"].set_index("strategy")
    n = df[df["coupling"] == "naive"].set_index("strategy")
    summary = {
        "cost_naive_vs_aligned_pct": {s: float(100 * (n.loc[s, "energy_cost_$"] / a.loc[s, "energy_cost_$"] - 1))
                                      for s in ex.STRATEGIES},
        "on_peak_naive_vs_aligned_pct": {s: float(100 * (n.loc[s, "on_peak_import_MWh"] / a.loc[s, "on_peak_import_MWh"] - 1))
                                         for s in ex.STRATEGIES},
        "savings_aligned_pct": {s: float(-a.loc[s, "energy_cost_$_vs_baseline_%"]) for s in ex.STRATEGIES},
        "savings_naive_pct": {s: float(-n.loc[s, "energy_cost_$_vs_baseline_%"]) for s in ex.STRATEGIES},
        "on_peak_reduction_aligned_pct": {s: float(-a.loc[s, "on_peak_import_MWh_vs_baseline_%"]) for s in ex.STRATEGIES},
        "on_peak_reduction_naive_pct": {s: float(-n.loc[s, "on_peak_import_MWh_vs_baseline_%"]) for s in ex.STRATEGIES},
        "components_coordinated": int(a.loc["coordinated", "components"]),
        "mean_wall_s": float(df["wall_s"].mean()),
    }
    (OUT / "case_community.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    plot(df)


# Strategy names as written in the text
STRATEGY_LABELS = {"baseline": "Baseline", "precool": "Pre-cooling", "battery": "Battery",
                   "coordinated": "Coordinated"}
IMPORT_LIMIT_KW = 120.0  # import limit of the battery's peak-shaving controller (example 04)


def plot(df: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt

    paper_style()
    fig, axd = plt.subplot_mosaic([["a", "b"], ["c", "c"]], figsize=(TEXT_WIDTH_IN, 4.6), layout="constrained",
                                  height_ratios=[1.1, 0.9])
    strategies = list(ex.STRATEGIES)[1:]
    x = np.arange(len(strategies))
    w = 0.42
    couplings = (("naive", COLORS[3], "Naive (latest values)"), ("aligned", COLORS[0], "Aligned (TIDE)"))
    for ax, (col, title) in zip((axd["a"], axd["b"]), [("energy_cost_$_vs_baseline_%", "(a) Energy cost"),
                                                        ("on_peak_import_MWh_vs_baseline_%", "(b) On-peak import")]):
        lo = df[col].min()
        for i, (coupling, c, lab) in enumerate(couplings):
            g = df[df["coupling"] == coupling].set_index("strategy").reindex(strategies)
            bars = ax.bar(x + (i - 0.5) * w, g[col], w, color=c, label=lab)
            for b, v in zip(bars, g[col]):
                ax.text(b.get_x() + b.get_width() / 2, v + 0.02 * lo, f"{v:.1f}".replace("-", "−"),
                        ha="center", va="top", fontsize=8)
        ax.set_ylim(1.2 * lo, 0)
        ax.axhline(0, color="k", lw=0.6)
        ax.grid(axis="x", visible=False)
        ax.set_xticks(x)
        ax.set_xticklabels([STRATEGY_LABELS[s] for s in strategies])
        ax.set_ylabel("Change vs. baseline [%]")
        ax.set_title(title, loc="left")
    ax = axd["c"]
    allx = np.arange(len(ex.STRATEGIES))
    for i, (coupling, c, lab) in enumerate(couplings):
        g = df[df["coupling"] == coupling].set_index("strategy").reindex(list(ex.STRATEGIES))
        ax.bar(allx + (i - 0.5) * w, g["peak_import_kW"], w, color=c, label=lab)
    ax.axhline(IMPORT_LIMIT_KW, color="k", lw=0.8, ls="--", label=f"Battery import limit ({IMPORT_LIMIT_KW:.0f} kW)")
    ax.set_ylim(100, 145)
    ax.grid(axis="x", visible=False)
    ax.set_xticks(allx)
    ax.set_xticklabels([STRATEGY_LABELS[s] for s in ex.STRATEGIES])
    ax.set_ylabel("Peak import (15 min) [kW]")
    ax.set_title("(c) Peak grid import", loc="left")
    handles, labels = ax.get_legend_handles_labels()
    order = [1, 2, 0]  # bars first, then the limit line
    fig.legend([handles[k] for k in order], [labels[k] for k in order], loc="outside upper center", ncol=3, frameon=False)
    save_paper_figure(fig, "fig09_case_a.png")
    plt.close(fig)


if __name__ == "__main__":
    import sys

    if "--plot" in sys.argv or "--plot-only" in sys.argv:  # re-draw from the saved table
        plot(pd.read_csv(OUT / "case_community.csv"))
    else:
        main()
