"""Case study C: travel behaviour -> occupancy -> apartment cooling loads (paper Section 5.3)."""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

from common import COLORS, FIG, OUT, load_example, set_style

ex = load_example("06_travel_behavior_occupancy")
WORK = OUT / "case_c"
DAYS = 7
SEEDS = (101, 202, 303)
PAIRS = (("long", "short"), ("ev", "nonev"))


def hourly_profiles(res) -> dict:
    df = res.df
    out = {}
    for b in sorted({c.split(".")[0] for c in df.columns if c.startswith("apt_")}):
        qc = df[[c for c in df.columns if c.startswith(f"{b}.Qc_")]].sum(axis=1) / 1000
        out[b] = qc.groupby(qc.index.hour).mean()
        parts = b.split("_")
        g, r = "_".join(parts[1:-2]), parts[-1]
        occ = df[[c for c in df.columns if c.startswith(f"occ_{g}_{r}.occ_")]].sum(axis=1)
        out[f"occ_{b}"] = occ.groupby(occ.index.hour).mean()
    return out


def household_stats() -> pd.DataFrame:
    """Household size and share of the diary day with nobody home, per group (whole NHTS selection)."""
    from pycosim.components.occupancy import PROFILE_COLUMNS, select_households

    rows = []
    for name, kw in {"long": dict(commute="long"), "short": dict(commute="short"), "ev": dict(group="ev"),
                     "nonev": dict(group="non_ev")}.items():
        d = select_households(**kw)
        prof = d[PROFILE_COLUMNS].to_numpy()
        rows.append({"group": name, "n": len(d), "mean_hhsize": d.hhsize.mean(),
                     "share_1person": (d.hhsize == 1).mean(), "empty_frac_day": (prof < 1e-9).mean(axis=1).mean(),
                     "mean_frac_home": prof.mean(), "commute_med": d.commute_min.median()})
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "case_c_household_stats.csv", index=False)
    return table


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    print(household_stats().round(3).to_string())
    summaries, profiles = [], []
    for pair in PAIRS:
        for seed in SEEDS:
            t0 = time.perf_counter()
            sim = ex.build(groups=pair, policies=("fixed", "smart"), replicates=1, days=DAYS, seed=seed,
                           output_dir=WORK, name=f"{'-'.join(pair)}_{seed}")
            res = sim.run()
            s = ex.summarize(res)
            s["seed"] = seed
            s["wall_time_s"] = time.perf_counter() - t0
            summaries.append(s)
            prof = hourly_profiles(res)
            for key, series in prof.items():
                profiles.append(pd.DataFrame({"key": key, "seed": seed, "hour": series.index, "value": series.values}))
            print(pair, seed, f"{time.perf_counter() - t0:.0f}s", flush=True)
    table = pd.concat(summaries, ignore_index=True)
    table.to_csv(OUT / "case_c_runs.csv", index=False)
    prof = pd.concat(profiles, ignore_index=True)
    prof.to_csv(OUT / "case_c_profiles.csv", index=False)
    agg = table.groupby(["group", "policy"]).agg(
        cooling_load_MWh=("cooling_load_MWh", "mean"), cooling_load_sd=("cooling_load_MWh", "std"),
        cooling_elec_MWh=("cooling_elec_MWh", "mean"), peak_to_valley=("peak_to_valley", "mean"),
        peak_hour=("peak_hour", lambda x: int(np.round(np.mean(x)))), people_home=("mean_people_home", "mean"))
    print(agg.round(3).to_string())
    savings = []
    for (g, seed), sub in table.groupby(["group", "seed"]):
        f = sub[sub.policy == "fixed"].iloc[0]
        sm = sub[sub.policy == "smart"].iloc[0]
        savings.append({"group": g, "seed": seed,
                        "cooling_load_saving_%": (1 - sm.cooling_load_MWh / f.cooling_load_MWh) * 100,
                        "cooling_elec_saving_%": (1 - sm.cooling_elec_MWh / f.cooling_elec_MWh) * 100})
    savings = pd.DataFrame(savings)
    savings.to_csv(OUT / "case_c_savings.csv", index=False)
    print(savings.groupby("group").agg(["mean", "std"]).round(2).to_string())
    agg.to_csv(OUT / "case_c_summary.csv")

    import matplotlib.pyplot as plt

    set_style()
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.2))
    labels = {"long": "long commute (≥30 min)", "short": "short commute (≤10 min)", "ev": "EV/hybrid households",
              "nonev": "non-EV households"}
    for col, pair in enumerate(PAIRS):
        ax_occ, ax_q = axes[0, col], axes[1, col]
        for c, g in zip((COLORS[0], COLORS[3]), pair):
            occ = prof[prof.key.str.startswith(f"occ_apt_{g}_fixed")].groupby("hour")["value"].mean()
            ax_occ.plot(occ.index, occ.values, color=c, lw=1.3, label=labels[g])
            for ls, policy in (("-", "fixed"), ("--", "smart")):
                q = prof[prof.key.str.match(rf"^apt_{g}_{policy}_")].groupby("hour")["value"].mean()
                ax_q.plot(q.index, q.values, ls, color=c, lw=1.3, label=f"{labels[g]}, {policy}")
        ax_occ.set_ylabel("People at home (23 units)")
        ax_occ.set_title(f"({'ab'[col]}) Occupancy from NHTS diaries", loc="left")
        ax_occ.legend(fontsize=7)
        ax_q.set_ylabel("Cooling load [kW]")
        ax_q.set_xlabel("Hour of day")
        ax_q.set_title(f"({'cd'[col]}) Apartment cooling load, fixed vs. smart", loc="left")
        ax_q.legend(fontsize=6.5)
        for a in (ax_occ, ax_q):
            a.set_xticks(range(0, 25, 4))
    fig.tight_layout()
    fig.savefig(FIG / "fig08_case_c_travel.png")
    print("figure written")


if __name__ == "__main__":
    main()
