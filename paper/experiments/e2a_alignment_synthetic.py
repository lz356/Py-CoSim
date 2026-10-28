"""E2a: energy and cost across different step sizes -- naive (latest value) vs. aligned exchange.

A producer publishes the exact mean of a known power signal over each of its
steps (time meaning ``average``/``past``, like EnergyPlus); a tariff publishes
prices (``hold``); a meter running at another step integrates energy and cost.
Because the power signal is analytic, the exact energy and cost over the
meter's windows are known. Producer and meter steps range from 1 to 60 min.

Output: results/e2a_alignment.csv, figures/fig05_alignment_synthetic.png
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
import pandas as pd

from common import OUT, paper_style, save_paper_figure

import pycosim as pc
from pycosim.components import GridMeter

START, END = "2023-07-17", "2023-07-20"  # Monday-Wednesday
STEPS_MIN = [1, 5, 15, 30, 60]
T0 = pd.Timestamp(START)


# ------------------------------------------------------------ the known truth
def power(tau: np.ndarray) -> np.ndarray:
    """Load in W at tau seconds after the start (daily cycle + 3-hour oscillation)."""
    return 1000.0 * (50 + 30 * np.sin(2 * np.pi * (tau / 86400.0 - 0.3)) + 12 * np.sin(2 * np.pi * tau / 10800.0))


def power_mean(a: float, b: float) -> float:
    """Exact mean of power() over [a, b)."""
    w1, w2 = 2 * math.pi / 86400.0, 2 * math.pi / 10800.0
    integral = 50 * (b - a)
    integral += 30 * (-math.cos(w1 * b - 2 * math.pi * 0.3) + math.cos(w1 * a - 2 * math.pi * 0.3)) / w1
    integral += 12 * (-math.cos(w2 * b) + math.cos(w2 * a)) / w2
    return 1000.0 * integral / (b - a)


def tou(t: pd.Timestamp) -> float:
    return 0.30 if (t.weekday() < 5 and 16 <= t.hour < 21) else 0.10


_RTP = 0.12 + 0.06 * np.sin(np.arange(2000) * 2 * np.pi / 288 * 1.0) + np.random.default_rng(7).normal(0, 0.02, 2000)


def rtp(t: pd.Timestamp) -> float:
    """Real-time price that changes every 5 minutes."""
    block = int((t - T0).total_seconds() // 300)
    return float(max(_RTP[block % len(_RTP)], 0.01))


PRICES = {"TOU": tou, "RTP-5min": rtp}


def exact(price_fn, a: float, b: float):
    """Exact energy [kWh] and cost [$] over [a, b) seconds (1-s resolution for the price product)."""
    tau = np.arange(a, b, 1.0) + 0.5
    p = power(tau)
    times = T0 + pd.to_timedelta(tau - 0.5, unit="s")
    minute_prices = {}
    price = np.empty(len(tau))
    for i, t in enumerate(times):
        key = (t.value // 60_000_000_000)
        if key not in minute_prices:
            minute_prices[key] = price_fn(t.floor("min"))
        price[i] = minute_prices[key]
    return float(p.sum() / 3.6e6), float((p * price).sum() / 3.6e6)


# ------------------------------------------------------------ the co-simulation
class Producer(pc.Component):
    """Publishes the exact mean power over its step that just ended (like an EnergyPlus meter)."""

    direct_feedthrough = False
    outputs = {"P": {"default": 0.0, "kind": "average", "interval": "past", "unit": "W"}}

    def output(self, t):
        s = (t - self.ctx.start).total_seconds()
        return {"P": power_mean(s - self.ctx.step_size, s)}


def run(h_p: int, h_c: int, aligned: bool, price_name: str):
    sim = pc.CoSimulation(START, END, "1min", save=False, log_level="WARNING", name="e2a")
    sim.add(Producer("load", step_size=f"{h_p}min"))
    fn = PRICES[price_name]
    sim.add(pc.FunctionComponent(lambda t: fn(pd.Timestamp(t)), name="tariff",
                                 outputs={"price": {"default": fn(T0 - pd.Timedelta("1min")), "kind": "hold"}}))
    sim.add(GridMeter("meter", aligned=aligned, demand_window=f"{max(h_c, 15)}min"), )
    sim["meter"].step_size = f"{h_c}min"
    if aligned:
        sim.connect("load.P", "meter.P_load")
    else:
        sim.connect("load.P", "meter.P_net")
    sim.connect("tariff.price", "meter.price")
    t0 = time.perf_counter()
    res = sim.run()
    wall = time.perf_counter() - t0
    m = res.df[["meter.E_import", "meter.cost"]].iloc[::h_c]  # the meter's own steps
    # drop the first window (it lies before the start) and compare [0, T - h_c)
    energy = float(m["meter.E_import"].iloc[-1] - m["meter.E_import"].iloc[0])
    cost = float(m["meter.cost"].iloc[-1] - m["meter.cost"].iloc[0])
    n = len(m)
    a, b = 0.0, (n - 1) * h_c * 60.0
    e_true, c_true = exact(fn, a, b)
    cov = res.alignment["min_coverage"].min() if len(res.alignment) else float("nan")
    extra = int(res.alignment["extrapolated_reads"].sum()) if len(res.alignment) else 0
    return {"price": price_name, "h_producer_min": h_p, "h_meter_min": h_c,
            "mode": "aligned" if aligned else "naive", "energy_kWh": energy, "cost_usd": cost,
            "energy_true_kWh": e_true, "cost_true_usd": c_true,
            "energy_err_pct": 100 * (energy / e_true - 1), "cost_err_pct": 100 * (cost / c_true - 1),
            "extrapolated_reads": extra, "min_coverage": cov, "wall_s": wall}


def main() -> None:
    rows = []
    for price_name in PRICES:
        for h_p in STEPS_MIN:
            for h_c in STEPS_MIN:
                for aligned in (False, True):
                    r = run(h_p, h_c, aligned, price_name)
                    rows.append(r)
                    print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}, flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "e2a_alignment.csv", index=False)
    summary = {}
    for (price_name, mode), g in df.groupby(["price", "mode"]):
        ok = g[g["h_meter_min"] >= g["h_producer_min"]]
        summary[f"{price_name}/{mode}"] = {
            "max_abs_energy_err_pct": float(g["energy_err_pct"].abs().max()),
            "max_abs_cost_err_pct": float(g["cost_err_pct"].abs().max()),
            "max_abs_cost_err_pct_meter_not_faster": float(ok["cost_err_pct"].abs().max()),
            "max_abs_energy_err_pct_meter_not_faster": float(ok["energy_err_pct"].abs().max()),
        }
    (OUT / "e2a_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    plot(df)


# Panel titles: the price names used in the CSV are short codes
PRICE_LABELS = {"TOU": "time-of-use price", "RTP-5min": "real-time price (5 min)"}


def plot(df: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm

    paper_style()
    fig, axes = plt.subplots(2, 2, figsize=(5.6, 5.2), sharex=True, sharey=True, layout="constrained")
    vmin, vmax = 1e-4, 50
    for col, price_name in enumerate(PRICES):
        for row, mode in enumerate(("naive", "aligned")):
            ax = axes[row, col]
            g = df[(df["price"] == price_name) & (df["mode"] == mode)]
            mat = g.pivot(index="h_producer_min", columns="h_meter_min", values="cost_err_pct").abs()
            data = np.clip(mat.to_numpy(), vmin, None)
            im = ax.imshow(data, norm=LogNorm(vmin=vmin, vmax=vmax), cmap="viridis", origin="lower")
            ax.set_xticks(range(len(STEPS_MIN)))
            ax.set_xticklabels(STEPS_MIN)
            ax.set_yticks(range(len(STEPS_MIN)))
            ax.set_yticklabels(STEPS_MIN)
            ax.tick_params(labelbottom=True, labelleft=True)
            ax.grid(False)
            for i in range(data.shape[0]):
                for j in range(data.shape[1]):
                    v = mat.to_numpy()[i, j]
                    txt = "<0.001" if v < 1e-3 else (f"{v:.3f}" if v < 0.1 else f"{v:.1f}")
                    ax.text(j, i, txt, ha="center", va="center", fontsize=8,
                            color="white" if data[i, j] < 0.5 else "black")
            if row == 1:
                ax.set_xlabel("Meter step [min]")
            if col == 0:
                ax.set_ylabel("Producer step [min]")
            ax.set_title(f"({'abcd'[row * 2 + col]}) {mode.capitalize()}, {PRICE_LABELS[price_name]}", loc="left")
    cb = fig.colorbar(im, ax=axes, shrink=0.75, aspect=30, pad=0.03, label="|Cost error| [%]")
    cb.ax.grid(False)
    save_paper_figure(fig, "fig05_alignment_synthetic.png")
    plt.close(fig)


if __name__ == "__main__":
    import sys

    if "--plot" in sys.argv or "--plot-only" in sys.argv:  # re-draw from the saved table
        plot(pd.read_csv(OUT / "e2a_alignment.csv"))
    else:
        main()
