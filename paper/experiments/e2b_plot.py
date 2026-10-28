"""Figure 6: coupling error of the EnergyPlus case (E2b) and the EnergyPlus time-step check (E2c).

Re-draws the figure from results/e2b_coupling.csv and results/e2c_timestep_cli.csv
(no simulation is run).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import COLORS, OUT, TEXT_WIDTH_IN, paper_style, save_paper_figure


def main() -> None:
    import matplotlib.pyplot as plt

    df = pd.read_csv(OUT / "e2b_coupling.csv")
    cli = pd.read_csv(OUT / "e2c_timestep_cli.csv")
    paper_style()
    fig, axs = plt.subplots(2, 2, figsize=(TEXT_WIDTH_IN, 4.7), layout="constrained")
    axes = axs.ravel()
    steps = [5, 15, 30, 60]
    x = np.arange(len(steps))
    w = 0.26
    modes = [("naive", COLORS[3], "Naive (latest values)"), ("aligned", COLORS[0], "Aligned (TIDE)"),
             ("aligned+sub", COLORS[2], "Aligned, 1-min EnergyPlus inside")]
    for ax, (col, title) in zip(axes[:3], [("cost_usd_err_pct", "(a) Energy cost"),
                                            ("grid_import_kWh_err_pct", "(b) Grid import"),
                                            ("grid_export_kWh_err_pct", "(c) Grid export")]):
        for i, (mode, c, lab) in enumerate(modes):
            g = df[df["mode"] == mode].set_index("step_min").reindex(steps)
            ax.bar(x + (i - 1) * w, g[col], w, color=c, label=lab)
        ax.axhline(0, color="k", lw=0.6)
        ax.grid(axis="x", visible=False)
        ax.set_xticks(x)
        ax.set_xticklabels([str(s) for s in steps])
        ax.set_xlabel("Exchange step [min]")
        ax.set_ylabel("Error vs. 1-min reference [%]")
        ax.set_title(title, loc="left")
    ax = axes[3]
    for sizing, c, lab in (("default", COLORS[1], "Sizing window in time steps (model)"),
                           ("fixed-90min", COLORS[0], "Sizing window fixed at 90 min")):
        g = cli[cli["sizing"] == sizing]
        ax.plot(g["timesteps_per_hour"], g["diff_vs_60_pct"], "o-", color=c, label=lab, ms=3.5)
    ax.set_xscale("log")
    ax.set_xticks([1, 2, 4, 12, 60])
    ax.set_xticklabels(["1", "2", "4", "12", "60"])
    ax.minorticks_off()
    ax.set_ylim(-16, 1.5)
    ax.set_xlabel("EnergyPlus time steps per hour")
    ax.set_ylabel("Electricity vs. 60 steps h$^{-1}$ [%]")
    ax.set_title("(d) Standalone EnergyPlus", loc="left")
    ax.legend(loc="center left", bbox_to_anchor=(0.0, 0.68), frameon=False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside upper center", ncol=3, frameon=False)
    save_paper_figure(fig, "fig06_coupling_energyplus.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
