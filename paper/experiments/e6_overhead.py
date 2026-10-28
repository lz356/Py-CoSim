"""E6: cost of TIDE features (no EnergyPlus; run on an otherwise idle machine).

(1) Micro-benchmark of the message store: publish + latest read vs. publish +
    window read (past) for interval-average signals.
(2) A co-simulation of 50 producers (15-min interval averages) and one hourly
    meter that subscribes to all of them with a pattern, run with latest reads,
    aligned (past) reads, aligned + record, aligned + record + trace; wall time
    per component-step.

Output: results/e6_overhead.csv, results/e6_overhead.json
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from common import OUT

import pycosim as pc
from pycosim.core.bus import DataBus
from pycosim.core.messages import Signal

REPEATS = 5


def micro() -> dict:
    n = 100_000
    times = pd.date_range("2023-01-01", periods=n + 1, freq="1min").to_pydatetime().tolist()
    out = {}
    # latest: publish + dict read
    bus = DataBus(times, 60.0)
    bus.declare("a.x", 0.0, kind="average", interval="past")
    best = 1e9
    for _ in range(REPEATS):
        bus = DataBus(times, 60.0)
        bus.declare("a.x", 0.0, kind="average", interval="past")
        t0 = time.perf_counter()
        for k in range(n):
            bus.k = k
            bus.publish("a.x", float(k))
            bus._latest["a.x"]
        best = min(best, time.perf_counter() - t0)
    out["publish_plus_latest_us"] = 1e6 * best / n
    # window: publish + a 15-step (15-min) past window read every step
    best = 1e9
    for _ in range(REPEATS):
        sig = Signal("a.x", "average", "past", 60.0, 0.0)
        t0 = time.perf_counter()
        for k in range(n):
            s = k * 60.0
            sig.append(k, float(k), s - 60.0, s)
            sig.window(s - 900.0, s)
        best = min(best, time.perf_counter() - t0)
    out["publish_plus_window15_us"] = 1e6 * best / n
    return out


def build(mode: str, n_prod: int = 50, days: int = 30) -> pc.CoSimulation:
    sim = pc.CoSimulation("2023-01-01", pd.Timestamp("2023-01-01") + pd.Timedelta(days=days), "15min", save=False,
                          log_level="WARNING", record=(mode in ("aligned+record", "aligned+trace")) or None,
                          trace=(mode == "aligned+trace"))
    for i in range(n_prod):
        sim.add(pc.FunctionComponent(lambda t, i=i: float(i + t.hour), name=f"p{i:02d}",
                                     outputs={"P": {"default": 0.0, "kind": "average", "interval": "past"}}))
    sim.add(pc.FunctionComponent(lambda t, total=0.0: total, name="meter", outputs="E", step_size="1h"))
    sim.subscribe("meter.total", "p*.P", reduce="sum", read=None if mode == "latest" else "past")
    return sim


def macro() -> list:
    rows = []
    for mode in ("latest", "aligned", "aligned+record", "aligned+trace"):
        walls = []
        for _ in range(3):
            sim = build(mode)
            t0 = time.perf_counter()
            res = sim.run()
            walls.append(time.perf_counter() - t0)
        steps = res.meta["n_steps"]
        comp_steps = steps * 50 + steps // 4
        rows.append({"mode": mode, "wall_s": min(walls), "n_steps": steps,
                     "us_per_component_step": 1e6 * min(walls) / comp_steps,
                     "meter_reads": steps // 4,
                     "messages_recorded": 0 if res.messages is None else len(res.messages)})
        print(rows[-1], flush=True)
    return rows


def main() -> None:
    m = micro()
    print(m, flush=True)
    rows = macro()
    pd.DataFrame(rows).to_csv(OUT / "e6_overhead.csv", index=False)
    (OUT / "e6_overhead.json").write_text(json.dumps({"micro": m, "macro": rows}, indent=2))


if __name__ == "__main__":
    main()
