"""Case study A: grid-interactive community of ten EnergyPlus offices (paper Section 5.1)."""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from common import COLORS, FIG, OUT, load_example, set_style

ex = load_example("04_community_pv_battery")
WORK = OUT / "case_a"
N, DAYS = 10, 7


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    results, rows = {}, {}
    for strategy in ex.STRATEGIES:
        t0 = time.perf_counter()
        sim = ex.build(strategy, n_buildings=N, days=DAYS, output_dir=WORK)
        res = sim.run()
        wall = time.perf_counter() - t0
        results[strategy] = res
        rows[strategy] = {**ex.kpis(res), "wall_time_s": wall}
        print(strategy, {k: round(v, 2) for k, v in rows[strategy].items()}, flush=True)
    table = pd.DataFrame(rows).T
    base = table.loc["baseline"]
    for col in ("import_MWh", "on_peak_import_MWh", "peak_import_kW", "energy_cost_$"):
        table[f"{col}_change_%"] = (table[col] / base[col] - 1) * 100
    table.to_csv(OUT / "case_a_kpis.csv")
    n_comp = len(results["coordinated"].meta["components"])
    meta = {"n_buildings": N, "days": DAYS, "components_coordinated": n_comp,
            "connections_coordinated": len(results["coordinated"].meta["connections"]),
            "steps": results["coordinated"].meta["n_steps"]}
    (OUT / "case_a_meta.json").write_text(json.dumps(meta, indent=2))
    print(table.round(2).T.to_string())

    import matplotlib.pyplot as plt

    set_style()
    fig, axes = plt.subplots(3, 1, figsize=(7.0, 6.4), sharex=False,
                             gridspec_kw={"height_ratios": [1.3, 0.8, 1.0]})
    window = slice("2023-07-18", "2023-07-20")
    ax = axes[0]
    tariff = results["baseline"]["tariff.is_peak"].loc[window]
    ax.fill_between(tariff.index, 0, 1, where=tariff > 0, transform=ax.get_xaxis_transform(), color="#F0E442",
                    alpha=0.25, lw=0, label="On-peak (16–21 h)")
    for c, (strategy, res) in zip(COLORS, results.items()):
        net = (res["grid.P_import"] - res["grid.P_export"]).loc[window] / 1000
        ax.plot(net.index, net, lw=1.1, color=c, label=strategy)
    ax.set_ylabel("Grid import [kW]")
    ax.set_title("(a) Community grid exchange (10 offices, 250 kW PV)", loc="left")
    ax.legend(ncol=5, loc="upper left", fontsize=7)
    ax = axes[1]
    for c, strategy in zip(COLORS[2:], ("battery", "coordinated")):
        soc = results[strategy]["battery.soc"].loc[window]
        ax.plot(soc.index, soc, color=c, lw=1.1, label=strategy)
    ax.set_ylabel("Battery SOC [-]")
    ax.set_title("(b) Community battery state of charge (500 kWh)", loc="left")
    ax.legend(loc="lower right")
    import matplotlib.dates as mdates

    for a in axes[:2]:
        a.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
        a.xaxis.set_major_formatter(mdates.DateFormatter("%d Jul %H:%M"))
    ax = axes[2]
    metrics = [("on_peak_import_MWh", "On-peak import"), ("peak_import_kW", "Peak import"),
               ("energy_cost_$", "Energy cost"), ("import_MWh", "Total import")]
    x = np.arange(len(metrics))
    w = 0.2
    for i, (strategy, c) in enumerate(zip(ex.STRATEGIES, COLORS)):
        vals = [table.loc[strategy, m] / table.loc["baseline", m] * 100 for m, _ in metrics]
        ax.bar(x + (i - 1.5) * w, vals, w, color=c, label=strategy)
    ax.set_xticks(x)
    ax.set_xticklabels([lab for _, lab in metrics])
    ax.set_ylabel("% of baseline")
    ax.axhline(100, color="k", lw=0.6)
    ax.set_title("(c) Key performance indicators relative to the baseline", loc="left")
    ax.legend(ncol=4, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.12), frameon=False)
    fig.tight_layout()
    fig.savefig(FIG / "fig06_case_a_community.png")
    print("figure written")


if __name__ == "__main__":
    main()
