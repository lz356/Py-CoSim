"""Figure 6: coupling error of the EnergyPlus case (E2b) and the EnergyPlus time-step check (E2c)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import COLORS, FIG, OUT, set_style


def main() -> None:
    import matplotlib.pyplot as plt

    df = pd.read_csv(OUT / "e2b_coupling.csv")
    cli = pd.read_csv(OUT / "e2c_timestep_cli.csv")
    set_style()
    fig, axes = plt.subplots(1, 4, figsize=(7.4, 2.6), gridspec_kw={"width_ratios": [1, 1, 1, 0.95]})
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
        ax.set_xticks(x)
        ax.set_xticklabels([str(s) for s in steps])
        ax.set_xlabel("Exchange step [min]")
        ax.set_title(title, loc="left")
    axes[0].set_ylabel("Error vs. 1-min reference [%]")
    ax = axes[3]
    for sizing, c, lab in (("default", COLORS[1], "sizing window\nin steps (model)"),
                           ("fixed-90min", COLORS[0], "sizing window\nfixed at 90 min")):
        g = cli[cli["sizing"] == sizing]
        ax.plot(g["timesteps_per_hour"], g["diff_vs_60_pct"], "o-", color=c, label=lab, ms=3.5)
    ax.set_xscale("log")
    ax.set_xticks([1, 2, 4, 12, 60])
    ax.set_xticklabels(["1", "2", "4", "12", "60"])
    ax.set_xlabel("EnergyPlus steps per hour")
    ax.set_ylabel("Electricity vs. 60 steps h$^{-1}$ [%]")
    ax.set_title("(d) Standalone EnergyPlus", loc="left")
    ax.legend(fontsize=6, loc="center left", bbox_to_anchor=(0.0, 0.45), frameon=False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, fontsize=6.5, frameon=False, bbox_to_anchor=(0.4, -0.08))
    fig.tight_layout()
    fig.savefig(FIG / "fig06_coupling_energyplus.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
