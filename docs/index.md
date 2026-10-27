# Py-CoSim documentation

**Py-CoSim** is a Python co-simulation framework for buildings and everything around them.
It runs EnergyPlus building models together with any other Python model — weather data,
PV arrays, batteries, electricity tariffs, controllers, machine-learning models, large
language models (LLMs) and reinforcement-learning (RL) agents — on **one synchronized clock**.

```
weather ──T_out──► office (EnergyPlus) ──T_zone──► controller ──clg_sp──┐
tariff  ──price──────────────────────────────────► controller            │
                   office ◄───────────────────────────────────────────────┘
```

You describe *what* is connected to *what*; Py-CoSim decides *when* each part runs,
moves the data between them, keeps EnergyPlus in lock-step with the rest, and collects
every exchanged value into one table.

## Who is it for?

Building-energy researchers and engineers who know some Python and want to

* test a control idea (pre-cooling, demand response, an ML or LLM controller) on a real
  EnergyPlus model without writing EnergyPlus Runtime Language (EMS) code,
* study many buildings at once (a campus, a neighbourhood, a community with shared PV and
  storage), with each building simulated in parallel,
* couple buildings with non-building models: travel behaviour and occupancy, the grid,
  electric vehicles, prices,
* train a reinforcement-learning agent on a building model, or put an LLM in the loop
  with safety guardrails and a full audit log.

You do **not** need to be a software engineer. A component can be a plain Python function:

```python
import pycosim as pc

@pc.component(outputs="clg_sp")
def thermostat(t, price=0.10):
    """Cooling set-point [C]: relax when electricity is expensive."""
    return 26.0 if price > 0.2 else 24.0
```

## A complete first co-simulation

This runs in about one second and needs no EnergyPlus (it uses a simple RC thermal model
of a house). Run it from the repository root:

```python
import pycosim as pc
from pycosim.components import EPWWeather, RCBuilding, TimeOfUseTariff

EPW = "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"

@pc.component(outputs="clg_sp")
def thermostat(t, price=0.10):
    if 7 <= t.hour < 22:                 # t is a datetime on the co-simulation clock
        return 26.0 if price > 0.2 else 24.0
    return 27.0

sim = pc.CoSimulation(start="2023-07-03", end="2023-07-10", step="15min", name="first_run")
sim.add(EPWWeather("weather", EPW),
        RCBuilding("house", floor_area=150),
        TimeOfUseTariff("tariff", peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.10),
        thermostat)
sim.connect("weather.T_out", "house.T_out")
sim.connect("weather.GHI", "house.GHI")
sim.connect("tariff.price", "thermostat.price")
sim.connect("thermostat.clg_sp", "house.clg_sp")

res = sim.run(progress=True)
print(res.summary())
fig = res.plot(["house.T_zone", "thermostat.clg_sp"], "house.P_elec", "tariff.price")
fig.savefig("first_run.png", dpi=150)      # or matplotlib.pyplot.show()
```

`res.df` is a pandas DataFrame with one column per output (`"house.T_zone"`, …) and one
row per 15-minute step. The same results are saved to `pycosim_runs/first_run/`.

To use EnergyPlus instead, replace `RCBuilding(...)` with an
[`EnergyPlusComponent`](energyplus.md) — the rest of the script stays the same.

## What is in the box

| Area | What you get |
|---|---|
| Core engine | `CoSimulation` master with a fixed time grid, multi-rate components, automatic execution order, algebraic-loop detection, error reports |
| EnergyPlus | `EnergyPlusComponent` (sensors, meters, actuators), one worker process per building (parallel), automatic run-period/time-step synchronisation, handle discovery (`pycosim handles`), `EnergyPlusModel` for editing IDF/epJSON |
| Component library | Weather (EPW), time series, schedules, RC building, PV, battery, time-of-use tariff, grid meter, thermostat/pre-cooling/PI/peak-shaving controllers, NHTS household occupancy |
| AI | `LLMSupervisor` with guardrails (Anthropic Claude, OpenAI-compatible, Ollama backends; record/replay; fault injection); Gymnasium environment for RL (`pycosim.gym`) |
| Workflow | YAML scenarios and the `pycosim` command line, live plots, progress bar, streaming CSV, `Results` object with save/load/plot |

## Map of the documentation

| Page | Read it when you want to … |
|---|---|
| [Installation](installation.md) | install Py-CoSim and EnergyPlus and check that everything is found |
| [Concepts](concepts.md) | understand components, ports, the clock, the three-phase step, timing and reproducibility (**read this once**) |
| [Writing components](writing-components.md) | turn your own model, controller or ML model into a component |
| [EnergyPlus](energyplus.md) | couple EnergyPlus models: sensors, meters, actuators, run period, executors, troubleshooting |
| [Component library](component-library.md) | look up the inputs, outputs and parameters of every built-in component |
| [AI and RL](ai-and-rl.md) | put an LLM in the control loop, or train a reinforcement-learning agent |
| [Scenarios and CLI](scenarios-and-cli.md) | describe a co-simulation in YAML, use the `pycosim` command, live plots and `Results` |
| [Examples](examples.md) | run and adapt the nine example scripts |
| [Migrating from 0.x](migration-from-0.x.md) | move code from the original notebook-based Py-CoSim |
| [FAQ and troubleshooting](faq.md) | fix a problem quickly |

## Suggested learning path

1. [Install](installation.md) and run `pycosim doctor`.
2. Run [example 01](examples.md#01--quickstart-no-energyplus) and read [Concepts](concepts.md).
3. Run [example 02](examples.md#02--energyplus-basics) to see EnergyPlus in the loop, then read
   [EnergyPlus](energyplus.md).
4. Write your first own component with [Writing components](writing-components.md) and
   [example 09](examples.md#09--writing-your-own-component-ev-charger).
5. Pick the examples closest to your research question (03–08) and adapt them.

## Getting help

* Every failure writes an `error_report.txt` into the run's output folder with the failing
  component, the simulation time, the inputs it received and the traceback.
* `sim.describe()` prints the components, connections and execution order before you run.
* Check the [FAQ](faq.md); report problems at <https://github.com/lz356/Py-CoSim/issues>.
