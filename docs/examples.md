# Examples

The runnable examples live in `examples/`. Each is a single `run.py` (example 08 is a YAML
file) with a docstring that explains it. Run them **from the repository root** with the
environment activated:

```bash
python examples/01_quickstart_no_energyplus/run.py
```

(They also work from any other folder: each script finds the shared files itself.)

Shared material:

| File | Content |
|---|---|
| `examples/_common.py` | paths, the zone names of the models, and `make_office(name, **kwargs)` — the DOE small office as an `EnergyPlusComponent` with zone temperature sensors `T_core_zn`, `T_perimeter_zn_1` … `_4`, `T_out`, meters `P_facility` and `P_hvac` [W], and the cooling set-point actuator `clg_sp` |
| `examples/models/small_office.idf` | DOE reference small office (511 m², 5 zones, packaged rooftop units), EnergyPlus 22.2 |
| `examples/models/apartment_midrise.idf` | PNNL ASHRAE 90.1-2019 mid-rise apartment prototype (23 dwellings + office), EnergyPlus 22.2 |
| `examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw` | TMY3 weather for Tucson, AZ (no daylight saving time, hence `daylight_saving=False` in the examples) |

Results go to `examples/<example>/output/<run name>/` (time series, events, timing,
metadata, and EnergyPlus files per building). Most scripts also save a PNG figure there; add
`--no-plot` to skip it. Run times below were measured on a desktop PC and are indicative; the
first EnergyPlus run also converts the IDF files (a few seconds, cached afterwards).

| # | Topic | EnergyPlus? | Default run time |
|---|---|---|---|
| 01 | quickstart: RC house, tariff, thermostat function | no | ~1 s |
| 02 | EnergyPlus basics: sensors, meters, actuators | yes | ~10 s |
| 03 | time-of-use pre-cooling, baseline vs pre-cool | yes | ~15 s |
| 04 | community: N buildings + PV + battery, 4 strategies | yes | minutes |
| 05 | LLM supervisory control with guardrails | yes | ~15 s offline |
| 06 | NHTS travel behaviour → apartment occupancy | yes | a few minutes |
| 07 | reinforcement learning (Gymnasium) | optional | 20–60 s |
| 08 | YAML scenario and the CLI | no (optional) | ~1 s |
| 09 | writing your own component (EV charger) | no | ~1 s |
| 11 | messages and time (TIDE): alignment, patterns, topics, quality, async AI stand-in, replay, lineage | no | ~15 s |

---

## 01 — Quickstart (no EnergyPlus)

**Demonstrates:** the basic workflow — `CoSimulation`, built-in components (`EPWWeather`,
`RCBuilding`, `TimeOfUseTariff`), a controller written as a plain function with
`@pc.component`, `connect`, `run`, `Results`.

```bash
python examples/01_quickstart_no_energyplus/run.py
python examples/01_quickstart_no_energyplus/run.py --no-plot
```

**Run time:** about 1 s (plus a few seconds to import pandas/matplotlib).

**Output:** the `describe()` overview, a summary, energy/cost/max temperature, and
`output/quickstart.png`.

**Make it your own:** change the `thermostat` function (it is just Python), `floor_area`, the
tariff's `peak_hours` and prices, or the dates. Then replace `RCBuilding("house", ...)` by an
`EnergyPlusComponent` (see example 02) — the connections stay the same if you name the ports
alike.

## 02 — EnergyPlus basics

**Demonstrates:** an EnergyPlus model as a component: sensors (zone temperatures, outdoor
temperature), meters in W, an actuator overriding the cooling set-point schedule, a
`ThermostatSchedule` controller, optional live plotting, and where EnergyPlus' files go.

```bash
python examples/02_energyplus_basics/run.py                 # 7 days
python examples/02_energyplus_basics/run.py --days 2 --live # watch it run
python examples/02_energyplus_basics/run.py --release       # leave the actuator unconnected
```

Options: `--days N` (7), `--live` (online plot), `--release` (EnergyPlus' own schedule runs —
compare with the controlled run), `--no-plot`.

**Run time:** ~10 s for 7 days (one EnergyPlus process).

**Output:** EnergyPlus version and warning counts, `output/energyplus_basics.png`, and the
EnergyPlus files in `output/02_energyplus_basics/office/eplus/`.

**Make it your own:** list your model's names with
`pycosim handles <your.idf> -w <your.epw> --kind actuator`, then edit `make_office` in
`examples/_common.py` (or copy it) with your sensors, meters and actuators.

## 03 — Time-of-use pre-cooling

**Demonstrates:** comparing two scenarios of the same office for a summer work week:
*baseline* (24 °C during occupied hours) and *precool* (22 °C for 3 h before the 16–21 h price
peak, 26.5 °C during it), using `PrecoolingController`, `TimeOfUseTariff` and a `GridMeter`
that integrates cost. Prints energy, on-peak energy, cost, peak demand and discomfort, and
the relative change.

```bash
python examples/03_tou_precooling/run.py
python examples/03_tou_precooling/run.py --days 2 --no-plot
```

Options: `--days N` (5, starting on Monday 2023-07-17), `--no-plot`.

**Run time:** ~15 s (two runs of ~6 s).

**Output:** a KPI table, `output/precooling.png`, runs `03_baseline` and `03_precool`.

**Make it your own:** change `precool_sp`, `peak_sp`, `precool_hours`, the tariff, or replace
the controller by your own function; add KPIs in `kpis()`.

## 04 — Community with PV and battery

**Demonstrates:** a grid-interactive community: *N* small offices (each its own EnergyPlus
process, floor areas varied ±10 % with `preprocess` + `scale_floor_area`) share a PV array,
a battery and one grid connection. Four strategies are compared: `baseline` (PV only),
`precool`, `battery` (PV surplus storage, on-peak discharge, peak shaving) and
`coordinated` (both). Shows fan-in with `reduce="sum"`, a function component built with
`FunctionComponent(lambda ...)`, and breaking the dispatch ↔ battery loop with
`delay=True`.

```bash
python examples/04_community_pv_battery/run.py --buildings 4 --days 2
python examples/04_community_pv_battery/run.py --strategies baseline coordinated
python examples/04_community_pv_battery/run.py          # 10 buildings, 7 days, 4 strategies
```

Options: `--buildings N` (10), `--days N` (7), `--strategies` (any of `baseline precool battery
coordinated`; all by default), `--no-plot`.

**Run time:** about 10 s per strategy with 3–4 buildings and 2 days. The default (10
buildings × 7 days × 4 strategies) takes several minutes and benefits from many CPU cores
(all buildings simulate in parallel).

**Output:** a KPI table (also `output/kpis.csv`: energy, import, on-peak import, peak, cost,
demand charge, PV self-consumption, discomfort, wall time) and `output/community.png`.

**Make it your own:** change `pv_kw_per_bldg`, `battery_kwh_per_bldg` and the controller
settings in `build()`; mix building types by adding other models; replace
`PeakShavingController` with your own dispatch (e.g. an optimiser) — it only needs inputs
`P_load`, `soc`, `is_peak` and an output `P_set`.

## 05 — LLM supervisor

**Demonstrates:** an LLM as the hourly supervisory controller of the EnergyPlus office
(15-minute building, 1-hour decisions), with guardrails (bounds, rate limit, fallback), the
event log of every prompt and answer, fault injection, and record/replay. Three intents are
run by default: comfort (nursing home), office, economy.

```bash
python examples/05_llm_supervisor/run.py                                    # offline stand-in
python examples/05_llm_supervisor/run.py --intent "This is a nursing home; residents are elderly." --days 3
python examples/05_llm_supervisor/run.py --faults 0.3                       # test the guardrails
python examples/05_llm_supervisor/run.py --backend anthropic --record llm_log.jsonl
python examples/05_llm_supervisor/run.py --replay llm_log.jsonl
python examples/05_llm_supervisor/run.py --backend ollama --model llama3.1
python examples/05_llm_supervisor/run.py --backend openai --model <model> --base-url http://localhost:8000/v1
```

Options: `--intent TEXT` (default: the three built-in intents), `--backend
{offline,anthropic,openai,ollama}` (`offline`), `--model`, `--base-url`, `--faults P` (total
fault probability, split equally over five fault types), `--record FILE`, `--replay FILE`,
`--days N` (3), `--seed N` (0), `--no-plot`.

The default `offline` backend is **not an LLM** (deterministic keyword rules); it makes the
pipeline runnable without an API key. `anthropic` needs `pip install anthropic` and
`ANTHROPIC_API_KEY`; `openai` needs `OPENAI_API_KEY` (or a local OpenAI-compatible server).

**Run time:** ~15 s offline for the three intents. With a real LLM add the response time of
24 calls per simulated day per intent.

**Output:** per intent: call statistics (valid, fallback, clipped, rate-limited), energy and
mean zone temperature, the last LLM answer; `output/llm_supervisor.png`; and
`output/05_llm_<intent>/events.csv` with every prompt and response.

**Make it your own:** write your own intent, add observations (e.g. occupancy, PV output,
forecasts), change the set-point bounds and `max_change`, or wrap `make_backend()` with your
own `LLMBackend`.

## 06 — Travel behaviour and occupancy

**Demonstrates:** transportation × buildings. Each of the 23 dwellings of the mid-rise
apartment prototype is occupied by a household sampled from the 2017 NHTS
(`HouseholdOccupancy`); the number of people at home is written every 15 minutes into the
per-dwelling `People` actuators. Two thermostat policies are compared: *fixed* (23.9 °C) and
*smart* (set up to 26.7 °C when nobody is home, through `Zone Temperature Control` actuators
and a `transform`). All groups × policies run as **one** co-simulation, one EnergyPlus process
per apartment building. Also uses `sizing="weather_file"` and a `preprocess` hook that removes the
prototype's door-switch thermostat reset (EMS program) and window ventilation, so that both policies
set the zone cooling set-points on equal terms.

```bash
python examples/06_travel_behavior_occupancy/run.py --days 2
python examples/06_travel_behavior_occupancy/run.py --groups long short ev nonev --replicates 2
```

Options: `--groups` (any of `long short ev nonev`; default `long short`), `--policies`
(`fixed smart`), `--replicates N` (1), `--days N` (7), `--no-plot`. The number of EnergyPlus
buildings is groups × policies × replicates (4 by default = 92 dwellings).

**Run time:** ~45 s for 2 days with the default 4 buildings; a few minutes for 7 days.

**Output:** mean cooling load, cooling electricity, peak-to-valley ratio and people at home
per group and policy (`output/summary.csv`), and `output/travel_behavior.png` (mean hourly
cooling-load profiles).

**Make it your own:** select households by size or urbanity (`hhsize=`, `urban=`), compare
EV and non-EV households, or feed `HouseholdOccupancy.total` into a grid or EV-charging
model.

## 07 — Reinforcement learning

**Demonstrates:** turning a scenario into a Gymnasium environment with `ExternalAgent` and
`CoSimEnv`; the agent picks the hourly cooling set-point to trade electricity cost under a
time-of-use tariff against comfort. A linear policy is trained with the cross-entropy
method (no extra RL library needed), compared with constant 24 °C and 26 °C policies, and then
run *inside* a normal co-simulation through `policy=`.

```bash
python examples/07_reinforcement_learning/run.py                     # RC house
python examples/07_reinforcement_learning/run.py --iterations 4 --population 8
python examples/07_reinforcement_learning/run.py --energyplus        # EnergyPlus office (slow)
```

Options: `--energyplus`, `--iterations N` (8), `--population N` (16), `--days N` (3 per
episode), `--seed N` (0). Needs `pip install -e ".[rl]"`.

**Run time:** 20–60 s with the RC house. With `--energyplus` every episode restarts
EnergyPlus (131 episodes with the defaults), so expect tens of minutes.

**Output:** returns per iteration, the trained policy's return, `policy_theta.npy` (the
trained parameters, overwritten on every run), and the first day of the trained policy's
set-points; run `07_trained_policy` in `output/`.

**Make it your own:** change `reward()`, the observations `OBS` or the action range
`SP_RANGE`; replace the cross-entropy loop by Stable-Baselines3 (see
[AI and RL](ai-and-rl.md#3-train-with-stable-baselines3-or-any-gymnasium-library)).

## 08 — YAML scenario

**Demonstrates:** a complete co-simulation (weather, RC house, tariff, PV, a Python rule,
a grid meter) described in `scenario.yaml` and run with the `pycosim` command; a function
component and a custom `reduce` imported from `my_rules.py`. An EnergyPlus variant is in the
file, commented out.

```bash
pycosim run examples/08_yaml_scenario/scenario.yaml
pycosim run examples/08_yaml_scenario/scenario.yaml --plot --name 08_variant
```

Options: `--name NAME`, `--plot`, `--quiet` (see [CLI](scenarios-and-cli.md#the-pycosim-command-line)).

**Run time:** under 1 s of simulation (a few seconds including start-up).

**Output:** `examples/08_yaml_scenario/output/<name>/`.

**Make it your own:** copy the folder, edit the YAML (paths are relative to the YAML file),
swap the RC house for the commented `EnergyPlusComponent` block, or add your own functions
to `my_rules.py`.

## 09 — Writing your own component (EV charger)

**Demonstrates:** a tutorial class component `EVCharger` with class-level ports, internal
state, `setup()`, reproducible randomness with `ctx.rng` (arrival/departure times, trip
energy), structured events with `ctx.log`, and `ctx.step_size`. The charger is coupled to a
house, a tariff and a grid meter, and plug-and-charge is compared with smart (off-peak)
charging.

```bash
python examples/09_custom_component_ev/run.py
python examples/09_custom_component_ev/run.py --days 14
```

Options: `--days N` (7).

**Run time:** under 1 s per run.

**Output:** the first EV events of each run (from `res.events`) and a table with EV energy,
household cost, peak demand and minimum SOC.

**Make it your own:** change the charger power, battery size or driving distances; make the
smart rule respond to PV output; add one charger per house with different names (each gets
its own random stream from `ctx.rng`).

## 11 — Messages and time

**Demonstrates:** the TIDE messaging features in one script without EnergyPlus
([Messages and time](tide.md)). An RC house, PV, a battery, a time-of-use tariff and a
"stochastic AI supervisor" are coupled. The supervisor is **not an LLM**: it picks set-points
at random, sometimes answers nonsense (40 °C) and sleeps to imitate a slow remote model. It is
made asynchronous with `make_async(delay="15min")`. An hourly `GridMeter` (aligned) subscribes
to `"*.P_elec"`, `"*.P_ac"`, the battery power and the price topic `"grid/price"`, and is
compared with a 15-minute meter. The live run is recorded (`record=["ai"]`, `trace=True`),
replayed (and checked to be identical), replayed again on a house with more PV
(counterfactual), and one battery decision is traced back with `lineage()`.

```bash
python examples/11_messages_and_time/run.py
python examples/11_messages_and_time/run.py --latency 0.1 --deadline 0.12
```

Options: `--days N` (3), `--latency S` (0.03 s per AI answer), `--deadline S` (none).

**Run time:** about 15 s (four runs of three days, most of it the AI stand-in's sleep).

**Output:** `describe()` with the kinds of every port; the alignment table (each meter input
read `past`, one extrapolated read at the start); the net grid import of the hourly and the
15-minute meter (identical); the quality table (nonsense set-points clipped, and with
`--deadline` some answers `late`); the cost of the live run, a second live run (different) and
the replay (identical); the counterfactual cost; and the lineage of the battery power at 16:15
on the first day — back through the AI's decision to the zone temperature, price and state of
charge it saw at 16:00, and to the weather before that.

**Make it your own:** replace `StochasticSupervisor` by an `LLMSupervisor` (keep
`make_async`); add a second `RCBuilding` and watch the meters pick it up through the pattern;
change `delay=` and see when the decisions apply; replay the recorded decisions into your own
variant of the house.
