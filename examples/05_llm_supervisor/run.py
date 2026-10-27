"""Example 05 - An LLM as the supervisory controller of an EnergyPlus building.

Every hour (the building runs at 15-min steps: a multi-rate co-simulation)
the LLM receives the operator's intent and live measurements (zone and
outdoor temperatures, electricity price, power) and returns cooling/heating
set-points as JSON. A guardrail layer validates, clips and rate-limits the
answer and falls back safely on errors. Every prompt and response is stored
in the run's event log (``output/<run>/events.csv``).

Backends (``--backend``):

* ``offline``   deterministic keyword rules, NOT an LLM (default; no key needed)
* ``anthropic`` Claude via the official SDK (``pip install anthropic``; ANTHROPIC_API_KEY)
* ``openai``    OpenAI or any OpenAI-compatible server (OPENAI_API_KEY, ``--base-url``)
* ``ollama``    a local model served by Ollama (``--model llama3.1``)

Add ``--faults 0.3`` to inject malformed / out-of-range / refused / timed-out
answers and watch the guardrails keep the building safe.

    python run.py --intent "This is a nursing home; residents are elderly." --days 3
    python run.py --backend anthropic --record llm_log.jsonl      # record a real run
    python run.py --replay llm_log.jsonl                          # replay it exactly
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pandas as pd  # noqa: E402

import pycosim as pc  # noqa: E402
from pycosim.components import (  # noqa: E402
    AnthropicBackend, FaultInjectionBackend, LLMSupervisor, OfflineStandInBackend, OllamaBackend, OpenAIBackend,
    RecordingBackend, ReplayBackend, TimeOfUseTariff,
)
from _common import TUCSON_EPW, SMALL_OFFICE  # noqa: E402

INTENTS = {
    "comfort": "This building is a nursing home. Residents are elderly; keep them comfortable at all times.",
    "office": "This is a small office open on weekdays from 7 am to 7 pm. Operate it normally.",
    "economy": "Minimise electricity cost, especially during the evening price peak, while keeping occupants "
               "reasonably comfortable.",
}


def make_backend(args):
    if args.replay:
        return ReplayBackend(args.replay)
    if args.backend == "offline":
        backend = OfflineStandInBackend()
    elif args.backend == "anthropic":
        backend = AnthropicBackend(model=args.model or "claude-opus-5-5")
    elif args.backend == "openai":
        if not args.model:
            raise SystemExit("--model is required for the openai backend")
        backend = OpenAIBackend(model=args.model, base_url=args.base_url or "https://api.openai.com/v1")
    elif args.backend == "ollama":
        backend = OllamaBackend(model=args.model or "llama3.1")
    else:  # pragma: no cover
        raise SystemExit(f"unknown backend {args.backend}")
    if args.faults > 0:
        f = args.faults / 5
        backend = FaultInjectionBackend(backend, p_malformed=f, p_out_of_range=f, p_extra_keys=f, p_refusal=f,
                                        p_timeout=f, seed=args.seed)
    if args.record:
        backend = RecordingBackend(backend, args.record)
    return backend


def build(intent: str, backend, days: int = 3, name: str = "05_llm", seed: int = 0,
          output_dir: Path = HERE / "output") -> pc.CoSimulation:
    start = pd.Timestamp("2023-07-17")
    sim = pc.CoSimulation(start=start, end=start + pd.Timedelta(days=days), step="15min", name=name,
                          output_dir=output_dir, seed=seed, log_level="WARNING")
    sim.add(pc.EnergyPlusComponent(
        "office", SMALL_OFFICE, TUCSON_EPW,
        sensors={"T_zone": ("Zone Mean Air Temperature", "Core_ZN"),
                 "T_out": ("Site Outdoor Air Drybulb Temperature", "Environment")},
        meters={"P_elec": ("Electricity:Facility", "kW")},
        actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH"),
                   "htg_sp": ("Schedule:Compact", "Schedule Value", "HTGSETP_SCH")},
        daylight_saving=False,  # Arizona does not observe daylight-saving time
    ))
    sim.add(TimeOfUseTariff("tariff", peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.08))
    sim.add(LLMSupervisor(
        "llm", backend, intent,
        setpoints={"clg_sp": (21.0, 30.0, 24.0), "htg_sp": (15.0, 23.0, 20.0)},
        observations={"T_zone": "core zone air temperature [C]", "T_out": "outdoor air temperature [C]",
                      "price": "electricity price [$/kWh]", "P_elec": "building electric power [kW]"},
        step_size="1h", max_change=3.0, timeout=60.0, occupied_hours=(7, 19),
    ))
    for port in ("T_zone", "T_out", "P_elec"):
        sim.connect(f"office.{port}", f"llm.{port}")
    sim.connect("tariff.price", "llm.price")
    sim.connect("llm.clg_sp", "office.clg_sp")
    sim.connect("llm.htg_sp", "office.htg_sp")
    return sim


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--intent", help="operator intent (default: run the three built-in intents)")
    parser.add_argument("--backend", default="offline", choices=["offline", "anthropic", "openai", "ollama"])
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--faults", type=float, default=0.0, help="total probability of an injected fault")
    parser.add_argument("--record", help="append every LLM exchange to this JSON-lines file")
    parser.add_argument("--replay", help="replay a recorded JSON-lines file instead of calling a backend")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    intents = {"custom": args.intent} if args.intent else INTENTS
    results = {}
    for key, intent in intents.items():
        sim = build(intent, make_backend(args), args.days, name=f"05_llm_{key}", seed=args.seed)
        res = results[key] = sim.run(progress=True)
        stats = res.meta["component_metadata"]["llm"]
        h = res.meta["step_s"] / 3600
        print(f"[{key}] backend={stats['backend']} calls={stats['calls']} valid={stats['valid']} "
              f"fallback={stats['fallback']} clipped={stats['clipped']} rate_limited={stats['rate_limited']} | "
              f"energy={res['office.P_elec'].sum() * h:.1f} kWh, mean T_zone={res['office.T_zone'].mean():.2f} C")
        last = res.events[res.events.kind == "llm"].iloc[-1]
        print(f"    last LLM answer: {str(last['response'])[:160]}")

    if not args.no_plot:
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
        for key, res in results.items():
            axes[0].step(res.df.index, res["llm.clg_sp"], where="post", label=f"{key}: cooling set-point")
            axes[1].plot(res.df.index, res["office.P_elec"], lw=1, label=key)
        axes[0].set_ylabel("Set-point [C]")
        axes[1].set_ylabel("Building power [kW]")
        for ax in axes:
            ax.grid(alpha=0.3)
            ax.legend(fontsize=8)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(HERE / "output" / "llm_supervisor.png", dpi=150)


if __name__ == "__main__":
    main()
