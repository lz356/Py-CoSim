"""Case study B: LLM-in-the-loop supervisory control and guardrail robustness (paper Section 5.2).

The decision-making backend is the deterministic offline stand-in (NOT an
LLM), so that the experiment isolates and stress-tests the integration layer:
multi-rate scheduling, prompt construction, parsing, guardrails, logging and
fault tolerance. Swap in a real model with ``--backend anthropic|openai|ollama``
in examples/05_llm_supervisor.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import COLORS, FIG, OUT, load_example, set_style

from pycosim.components import FaultInjectionBackend, OfflineStandInBackend

ex = load_example("05_llm_supervisor")
WORK = OUT / "case_b"
DAYS = 5
FAULT_RATES = (0.0, 0.1, 0.2, 0.3, 0.5)


def run(intent_key: str, backend, name: str):
    sim = ex.build(ex.INTENTS[intent_key], backend, days=DAYS, name=name, output_dir=WORK)
    return sim.run()


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    intents = {}
    for key in ex.INTENTS:
        intents[key] = run(key, OfflineStandInBackend(), f"intent_{key}")
    rows = []
    for key, res in intents.items():
        h = res.meta["step_s"] / 3600
        occ = (res.df.index.hour >= 7) & (res.df.index.hour < 19)
        rows.append({"intent": key, "energy_kWh": res["office.P_elec"].sum() * h,
                     "mean_occupied_T": res.df.loc[occ, "office.T_zone"].mean(),
                     "mean_occupied_clg_sp": res.df.loc[occ, "llm.clg_sp"].mean(),
                     **{k: v for k, v in res.meta["component_metadata"]["llm"].items() if k not in ("intent",)}})
    intents_table = pd.DataFrame(rows)
    intents_table.to_csv(OUT / "case_b_intents.csv", index=False)
    print(intents_table.round(3).to_string())

    sweep, ref = [], None
    for p in FAULT_RATES:
        f = p / 5
        backend = FaultInjectionBackend(OfflineStandInBackend(), p_malformed=f, p_out_of_range=f, p_extra_keys=f,
                                        p_refusal=f, p_timeout=f, seed=11)
        res = run("economy", backend, f"faults_{int(p * 100):02d}")
        if ref is None:
            ref = res
        h = res.meta["step_s"] / 3600
        stats = res.meta["component_metadata"]["llm"]
        injected = pd.Series(backend.injected).value_counts().to_dict()
        sweep.append({
            "fault_rate": p, "calls": stats["calls"], "valid": stats["valid"], "fallback": stats["fallback"],
            "clipped": stats["clipped"], "injected_faults": int(sum(v for k, v in injected.items() if k != "none")),
            "min_clg_sp_applied": res["llm.clg_sp"].min(), "max_clg_sp_applied": res["llm.clg_sp"].max(),
            "energy_kWh": res["office.P_elec"].sum() * h,
            "energy_dev_%": (res["office.P_elec"].sum() / ref["office.P_elec"].sum() - 1) * 100,
            "mean_abs_dT_zone": (res["office.T_zone"] - ref["office.T_zone"]).abs().mean(),
            "llm_events_logged": int((res.events.kind == "llm").sum()),
        })
        print(sweep[-1], flush=True)
    sweep = pd.DataFrame(sweep)
    sweep.to_csv(OUT / "case_b_faults.csv", index=False)
    example_event = intents["comfort"].events[intents["comfort"].events.kind == "llm"].iloc[10]
    (OUT / "case_b_example_exchange.json").write_text(json.dumps(
        {"prompt": example_event["prompt"], "response": example_event["response"],
         "applied": example_event["applied"]}, indent=2, default=str))

    import matplotlib.pyplot as plt

    set_style()
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.0), gridspec_kw={"width_ratios": [1.6, 1]})
    window = slice("2023-07-17", "2023-07-18")
    ax = axes[0, 0]
    for c, (key, res) in zip(COLORS, intents.items()):
        sp = res["llm.clg_sp"].loc[window]
        ax.step(sp.index, sp, where="post", color=c, lw=1.2, label=key)
    ax.set_ylabel("Cooling set-point [°C]")
    ax.set_title("(a) Hourly decisions for three operator intents", loc="left")
    ax.legend(fontsize=7, ncol=3)
    ax = axes[1, 0]
    for c, (key, res) in zip(COLORS, intents.items()):
        T = res["office.T_zone"].loc[window]
        ax.plot(T.index, T, color=c, lw=1.0, label=key)
    ax.set_ylabel("Core zone T [°C]")
    ax.set_title("(b) EnergyPlus core-zone temperature (15-min)", loc="left")
    import matplotlib.dates as mdates

    for a in axes[:, 0]:
        a.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 6, 12, 18]))
        a.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[1, 0].set_xlabel("17–18 July 2023 (Monday–Tuesday)")
    ax = axes[0, 1]
    ax.bar(sweep.fault_rate * 100, sweep.fallback, width=6, color=COLORS[3], label="fallback (held value)")
    ax.bar(sweep.fault_rate * 100, sweep.clipped, width=6, bottom=sweep.fallback, color=COLORS[1], label="clipped")
    ax.set_xlabel("Injected fault rate [%]")
    ax.set_ylabel("Guardrail interventions")
    ax.set_title("(c) Guardrail activity", loc="left")
    ax.legend(fontsize=7)
    ax = axes[1, 1]
    ax.plot(sweep.fault_rate * 100, sweep["energy_dev_%"], "o-", color=COLORS[0], label="energy deviation [%]")
    ax.plot(sweep.fault_rate * 100, sweep.mean_abs_dT_zone, "s-", color=COLORS[2], label="mean |ΔT| [K]")
    ax.set_xlabel("Injected fault rate [%]")
    ax.set_title("(d) Effect on the building vs. fault-free run", loc="left")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "fig07_case_b_llm.png")
    print("figure written")


if __name__ == "__main__":
    main()
