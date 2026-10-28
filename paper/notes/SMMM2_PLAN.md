# SMMM 2.0 — contribution plan

Status: agreed direction (2026-10-28). Code, experiments and manuscript are being built on branch `smmm2`.

## 1. The idea in one paragraph

Co-simulation is usually designed as *connecting software*: tool A is wired to tool B through an
interface (FMU, socket, adapter), and every pair of tools needs its own conversion of time steps
and units. Py-CoSim instead makes co-simulation happen through **messages**. Every component
publishes what it knows to the Simulation Management and Messaging Module (SMMM, introduced in the
original Py-CoSim preprint) and subscribes to what it needs, without knowing who produced it. Each
message says what its timestamp means. That makes the system easy to extend (new models, APIs, AI
agents and data streams join without rewiring) and lets SMMM align time correctly between
components that run at different rates. SMMM's management half is the timekeeper (one simulation
clock, per-step barriers, deadlines); its messaging half is the data hub (time-meaning-carrying
messages, quality flags, record/replay, provenance).

The FMU is not the backbone. An FMU adapter exists for interoperability ("FMU-optional").

## 2. Why this matters in the AI era

Physics models are deterministic, fast, always produce a valid answer and need only current inputs.
AI components (LLMs, ML models, web APIs, RL agents) break all four assumptions:

| AI property | Co-simulation problem | SMMM 2.0 answer |
|---|---|---|
| same input, different output | results cannot be reproduced | record/replay of messages; counterfactual replay |
| can fail, time out or return nonsense | validity and safety | quality flags, bounds and fallback on any port |
| slow (seconds) and variable latency | when does a decision take effect? | deadline-aware asynchronous delivery at a declared simulation time |
| needs history/context | memory | history and window queries served by the hub |

## 3. Contributions (what the paper will claim)

**C1 — Time-meaning-carrying messages and automatic alignment.** Each output declares the time
meaning of its values: `instant` (state at t), `average` (mean over an interval), `amount` (total
over an interval, e.g. energy), `hold` (valid from t until replaced, e.g. a set-point or price) or
`event`. Each message carries its validity interval. Each input declares what it needs relative to
its own step: `latest` (legacy), `at` (value in effect at t), `past` (aggregate over the step that
just ended) or `ahead` (over the step that starts). SMMM reconstructs each signal from its messages
and answers the query: time-weighted averages, proportional splitting of amounts, sample-and-hold.
Consequences:
- components with different steps (1 min to 1 h) exchange data without conversion code;
- energy and cost are conserved across rates (removes the ~2 % cost bias measured in v2 Case A);
- a `past` read of a value that is valid from t onward creates no same-step dependency, so time
  semantics also remove artificial algebraic loops.

**C2 — Topic and pattern subscriptions.** Inputs can subscribe to topics and patterns
(`"*.P_elec"`), so adding a building or replacing a tariff model with a live price API requires no
rewiring. `connect()` stays as the explicit form.

**C3 — AI-ready message handling.**
- *Quality:* outputs can declare bounds and a fallback; every message carries a quality flag
  (`ok`, `clipped`, `fallback`, `replayed`, `late`); results include a quality summary.
- *Record/replay:* the messages of any component can be journaled and replayed, so a run with
  non-deterministic components can be repeated exactly, and the same AI decisions can be replayed on
  a modified building (counterfactual analysis).
- *Provenance:* optional trace of which messages each component read at each step, so any decision
  can be traced back through the chain (weather → AI → set-point → EnergyPlus → grid).

**C4 — Deadline-aware asynchronous components.** A slow component (LLM, web API) runs in the
background in wall-clock time while the master keeps stepping. Its answer is delivered at a declared
*simulation* time (decision delay), so results do not depend on computer speed; N slow calls
overlap; a wall-clock deadline triggers a fallback.

**C5 — EnergyPlus internal step finer than the exchange step.** EnergyPlus runs at e.g. 1 min while
messages are exchanged every 15 min; outputs are aggregated according to their time meaning and
actuators are held.

## 4. Evidence (experiments)

| ID | Experiment | Claim | Metric |
|---|---|---|---|
| E1 | Verification: Py-CoSim vs standalone EnergyPlus (V1–V3, re-run) | the wrapper is exact | max abs diff = 0 |
| E2a | Synthetic multi-rate signals (1 min … 1 h) | C1 conserves energy/cost | error vs exact integral, naive vs aligned |
| E2b | EnergyPlus + PV + battery + tariff + meter at 5/15/30/60 min vs 1-min reference | C1 removes coupling bias | energy, cost, peak error |
| E2c | EnergyPlus internal 1 min, exchange 15 min | C5 accuracy/runtime trade-off | error vs runtime |
| E3 | Stochastic AI stand-in on EnergyPlus: live runs vs replay vs counterfactual replay | C3 reproducibility | run-to-run spread; replay diff = 0 |
| E4 | N buildings × slow AI (simulated latency): blocking vs asynchronous | C4 latency hiding and determinism | wall time vs N; identical results |
| E5 | Fault-injected AI/ML/API components | C3 quality and fallback | quality counts; bounded outputs |
| E6 | Overhead: latest vs window reads, trace on/off; scalability | cost of SMMM 2.0 | µs per exchange; speed-up |
| E7 | Same scenario in HELICS; FMU adapter demo | positioning vs prior art | LOC, runtime, accuracy, features |

The AI components are clearly labelled stand-ins (controlled latency, randomness and faults). A
real-LLM run is a follow-up that needs the author's API key; the infrastructure claims do not depend
on it.

## 5. Literature to cover (check in progress: `literature_check.md`)

HLA (IEEE 1516) and Fujimoto (2000); HELICS; mosaik 3.0; FMI 3.0 and SSP; Broman et al. (2013);
Gomes et al. (2018); Sadjina et al. (2017); NEPCE (Benedikt et al.); BCVTB, EnergyPlus FMU,
Spawn of EnergyPlus, Sinergym, COBS, Energym, BOPTEST, CityLearn; recent LLM-for-buildings work.

Positioning: message-based co-simulation with a time manager is established (HLA, HELICS). The new
parts are (i) messages that carry their time meaning so the hub aligns automatically, (ii) AI-ready
message handling (quality, replay, provenance, deadline-aware delivery), and (iii) all of this for
EnergyPlus-based building co-simulation, verified to be exact against standalone EnergyPlus.

## 6. Manuscript

Format decided by the `manuscript-template` skill (Times New Roman 12 pt, double spacing, numbered
headings, Fig./Table captions, numbered EndNote citations), submission files split with
`--anonymize --flatten-fields` for *Building Simulation* (double-blind, no field codes).

Working outline:
1. Introduction — connecting software vs. exchanging messages; the AI-era problems.
2. Related work — co-simulation standards and master algorithms; message-based co-simulation;
   building co-simulation tools; AI in building simulation.
3. SMMM 2.0 — message model and time meanings; alignment rules; management (clock, barrier,
   dependency analysis); AI-ready handling; asynchronous components; EnergyPlus adapter.
4. Implementation — Py-CoSim package (short).
5. Verification and evaluation — E1–E7.
6. Case study — grid-interactive community with EnergyPlus buildings, PV, battery, tariff and AI
   supervisors (combines the earlier Case A and B).
7. Discussion and limitations; 8. Conclusions.

## 7. Open items for the author

- Real-LLM run (API key and budget) before submission.
- Author names, affiliations and funding on the title page.
- EnergyPlus version used in the paper: 22.2 (installed); newer versions not tested.
