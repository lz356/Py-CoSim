# Py-CoSim examples

Every example is a single, commented `run.py` you can run from the repository root and then modify.
Results (time series, events, figures) are written to the example's `output/` folder.

| Example | Run it | Time* | Shows |
|---|---|---|---|
| [01 Quick start](01_quickstart_no_energyplus/run.py) | `python examples/01_quickstart_no_energyplus/run.py` | ~1 s | function components, RC building, weather, tariff — no EnergyPlus needed |
| [02 EnergyPlus basics](02_energyplus_basics/run.py) | `python examples/02_energyplus_basics/run.py --live` | ~10 s | sensors, meters, actuators, live plot, releasing actuators (`--release`) |
| [03 Time-of-use pre-cooling](03_tou_precooling/run.py) | `python examples/03_tou_precooling/run.py` | ~20 s | comparing strategies, cost/comfort KPIs |
| [04 Grid-interactive community](04_community_pv_battery/run.py) | `python examples/04_community_pv_battery/run.py --buildings 4 --days 2` | 1–3 min | many EnergyPlus buildings in parallel, PV, battery, fan-in, delayed links (paper Case A) |
| [05 LLM supervisor](05_llm_supervisor/run.py) | `python examples/05_llm_supervisor/run.py --faults 0.3` | ~1 min | LLM in the loop, guardrails, fault injection, record/replay, multi-rate (paper Case B) |
| [06 Travel behaviour → occupancy](06_travel_behavior_occupancy/run.py) | `python examples/06_travel_behavior_occupancy/run.py --days 2` | 1–3 min | NHTS diaries drive 23 apartments, smart vs fixed thermostat (paper Case C) |
| [07 Reinforcement learning](07_reinforcement_learning/run.py) | `python examples/07_reinforcement_learning/run.py` | ~1 min | Gymnasium environment, training a policy, running it inside a co-simulation |
| [08 YAML scenario](08_yaml_scenario/scenario.yaml) | `cd examples/08_yaml_scenario && pycosim run scenario.yaml --plot` | ~1 s | a whole co-simulation without a script |
| [09 Custom component](09_custom_component_ev/run.py) | `python examples/09_custom_component_ev/run.py` | ~1 s | writing your own stateful, stochastic component (EV charger) |

\* on a laptop; EnergyPlus examples include EnergyPlus' sizing and warm-up.

Shared paths and the small-office helper live in [`_common.py`](_common.py); building models and weather in
[`models/`](models) and [`weather/`](weather).

## Using the LLM example with a real model

```bash
pip install anthropic                      # for Claude; set ANTHROPIC_API_KEY
python examples/05_llm_supervisor/run.py --backend anthropic --record claude_run.jsonl
python examples/05_llm_supervisor/run.py --replay claude_run.jsonl          # exact, offline replay

python examples/05_llm_supervisor/run.py --backend openai --model <model>    # OPENAI_API_KEY
python examples/05_llm_supervisor/run.py --backend ollama --model llama3.1  # local model via Ollama
```

The default `offline` backend is a deterministic keyword-rule stand-in (not an LLM) so that the example runs
without network access or API keys.
