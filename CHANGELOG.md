# Changelog

## Unreleased — SMMM 2.0

The data hub was redesigned as **SMMM 2.0** (Simulation Management and Messaging Module, the name used in the
original Py-CoSim paper). Components publish *messages* that say what their time stamp means, and subscribe to what
they need; SMMM aligns them in time. Guide: [`docs/smmm.md`](docs/smmm.md); runnable tour:
`examples/11_messages_and_time/run.py`.

### New
- **Time meaning of outputs**: `kind=` on output ports — `instant`, `average`, `amount`, `hold`, `event` — and
  `interval="past"|"ahead"` for averages and amounts. Untyped outputs behave like `hold` (the 2.0 behaviour).
  `pycosim.Message(value, start=, end=, quality=)` publishes a value for another time span (e.g. a price announced
  for 16:00).
- **Aligned reads**: `read=` on input ports and on `connect()` — `latest` (default), `at`, `past`, `ahead`. SMMM
  returns time-weighted means, totals (amounts split in proportion to time), sample-and-hold values or event lists,
  so components with different step sizes and timing conventions exchange data without conversion code.
  `ctx.at(ref, when)` and `ctx.window(ref, start, end)` give the same answers for any port. `Results.alignment`
  reports reads that had to extrapolate. A `past` read of a value that only becomes valid at `t` creates no
  same-step dependency (an alternative to `delay=True` for breaking algebraic loops).
- **Patterns and topics**: `sim.subscribe(input, source)` (`connect` with the arguments in reading order); sources
  may be glob patterns (`"*.P_elec"`, resolved at initialization, so components added later are included) or
  topics (`topic="grid/price"` on an output). `Results.meta["topics"]`.
- **Quality**: every message carries a flag (`ok`, `clipped`, `fallback`, `late`, `replayed`); outputs can declare
  `bounds=` and `on_invalid="clip"|"last"|"default"|"error"`. `Results.quality` counts flagged messages.
- **Record/replay**: `CoSimulation(..., record=[names] | True)` keeps the messages (`Results.messages`,
  `messages.jsonl`); `sim.replay(name, run_folder | Results)` replaces a component by its recorded messages —
  bit-for-bit reproduction of runs with non-deterministic components, and counterfactual replay into a modified
  system.
- **Provenance**: `CoSimulation(..., trace=True)` records which messages each component read (`Results.trace`,
  `trace.csv`); `Results.lineage(ref, when, depth)` follows a value back through the chain.
- **Asynchronous components**: `Component.make_async(delay=, deadline=, on_deadline=)` runs `step()` in a background
  thread; the answer is published `delay` later in *simulation* time (new phase 0 of the master step), so results
  do not depend on wall-clock latency and several slow components (LLMs, web APIs) overlap. A missed wall-clock
  deadline keeps the previous outputs (flagged `late`) or stops the run. The context of an asynchronous component
  shows the state of the step its inputs were taken at. `Results.meta["asynchronous"]`.
- **EnergyPlus internal sub-steps**: `EnergyPlusComponent(..., timestep=60, step_size="15min")` runs EnergyPlus at
  1 minute and exchanges every 15 minutes; actuators are held and outputs aggregated by their time meaning.
  A sensor's kind can be given as a third element `(variable, key, kind)` or `{"variable", "key", "kind"}`.
- Ports: `Component.set_port(name, **attrs)`, `add_input(..., read=)`, `add_output(..., kind=, interval=, bounds=,
  on_invalid=, topic=)`; `describe()` shows each port's time meaning.
- Library components declare their time meanings (weather, PV and temperatures `instant`; RC-building powers
  `average/past`; `Battery.P` and `HouseholdOccupancy` `average/ahead`; prices, set-points and commands `hold`).
  `TimeSeriesSource(kind=, interval=)`, `DailyProfile(kind="hold")`.
- `GridMeter` has new inputs `P_load`, `P_gen`, `P_bat` and an `aligned` option (see below).
- `Results.save`/`load` handle `quality.csv`, `alignment.csv`, `trace.csv` and `messages.jsonl`; `meta.json` lists
  `port_kinds`, `topics`, `asynchronous`, `replayed` and `recorded`.
- Documentation: new page *Messages and time (SMMM)*; concepts, writing components, EnergyPlus, component library,
  AI/RL, scenarios and FAQ updated; example 11.

### Changed behaviour
- **`GridMeter` is aligned by default** (`aligned=True`): `P_load`, `P_gen`, `P_bat`, `price` and `export_price` are
  read as the mean over the meter's step that just ended. Energy from EnergyPlus or `RCBuilding` connected to
  `P_net` is now priced with the tariff of its own interval (2.0 used the tariff of the following step, about
  1–2 % of a week's cost at 15-minute steps in our tests), also for meters slower than the building. `P_net` itself
  is still read `latest`, so power that describes the *coming* step (`Battery.P`, a feedthrough EV charger) added to
  `P_net` is now priced with the previous step's price — connect it to `P_bat`/`P_load` instead. `delay=True` on a
  connection to an aligned meter input now raises a `ConfigurationError`. `GridMeter(name, aligned=False)` restores
  the 2.0 meter exactly.
- **EnergyPlus outputs are typed**: sensors and W/kW meters `average`, J/Wh/kWh/MJ meters `amount` (both
  `interval="past"`), internal variables `hold`. The published values are unchanged; the types only matter for
  aligned reads.
- Nothing else changes for code that uses `connect()` without `read=`: `latest` reads, untyped outputs, execution
  order and results are as in 2.0.

### Known limitations
- `sim.replay()` replays messages by master step number and does not check that the clock (start, step) matches the
  recorded run.
- YAML scenarios cannot set `read:` on a connection (set it on a function component's input port), make a component
  asynchronous or replay one.

## 2.0.0 — complete rewrite

Py-CoSim is now an installable Python package (`pip install -e .`, import `pycosim`) instead of a set of notebooks.

### New
- **Component model** with explicit input/output ports, function components (`@pycosim.component`), class components,
  feedthrough vs. state-based (non-feedthrough) semantics and multi-rate step sizes.
- **Master algorithm** (`CoSimulation`): three-phase step, topological ordering of the data-flow graph, algebraic-loop
  detection with the offending cycle in the message, delayed (Jacobi) connections, fan-in with reducers, transforms,
  reproducible per-component random generators, step-by-step and generator APIs.
- **EnergyPlus as a steppable component** (`EnergyPlusComponent`): lock-step handshake inside EnergyPlus callbacks,
  time-stamp verification at every step, sensors/meters (J, Wh, kWh, MJ, W, kW)/internal variables/actuators,
  releasing actuators with `None`, automatic run-period and time-step synchronisation, weather-file based sizing,
  daylight-saving control, `preprocess` hook, **one worker process per EnergyPlus instance** (parallel and isolated).
- **`EnergyPlusModel`**: IDF/epJSON editing through EnergyPlus' own converter (run period, time step, sizing,
  outputs, floor-area scaling, ...).
- **Handle discovery**: `pycosim handles` / `list_available_handles`, and "did you mean" suggestions for misspelled
  sensor/actuator names.
- **Data bus** replacing the CSV-file "SMMM": in-memory, time-indexed, history queries, event log, tidy results,
  error reports with the failing component's inputs.
- **Observers**: progress bar, live plots (online visualisation), streaming CSV recorder.
- **Component library**: EPW weather + solar position, time series, daily profiles, RC building, PV, battery,
  time-of-use tariff, grid meter, thermostat schedule, pre-cooling, PI, peak-shaving battery dispatch, NHTS household
  occupancy (+ MLP occupancy model), LLM supervisor with Anthropic/OpenAI/Ollama backends, guardrails, fault injection
  and record/replay.
- **Reinforcement learning**: `pycosim.gym.ExternalAgent` + `CoSimEnv` (Gymnasium API; the end of the
  co-simulation window is reported as `truncated`, several external agents may coexist).
- Function components: returning `None` from a single-output function publishes `None` (e.g. releases an
  EnergyPlus actuator); return `pycosim.HOLD` to keep the previous output. Inputs whose source publishes
  `None` fall back to their default.
- **YAML scenarios** and a CLI: `pycosim doctor | handles | run | version`.
- Nine runnable examples, documentation, test-suite and CI.

### Fixed (compared with 0.x)
- Concurrent EnergyPlus instances in threads of one process deadlock (EnergyPlus is not thread-safe) → worker processes.
- Race conditions and file-count based synchronisation of per-step CSV files → in-memory bus with an explicit clock.
- Time stamps from `current_time`/`minutes`, which follow adaptive HVAC sub-steps → zone-time-step based stamps.
- Occupancy from NHTS diaries counted people as "at home" whenever they were not travelling → home before the first
  departure and after the last return; household members without trips stay home.
- Hard-coded EnergyPlus path, global API/state objects, unchecked handles, and an API key in source code removed.

## 0.0.2 — legacy
Notebook-based prototype described in the SSRN preprint 4572925; preserved in the git tag `v0.1-legacy`.
