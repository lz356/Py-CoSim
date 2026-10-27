# Changelog

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
- **Reinforcement learning**: `pycosim.gym.ExternalAgent` + `CoSimEnv` (Gymnasium API).
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
