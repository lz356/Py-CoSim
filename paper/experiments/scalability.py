"""Computational performance (paper Section 4.2).

S1  framework overhead: one office, EnergyPlus CLI vs Py-CoSim (process / thread executor), repeated.
S2  scalability: N offices co-simulated (each with a Python controller actuating every step)
    - serial  : one co-simulation per building, run one after another
    - parallel: all N buildings in one co-simulation (one EnergyPlus worker process each)
    with a check that parallel results are bit-identical to the serial ones.
S3  data-exchange micro-benchmark: Py-CoSim data bus vs the per-step CSV files of Py-CoSim 0.x.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from functools import partial
from pathlib import Path

import numpy as np
import pandas as pd

from common import FIG, OUT, machine_info, make_office, run_energyplus_cli, set_style

import pycosim as pc
from pycosim.core.bus import DataBus

START, DAYS = "2023-07-17", 7
WORK = OUT / "scalability"


def _scale(f, m):
    m.scale_floor_area(f)


def office_sim(names, factors, name, executor="process") -> pc.CoSimulation:
    sim = pc.CoSimulation(START, pd.Timestamp(START) + pd.Timedelta(days=DAYS), "15min", name=name,
                          output_dir=WORK, log_level="WARNING", save=False)

    @pc.component(outputs="clg_sp")
    def ctrl(t):
        return 22.0 if 13 <= t.hour < 16 else (26.5 if 16 <= t.hour < 21 else 24.0)

    sim.add(ctrl)
    for n, f in zip(names, factors):
        sim.add(make_office(n, preprocess=partial(_scale, float(f)), executor=executor))
        sim.connect("ctrl.clg_sp", f"{n}.clg_sp")
    return sim


def timed_run(sim):
    t0 = time.perf_counter()
    res = sim.run()
    return res, time.perf_counter() - t0


def overhead(repeats: int = 3) -> pd.DataFrame:
    rows = []
    sim = office_sim(["b"], [1.0], "probe")
    sim.initialize()  # writes the epJSON the CLI will run (same model as the co-simulation)
    for c in sim.components:
        c.abort()
    epjson = WORK / "probe" / "b" / "in.epJSON"
    import subprocess

    for r in range(repeats):
        rows.append({"mode": "EnergyPlus CLI", "rep": r, "wall_s": run_energyplus_cli(epjson, WORK / f"cli_{r}")})
        _, w = timed_run(office_sim(["b"], [1.0], f"one_process_{r}", executor="process"))
        rows.append({"mode": "Py-CoSim (process)", "rep": r, "wall_s": w})
        # EnergyPlus can run only once per Python process in-thread -> measure each repetition in a fresh interpreter
        code = ("import time, scalability as s; sim = s.office_sim(['b'], [1.0], 'one_thread_%d', executor='thread'); "
                "t0 = time.perf_counter(); sim.run(); print(time.perf_counter() - t0)" % r)
        out = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).parent), capture_output=True, text=True)
        rows.append({"mode": "Py-CoSim (thread)", "rep": r, "wall_s": float(out.stdout.strip().splitlines()[-1])})
    return pd.DataFrame(rows)


def scaling(ns=(1, 2, 4, 8, 16)) -> pd.DataFrame:
    rows = []
    for n in ns:
        names = [f"b{i:02d}" for i in range(n)]
        factors = np.random.default_rng(n).uniform(0.9, 1.1, n)
        t0 = time.perf_counter()
        serial = {}
        for nm, f in zip(names, factors):
            res, _ = timed_run(office_sim([nm], [f], f"serial_{n}_{nm}"))
            serial[nm] = res.df
        t_serial = time.perf_counter() - t0
        res_par, t_par = timed_run(office_sim(names, factors, f"parallel_{n}"))
        maxdiff = max(float((res_par.df[[c for c in res_par.df.columns if c.startswith(nm + ".")]]
                             - serial[nm][[c for c in serial[nm].columns if c.startswith(nm + ".")]]).abs().max().max())
                      for nm in names)
        rows.append({"n_buildings": n, "serial_s": t_serial, "parallel_s": t_par, "speedup": t_serial / t_par,
                     "max_abs_diff_parallel_vs_serial": maxdiff})
        print(rows[-1], flush=True)
    return pd.DataFrame(rows)


def bus_vs_csv(n: int = 2000) -> pd.DataFrame:
    """Per-exchange cost: in-memory data bus vs one CSV file per value (Py-CoSim 0.x)."""
    times = [pd.Timestamp(START).to_pydatetime()] * n
    bus = DataBus(times)
    bus.declare("a.x")
    t0 = time.perf_counter()
    for k in range(n):
        bus.begin_step(k)
        bus.publish("a.x", float(k))
        _ = bus.latest("a.x")
    t_bus = (time.perf_counter() - t0) / n
    tmp = Path(tempfile.mkdtemp())
    m = min(n, 300)
    t0 = time.perf_counter()
    for k in range(m):
        path = tmp / f"bldg_1_{k:03d}.csv"
        pd.DataFrame([float(k)]).to_csv(path)
        _ = pd.read_csv(path).iloc[0, 0]
    t_csv = (time.perf_counter() - t0) / m
    return pd.DataFrame([{"method": "Py-CoSim 2 data bus (publish + read)", "seconds_per_exchange": t_bus},
                         {"method": "Py-CoSim 0.x CSV file (write + read)", "seconds_per_exchange": t_csv}])


def master_overhead(n_components: int = 20, steps: int = 35040) -> float:
    """Master time per step and per component for trivial Python components (seconds)."""
    sim = pc.CoSimulation("2023-01-01", pd.Timestamp("2023-01-01") + pd.Timedelta(minutes=15 * steps), "15min",
                          save=False, log_level="WARNING")
    prev = None
    for i in range(n_components):
        c = pc.FunctionComponent(lambda t, x=0.0: x + 1.0, name=f"c{i}", outputs="y")
        sim.add(c)
        if prev:
            sim.connect(f"{prev}.y", f"c{i}.x")
        prev = f"c{i}"
    t0 = time.perf_counter()
    sim.run()
    return (time.perf_counter() - t0) / steps / n_components


def overhead_only(repeats: int = 5) -> None:
    """Re-measure the single-building overhead on an otherwise idle machine and update the summary."""
    WORK.mkdir(parents=True, exist_ok=True)
    ov = overhead(repeats)
    ov.to_csv(OUT / "scalability_overhead.csv", index=False)
    summary = json.loads((OUT / "scalability_summary.json").read_text())
    summary["overhead"] = ov.groupby("mode")["wall_s"].agg(["mean", "std"]).to_dict()
    summary["master_overhead_s_per_step_per_component"] = master_overhead()
    micro = bus_vs_csv()
    micro.to_csv(OUT / "exchange_microbenchmark.csv", index=False)
    summary["exchange"] = micro.set_index("method")["seconds_per_exchange"].to_dict()
    (OUT / "scalability_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(ov.groupby("mode")["wall_s"].agg(["mean", "std", "min"]))


def main(argv=None) -> None:
    argv = list(argv or sys.argv[1:])
    if argv and argv[0] == "--plot":
        plot()
        return
    if argv and argv[0] == "--overhead-only":
        overhead_only(int(argv[1]) if len(argv) > 1 else 5)
        return
    WORK.mkdir(parents=True, exist_ok=True)
    ns = tuple(int(x) for x in argv) or (1, 2, 4, 8, 16)
    info = machine_info()
    ov = overhead()
    ov.to_csv(OUT / "scalability_overhead.csv", index=False)
    ov_sum = ov.groupby("mode")["wall_s"].agg(["mean", "std"])
    print(ov_sum)
    sc = scaling(ns)
    sc.to_csv(OUT / "scalability.csv", index=False)
    micro = bus_vs_csv()
    micro.to_csv(OUT / "exchange_microbenchmark.csv", index=False)
    per_comp = master_overhead()
    summary = {"machine": info, "overhead": ov_sum.to_dict(), "master_overhead_s_per_step_per_component": per_comp,
               "exchange": micro.set_index("method")["seconds_per_exchange"].to_dict(), "steps": 672}
    (OUT / "scalability_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(json.dumps(summary, indent=2, default=float))

    plot(sc, info)


def plot(sc=None, info=None) -> None:
    """Figure 4 of the paper (can be redrawn from the saved CSV/JSON: python scalability.py --plot)."""
    import matplotlib.pyplot as plt

    if sc is None:
        sc = pd.read_csv(OUT / "scalability.csv")
    if info is None:
        info = json.loads((OUT / "scalability_summary.json").read_text())["machine"]
    cores = info.get("physical_cores") or 4
    set_style()
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.8))
    ax = axes[0]
    ax.plot(sc.n_buildings, sc.serial_s, "o-", label="Serial (one building at a time)")
    ax.plot(sc.n_buildings, sc.parallel_s, "s-", label="Py-CoSim parallel workers")
    ax.set_xlabel("Number of EnergyPlus buildings")
    ax.set_ylabel("Wall-clock time [s]")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_title("(a) Run time, 1 week at 15 min", loc="left")
    ax.legend()
    ax = axes[1]
    ax.plot(sc.n_buildings, sc.speedup, "o-", color="#009E73", label="Measured speed-up")
    ax.plot(sc.n_buildings, np.minimum(sc.n_buildings, cores), "k:", lw=1,
            label=f"Ideal ({cores} physical cores)")
    ax.set_xlabel("Number of EnergyPlus buildings")
    ax.set_ylabel("Speed-up [-]")
    ax.set_xscale("log", base=2)
    ax.set_title("(b) Parallel speed-up", loc="left")
    ax.legend()
    from matplotlib.ticker import FixedLocator, FixedFormatter

    for a in axes:
        a.xaxis.set_major_locator(FixedLocator(list(sc.n_buildings)))
        a.xaxis.set_major_formatter(FixedFormatter([str(int(n)) for n in sc.n_buildings]))
        a.xaxis.set_minor_locator(FixedLocator([]))
    fig.tight_layout()
    fig.savefig(FIG / "fig05_scalability.png")


if __name__ == "__main__":
    main()
