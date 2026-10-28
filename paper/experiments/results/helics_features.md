# HELICS 3.6 vs Py-CoSim: feature comparison

HELICS cells are based on the HELICS documentation (docs.helics.org, "latest" = 3.6.x, checked
2026-10-28), with the source for each cell given. Py-CoSim cells describe the `smmm2` branch
(`src/pycosim`). Lines marked *measured* were checked in `helics_comparison.py`
(helics 3.6.1 from PyPI, EnergyPlus 22.2, Windows 11).

| Feature | HELICS 3.6 | Py-CoSim (SMMM) |
|---|---|---|
| **Time meaning on values** | None. A publication/input has a data type, units, default, `only_update_on_change`/tolerance, targets, tags. Nothing says whether a value is a sample, an interval mean, an amount or a held set-point. A reader gets the most recent value delivered by its granted time. [1] | Each output port has a `kind` (`instant`, `average`, `amount`, `hold`, `event`) and, for interval kinds, `past`/`ahead`. Every message carries `[start, end)` and a quality code (`core/messages.py`). |
| **Alignment across rates / intervals** | Each federate's grant times are set by `period`/`offset`. The time coordinator guarantees causality, but there is no aggregation over the reader's step. *Measured:* a value published at *t* is read at *t + period* by default; with `wait_for_current_time_update` (the federate "is the last one granted a given time" [2]) it is read at *t*. Interval semantics (pairing a past-interval power with the price that applied to it) had to be hand-coded in the meter (`meter_fed_aligned.py`). [2] | Consumers choose `read=` `latest` / `at` / `past` / `ahead`. SMMM returns time-weighted means of instant/average/hold signals and totals of amount signals over the consumer's own step. It also reports coverage and extrapolated reads (`alignment.csv`). *Measured:* `GridMeter(aligned=True)` matches the hand-coded HELICS meter. |
| **Fan-in reductions** | Multi-source inputs with `multi_input_handling_method` (sum, average, max, min, and, or, diff, vectorize). These act across sources at one grant, not over time. [3] | `reduce=` `sum`/`mean`/`max`/`min`/`list`/callable, applied after the per-source time alignment. |
| **Pattern subscriptions** | Yes, through the separate `helics_connector` app: a match file connects interfaces by regular expression and by tags, without changing the federates. Inputs can also list several `targets`. [4] [1] | Built into `connect`/`subscribe`: glob patterns (`"*.P_elec"`) and topics (`"grid/price"`), resolved when the simulation is initialized. |
| **Record / replay** | Yes, with separate apps. The Recorder captures values or messages of chosen publications or endpoints "in a format the Player can read". The Clone app records one federate's values and interfaces so that a Player can stand in for it. [5] [6] | Built in: `CoSimulation(record=...)` stores every message with its time envelope and quality (`messages.jsonl`). `sim.replay(name, run)` swaps a component for its recorded messages, which are flagged `replayed`. |
| **Quality flags** | No per-value quality or status field in the value interface [1]. Validity must be encoded by the user, for example as an extra publication. | Every message carries a quality code: `ok`, `clipped`, `fallback`, `late`, `replayed`. Port bounds with `on_invalid` clip, fall back or raise. |
| **Asynchronous / slow components, deadlines** | Federates run concurrently in their own processes. There are asynchronous time requests [18] and callback federates [19]. `real_time` mode with `rt_lag`/`rt_lead` keeps simulated time within a wall-clock window [7]. `grant_timeout` is "primarily for diagnostics" and "cannot generate an error" [8]. There is no built-in deadline-then-fallback-value policy. | `make_async(delay, deadline, on_deadline)`: a feedthrough component (for example an LLM or web API) runs in a background thread. Its answer applies after a simulated decision delay. When it misses a wall-clock deadline, the previous outputs are re-published and flagged `late` (or an error is raised). |
| **Co-iteration at one time step** | Yes, with iterative time requests [9]. | No. Algebraic loops are detected and must be broken with `delay=True` or a `past` read. |
| **Unit handling** | Automatic unit checking and conversion between double publications and inputs when both declare units [1]. | Units are metadata only. Conversion is a user `transform=` on the connection. |
| **EnergyPlus support** | No EnergyPlus federate ships with HELICS, and EnergyPlus is not in the "Tools using HELICS" list [10]. Here it took a 73-line user federate built on the EnergyPlus Python API (`helics_feds/building_fed.py`), with hand-built time stamps. The model was prepared with a separate script. | `EnergyPlusComponent`: sensors, meters and actuators become ports, each with its time meaning. The RunPeriod, time step and daylight-saving time are synchronised with the clock. Each instance runs in its own worker process, and outputs are aggregated when EnergyPlus runs on a finer internal step. |
| **FMU support** | HELICS-FMI, a separate executable in its own repository (requires HELICS ≥ 3.3): "Currently FMI 2.0 is supported" [11]. | `FMUComponent` for FMI 2.0/3.0 co-simulation FMUs, through the optional `fmpy` package (`components/fmu.py`). |
| **Language support** | C and C++ (native), Python, MATLAB and Julia; Java, Octave, nim and C# bindings are also documented [12] [13]. Federates written in different languages join one federation. | Python only. Other tools join through Python wrappers (EnergyPlus API, FMUs, subprocesses, HTTP clients). |
| **Distributed execution and scale** | Yes, and this is HELICS' main strength. Federates are separate processes linked by zmq/tcp/udp/ipc/mpi cores [14]. They can run on several computers [15] or in broker hierarchies [16]. HELICS is "designed to support very-large-scale (10,000,000+ federates) co-simulations" [17]. | Single host. Components are Python objects in the master process; each EnergyPlus instance has its own local worker process, so EnergyPlus steps run in parallel on one machine. There is no multi-host transport. |
| **Orchestration** | `helics run --path=runner.json` (helics-cli) starts the broker and the federates [20]. | `CoSimulation(...).run()` in one Python script, or a YAML scenario. |

## Sources (HELICS)

1. Interface configuration: https://docs.helics.org/en/latest/user-guide/fundamental_topics/interface_configuration.html
2. Timing configuration (`period`, `offset`, `uninterruptible`, `wait_for_current_time_update`): https://docs.helics.org/en/latest/user-guide/fundamental_topics/timing_configuration.html
3. Multi-source inputs: https://docs.helics.org/en/latest/user-guide/advanced_topics/multiSourceInputs.html
4. helics_connector: https://docs.helics.org/en/latest/user-guide/advanced_topics/connector_fed.html
5. Recorder app: https://docs.helics.org/en/latest/references/apps/Recorder.html (Player: https://docs.helics.org/en/latest/references/apps/Player.html)
6. Clone app: https://docs.helics.org/en/latest/references/apps/Clone.html
7. Configuration options reference (`real_time`, `rt_lag`, `rt_lead`, `grant_timeout`, `terminate_on_error`): https://docs.helics.org/en/latest/references/configuration_options_reference.html
8. Timeouts: https://docs.helics.org/en/latest/user-guide/advanced_topics/timeouts.html
9. Iteration: https://docs.helics.org/en/latest/user-guide/advanced_topics/iteration.html
10. Tools using HELICS: https://docs.helics.org/en/latest/references/Tools_using_HELICS.html
11. HELICS-FMI: https://github.com/GMLC-TDC/HELICS-FMI
12. API reference (C, C++, Python, MATLAB, Julia): https://docs.helics.org/en/latest/references/api-reference/index.html
13. Language bindings (Java, Octave, nim, C#): https://docs.helics.org/en/latest/user-guide/installation/language.html
14. Cores: https://docs.helics.org/en/latest/user-guide/advanced_topics/CoreTypes.html
15. Multi-compute-node co-simulation: https://docs.helics.org/en/latest/user-guide/advanced_topics/broker_multicomputer.html
16. Broker hierarchies: https://docs.helics.org/en/latest/user-guide/advanced_topics/broker_hierarchies.html
17. HELICS documentation home: https://docs.helics.org/en/latest/index.html
18. C API (`helicsFederateRequestTimeAsync`): https://docs.helics.org/en/latest/references/api-reference/C_API.html
19. Callback federates: https://docs.helics.org/en/latest/user-guide/advanced_topics/CallbackFederate.html
20. Running HELICS co-simulations (`helics run`): https://docs.helics.org/en/latest/user-guide/fundamental_topics/helics_run.html
18. C API (`helicsFederateRequestTimeAsync`): https://docs.helics.org/en/latest/references/api-reference/C_API.html
19. Callback federates: https://docs.helics.org/en/latest/user-guide/advanced_topics/CallbackFederate.html
20. Running HELICS co-simulations (`helics run`): https://docs.helics.org/en/latest/user-guide/fundamental_topics/helics_run.html

## Measured on the same scenario

See `helics_comparison.csv/.json`. HELICS with latest-value meters reproduces Py-CoSim's naive
meter exactly. Neither tool can do better than "latest × latest" unless the time meaning of the
values is known. Py-CoSim takes it from the message; in HELICS the user has to hand-code it.
