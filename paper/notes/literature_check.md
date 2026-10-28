# Literature check: SMMM 2.0 novelty claims

Checked on 28 Oct 2026 for the *Building Simulation* manuscript on Py-CoSim v2 (claims C1–C5 in
`SMMM2_PLAN.md`, plus "FMU optional").

**How things were checked.** Bibliographic data came from the Crossref API (DOI resolved; title,
authors, venue, volume and pages compared) or from the publisher's or standards body's own page. What
each tool or standard does was read from its official documentation pages (HELICS, mosaik, FMI 3.0,
SSP 2.0, OPC UA, CF, EnergyPlus, BOPTEST, VOLTTRON, Lingua Franca), not from secondary summaries.
Where a point rests on a secondary source, it says so. Keys already in `paper/references.py` are
reused and were not re-checked: `palmintier2017`, `hardy2024`, `steinbrink2019`, `blochwitz2011`,
`gomes2018`, `kubler2000`, `wetter2011`, `nouidui2014`, `wetter2020`, `blum2021`, `jimenez2021`,
`campoy2025`, `zhang2020cobs`, `scharnhorst2021`, `vazquez2019`, `nweye2025`, `benne2024`,
`ahn2023`, `zhu2025`, `zhangchen2024`, `jiang2024`, `liu2025llm`, `doe2022`.

---

## 1. Novelty table

Verdicts: **(a)** new, **(b)** new only for building/EnergyPlus co-simulation, **(c)** already done.

| # | Claim | Closest prior art | Verdict | How to phrase it safely |
|---|---|---|---|---|
| C1 | Every value carries its time meaning (`instant`, `average`, `amount`, `hold`, `event`) and the hub aligns it automatically to each subscriber's own step window (time-weighted mean, proportional split, sample-and-hold) | *The vocabulary already exists outside co-simulation:* CF conventions `cell_methods` point/mean/sum with time bounds (`cf2023`); WaterML 2.0 interpolation types such as average, total or constant over the preceding or succeeding interval (`ogc_waterml2014`); EnergyPlus's own Average vs Sum output variables (`doe2024rdd`). *Partial forms in co-simulation:* hold and event semantics in mosaik 3 (measurements valid over [t, t_next) vs events, `ofenloch2022`, `mosaik_docs`), HELICS (values persist, messages are one-time, `hardy2024`, `helics_docs`), FMI 3.0 (discrete inputs held, clocks and Event Mode, `junghanns2021`, `fmi3_2022`). Automatic rate conversion with hold or unit delay only: Simulink Rate Transition (`mathworks_rt`). Signal conversion written by the user as hand-coded "semantic adaptation" (`gomes2019semantic`, `tripakis2015`). | **(a), with qualification.** I found no co-simulation framework (HLA, HELICS, mosaik, FMI 3.0/SSP 2.0, DCP, BCVTB, EnergyPlus FMU import/export, Spawn, Sinergym, BOPTEST) whose exchanged values say whether they are interval averages or interval totals, and none that aligns them to the receiver's step window. HELICS's `average` reduces across several sources at one instant, not over time. mosaik's receiver simply reads the value valid at the start of its step. The individual meanings themselves are prior art. | "To our knowledge, none of the general-purpose (HLA, HELICS, mosaik, FMI/SSP) or building co-simulation tools we reviewed attaches interval semantics (average, total) to exchanged values or uses them to align data automatically between components with different step sizes. We borrow the vocabulary from data conventions (CF `cell_methods`, WaterML 2.0 interpolation types) and from EnergyPlus's own average/sum reporting, and make it a service of the co-simulation hub." Present the ~2 % cost bias as an *alignment* (off-by-one-interval) error of the kind known from explicit coupling (`kubler2000`, `gomes2018`), not as a newly discovered phenomenon. Do not say "energy-conserving coupling" without qualification; that phrase belongs to NEPCE and power bonds (see §2.D). |
| C1b | A `past` read of a value that is valid from t onward creates no same-step dependency, so artificial algebraic loops disappear | HLA lookahead (`ieee1516_1_2010`, `fujimoto2000`); mosaik time-shifted connections (`ofenloch2022`, `mosaik_docs`); `after` delays in Lingua Franca (`lohstroh2021`); determinate master algorithms that use feedthrough dependency information (`broman2013`) | **(c)** as a mechanism | "As with lookahead in HLA and time-shifted connections in mosaik, …". Present it as a convenient consequence of C1, not as a separate contribution. |
| C2 | Topic and pattern subscriptions (`*.P_elec`), so adding a component needs no rewiring | Class-based publish/subscribe in HLA declaration management (`ieee1516_1_2010`); MQTT topic wildcards `+`/`#` (`oasis_mqtt2019`); DDS topics and content-filtered topics (`omg_dds2015`); VOLTTRON prefix subscriptions on the `devices/...` topic tree, already used for buildings (`katipamula2016`, `volttron_docs`); HELICS inputs with several publications plus `multi_input_handling_method` (sum, max, average, …) and the Connector app that matches interfaces by regex (`helics_docs`); co-simulation over DDS and MQTT (`wen2024`, `paduani2023`) | **(c)** | "Py-CoSim adopts topic-based publish/subscribe with wildcard patterns, as used in MQTT, DDS, VOLTTRON and HELICS, so that…". Do not claim it as a contribution. At most say it is new for EnergyPlus-API-based Python frameworks. |
| C3a | Quality flags (ok/clipped/fallback/replayed/late), bounds and fallback on any port | OPC UA status codes, e.g. Uncertain_LastUsableValue, Uncertain_SubstituteValue, Uncertain_EngineeringUnitsExceeded (`opcua_part8`); IEC 61850 quality (validity good/invalid/questionable; detail flags; source = substituted; test) (`iec61850_7_3`); HELICS `default` value before any publication (`helics_docs`) | **(c)** in industrial communication; **(b)** in co-simulation, since I found per-value quality in no co-simulation framework | "Following status-code practice in OPC UA and IEC 61850, every message carries a quality flag…" |
| C3b | Record/replay of every non-deterministic component, for bit-identical reruns and counterfactual replays | General deterministic replay (`chen2015replay`); HELICS Recorder writes files that the HELICS Player can feed back into a federation, standing in for a federate (`helics_docs`); record and replay for LLM agents (`feng2025agentrr`); motivation: LLM outputs differ between runs even at temperature 0 (`ouyang2025`), and RL results are hard to reproduce (`henderson2018`) | **(b)** | "Record/replay is an established technique (Chen et al. 2015) and HELICS ships a recorder/player pair. We make it automatic and per-component in the hub, aimed at AI components, so that a run can be repeated exactly and AI decisions can be replayed against a modified building." Claim bit-identical results only for replay mode, not for live LLM calls. |
| C3c | Provenance trace (who published, who read) | W3C PROV-DM (`moreau2013prov`); provenance in simulation studies (`ruscheinski2017`, `ruscheinski2025`); HELICS Tracer app and queries (`helics_docs`) | **(c)** generally; **(b)** in building co-simulation | "a lightweight PROV-style trace…" |
| C4 | Deadline-aware asynchronous components: a slow AI call runs in wall-clock time, its answer is delivered at a declared *simulation* time, a fallback fires on a wall-clock deadline, and N calls overlap | Logical execution time: Giotto (`henzinger2003`), PTIDES (`zhao2007`), Lingua Franca deadlines with deadline handlers (`lohstroh2021`). HLA lookahead with time-stamp-order delivery (`ieee1516_1_2010`, `fujimoto2000`). Delayed-action MDPs and RL (`katsikopoulos2003`, `ramstedt2019`, `xiao2020`). Overlapping LLM calls in multi-agent simulation (`xie2025`). HELICS async time requests and a real-time mode whose forced grants the docs say can make results non-deterministic (`helics_docs`). Optimistic and conservative PDES (`jefferson1985`, `chandy1979`, `fujimoto1990`). | **(b)**. The principle (wall-clock latency decoupled from logical/simulated time, with a deadline handler) is well established in real-time systems. I found no building co-simulation tool that does this for AI calls. Alfalfa can run in real time; BOPTEST and Sinergym step synchronously; the LLM-HVAC studies call the model in the loop, and a 2026 review lists bounded latency as an open need (`neubauer2026`). | "We apply the logical-execution-time principle (Henzinger et al. 2003; Lohstroh et al. 2021) to AI components in building co-simulation: an AI component's simulated decision delay is declared and does not depend on its wall-clock latency; deadline misses trigger a fallback, as Lingua Franca's deadline handlers do." Point out that the declared decision delay plays the role of HLA lookahead. |
| C5 | EnergyPlus internal step finer than the exchange step; sensors aggregated by time meaning; actuators held | EnergyPlus already averages or sums outputs to the reporting interval by variable type (`doe2024rdd`). EnergyPlus External Interface/BCVTB reads inputs at the start of each zone time step, holds them during it, and synchronizes at the zone time step (`doe_extif`). EnergyPlus FMU export requires time steps per hour to match the FMU sampling time (`lbnl_fmuexport`). Sinergym's legacy interface offered action repeat (from search results only, not cited). | **(b)**, and modest | "Unlike ExternalInterface/FMU-export coupling, which exchanges data at every zone time step, Py-CoSim allows the exchange step to be a multiple of the zone time step and aggregates sensor values by their declared time meaning, the same average/sum rule EnergyPlus applies to its own reports." Present it as an engineering feature evaluated in E2c, not a scientific novelty. |
| C6 | FMU is an optional adapter, not the backbone | mosaik and HELICS treat FMUs as one simulator type among others (`steinbrink2019`, `hardy2024`); Sinergym and COBS drive EnergyPlus directly (`campoy2025`, `zhang2020cobs`); Py-CoSim v1 already used the EnergyPlus API (`zhang2023`) | **(c)** | Present it as a design choice ("FMI-compatible through an adapter, but not dependent on FMI"), not a contribution. |

---

## 2. Prior art, item by item

### A. Distributed-simulation foundations

- **HLA, IEEE 1516-2010 / 1516.1-2010 (`ieee1516_2010`, `ieee1516_1_2010`); Fujimoto 2000 (`fujimoto2000`).**
  HLA federates exchange attribute updates and interactions through an RTI. Publish/subscribe works
  by object class and attribute (declaration management), with optional region-based filtering
  (data distribution management), so senders do not name receivers. Time management delivers
  time-stamp-order messages; federates advance with Time Advance Request or Next Message Request and
  are released by a Time Advance Grant. A federate's *lookahead* is a promise not to send messages
  stamped earlier than its current time plus the lookahead. HLA timestamps are instants: an update
  has no "average over an interval" meaning, so HLA covers C2 and the timing side of C4, but not C1.
- **PDES synchronization (`chandy1979`, `jefferson1985`, `fujimoto1990`).** Chandy and Misra's
  conservative protocol only processes events that are known to be safe. Jefferson's Time Warp runs
  ahead optimistically and rolls back. Fujimoto's survey compares the two. These are the classical
  ways to hide latency while keeping results deterministic. Py-CoSim's async design is closer to the
  conservative side: a fixed declared delay acts as lookahead, so no rollback is needed.

### B. Energy-domain co-simulation frameworks

- **HELICS (`palmintier2017`, `hardy2024`, `helics_docs`).** HELICS distinguishes *values* from
  *messages*. Values go from publications to inputs, persist until replaced, and the newest one
  wins. Messages are one-time, addressed to endpoints, and can pass through filters (delay, random
  delay, random drop, clone). An input fed by several publications is reduced by
  `multi_input_handling_method` (none/priority, sum, max, min, average/mean, vectorize, and, or,
  diff). This reduction works *across sources at one time*; I found no option for interpolating,
  time-averaging or integrating values over an interval. `only_update_on_change` and
  `only_transmit_on_change` suppress repeated values, with `tolerance` defining what counts as a
  change. Units are converted for doubles when both sides declare them, and a `default` value covers
  the time before the first publication. Timing options include period, offset, time_delta,
  input/output delay and uninterruptible. A `real_time` mode ties grants to the wall clock, and the
  docs warn that its forced grants can make results non-deterministic. There is also a non-blocking
  `requestTimeAsync`. Several apps matter here: Recorder writes files that Player can read, so a
  recording can stand in for a federate; Connector matches interfaces by regex; Tracer prints
  traffic. **HELICS is the strongest prior art for C2 and part of C3b**, and the strongest contrast
  for C1 and C4.
- **FNCS (`ciraci2014`).** FNCS is PNNL's predecessor to HELICS, built on ZeroMQ for
  power-grid/communication-network co-simulation. The MASCOTS 2014 paper proposes two conservative
  synchronization algorithms that cut synchronization overhead. It is useful only as background for
  energy-domain co-simulation over a message broker.
- **mosaik 3.0 (`ofenloch2022`, `mosaik_docs`, `steinbrink2019`).** mosaik 3 combines time-stepped,
  event-based and hybrid simulators. *Measurements* (persistent attributes) stay valid until
  overwritten. *Events* (non-persistent attributes) exist only at a point in time, and a *trigger*
  input causes the receiving simulator to step. The scheduler docs say a time-based output holds
  over the right-open interval from now to the next step. A receiver gets the value valid at the
  start of its step: a slow receiver may miss intermediate updates and a fast one reuses the same
  value. Averaging, if needed, must be written into the simulators. Weak connections allow same-time
  iteration (up to `max_loop_iterations`), and time-shifted connections feed the receiver's next step
  using `initial_data`. mosaik therefore has `hold` and `event`, but not `average` or `amount`.
- **DDS-, MQTT- and ZeroMQ-based co-simulation (`omg_dds2015`, `oasis_mqtt2019`, `wen2024`,
  `paduani2023`).** DDS (OMG 1.4) offers data-centric publish/subscribe with topics and
  content-filtered topics. Wen et al. (2024) build a distributed power-system co-simulation platform
  on DDS. MQTT 5.0 subscriptions use topic filters with wildcards. Paduani et al. (2023) use MQTT for
  data exchange and time synchronization in real-time transmission-and-distribution/DER
  co-simulation. HELICS itself can run over ZeroMQ, TCP and other cores. Together they show that
  broker-based, topic-addressed co-simulation is established.
- **VOLTTRON (`katipamula2016`, `volttron_docs`).** VOLTTRON is an open-source agent platform for
  buildings and grid integration, built on a publish/subscribe message bus. Subscribing to a topic
  also delivers all its subtopics, and drivers publish under a `devices/...` tree. This is
  building-domain prior art for C2.

### C. FMI, SSP and DCP

- **FMI 3.0 (`junghanns2021`, `gomes2021clocks`, `fmi3_2022`; FMI 1.0 is `blochwitz2011`).** For
  co-simulation, FMI 3.0 adds Event Mode, early return from `fmi3DoStep`, Intermediate Update Mode
  (the FMU calls back during a step so the importer can inspect outputs and set inputs, for example
  to interpolate) and clocks (time-based periodic/aperiodic, or triggered). In the specification,
  discrete inputs keep their values between communication points, while continuous inputs are
  treated as continuous, possibly extrapolated by the importer from output derivatives. Outputs are
  values at the FMU's current time. I found no notion of an output being an average or total over
  the communication step.
- **SSP 2.0 (`ssp2_2024`).** SSP describes a system of connected components. Connectors have a
  kind, type and unit; connections may apply unit conversion, a linear transformation
  (factor × source + offset) or enumeration/boolean/integer maps. The parts of the 2.0 text I read
  do not define any time meaning or rate conversion for signals.
- **DCP (`krammer2019`).** DCP is a Modelica Association protocol for coupling real-time systems
  (hardware) with non-real-time simulators over UDP, TCP, CAN and similar transports. It is relevant
  background for wall-clock vs simulation time (C4). It does not attach interval semantics to
  signals.

### D. Coupling error, energy conservation and determinacy

- **NEPCE (`benedikt2013nepce`) and non-iterative coupling analysis (`benedikt2013`).** Benedikt et
  al. treat coupling as an exchange of generalized energy. In explicit (non-iterative)
  co-simulation, extrapolation errors leave an energy residual, and NEPCE feeds that residual back in
  the following steps. The MCMDS paper analyses the error of the non-iterative coupling process.
  These works correct the *physics* coupling error after it happens. Py-CoSim's C1 instead avoids an
  *accounting/alignment* error (which interval a power value belongs to). These are related but
  distinct, so cite them as the closest "energy-preserving" work and explain the difference.
- **Power bonds (`sadjina2017`).** Sadjina et al. couple subsystems through power bonds (pairs of
  effort and flow variables), monitor the energy residual to estimate error and control the step
  size adaptively. Like NEPCE, this works on physical power flow, not on the meaning of a reported
  value.
- **Determinate master algorithms (`broman2013`); survey (`gomes2018`); tagged-signal model
  (`lee1998`).** Broman et al. state the conditions (dependency information, rollback or step-size
  prediction) under which a master algorithm composing FMUs gives determinate results. Gomes et al.
  survey co-simulation, including signal reconstruction between communication points (zero-order
  hold, extrapolation), which is where `hold`/`instant` fit. In Lee and Sangiovanni-Vincentelli's
  tagged-signal model, a signal is a set of (tag, value) events. Py-CoSim's `average`/`amount`
  messages can be described as events tagged with an *interval* rather than an instant, which is a
  useful way to explain C1 formally.
- **Semantic adaptation (`gomes2019semantic`, `tripakis2015`).** Tripakis lists the semantic gaps
  (untimed vs timed, signals vs events, and so on) between modeling formalisms and the FMI API.
  Gomes et al. provide a DSL for writing adapters that correct such mismatches without changing the
  simulators. **This is the closest co-simulation concept to C1**, but the adapters are written by
  hand for each connection. Py-CoSim instead declares the meaning on each output and derives the
  adaptation automatically.
- **Simulink Rate Transition (`mathworks_rt`).** Simulink inserts rate-transition blocks
  automatically. In deterministic mode, a fast-to-slow transfer behaves as a zero-order hold and a
  slow-to-fast transfer as a unit delay. This is automatic multi-rate conversion, but only with
  sample/hold semantics and no interval averaging.

### E. Time semantics, quality and provenance in data standards

- **CF conventions (`cf2023`).** `cell_methods` says how a value represents its cell along an axis:
  `time: point` (instantaneous), `time: mean`, `time: sum`. Bounds are required for any method other
  than point. This is the closest established vocabulary for `instant`/`average`/`amount`.
- **WaterML 2.0 (`ogc_waterml2014`).** Every time series (or point) has an interpolation type, for
  example continuous, average/total/min/max/constant in the preceding interval, and the same for the
  succeeding interval. This maps almost one-to-one to `average`, `amount` and `hold`, so cite it as
  the source of the idea.
- **EnergyPlus Average vs Sum (`doe2024rdd`).** EnergyPlus classifies every output variable as
  "Average" (a state, such as a temperature or rate) or "Sum" (an amount, such as energy). When
  reporting at a coarser frequency it averages the former and sums the latter. Stated in the
  EnergyPlus 24.1 documentation: "'sum' variables are summed over that time interval." This is
  direct support for C1 and C5 from the building-simulation world.
- **OPC UA status codes (`opcua_part8`) and IEC 61850 quality (`iec61850_7_3`).** Every OPC UA value
  carries a Good/Uncertain/Bad status with specific sub-codes: last usable value, substitute
  (manually overwritten) value, engineering units exceeded, sensor not accurate, simulated value.
  IEC 61850 values carry validity (good/invalid/questionable), detail flags such as overflow,
  out-of-range and old data, a source (process vs substituted) and a test flag. My IEC 61850 summary
  comes from vendor and CIM documentation, not the paywalled standard. Py-CoSim's
  ok/clipped/fallback/replayed/late flags are a simplified version of this practice.
- **Provenance (`moreau2013prov`, `ruscheinski2017`, `ruscheinski2025`).** W3C PROV-DM models
  provenance as entities, activities and agents. Ruscheinski and Uhrmacher argue for provenance in
  M&S studies, and SIMPROV (2025) captures it automatically for simulation workflows. The SMMM trace
  of who published and who read can be framed as a lightweight PROV graph at message level.

### F. Record/replay and reproducibility of AI components

- **Deterministic replay (`chen2015replay`).** This survey covers record/replay for debugging and
  reproducing non-deterministic executions, the classical basis for C3b.
- **HELICS Recorder/Player (`helics_docs`).** The Recorder captures values and messages from chosen
  interfaces into files the Player can read, so a recording can replace a live federate. This is
  energy co-simulation prior art for replaying a component.
- **AgentRR (`feng2025agentrr`).** AgentRR records an LLM agent's interactions and decisions and
  replays the condensed experience to guide later tasks. Its aim is reuse, not bit-identical
  reruns, but it shows record/replay is now applied to LLM agents.
- **Non-determinism (`ouyang2025`, `henderson2018`).** Ouyang et al. find that ChatGPT gives
  different outputs for identical prompts and that temperature 0 reduces but does not remove the
  variation. Henderson et al. show deep-RL results vary strongly with seeds and implementations.
  Together they motivate C3b.

### G. Asynchronous and latency-hiding execution with deterministic timing

- **Giotto / logical execution time (`henzinger2003`).** Giotto fixes when a task reads inputs and
  when its outputs are released, independent of how long it actually runs. Timing is therefore
  platform-independent and deterministic, which is exactly C4's principle.
- **PTIDES (`zhao2007`).** PTIDES gives discrete-event models a defined relation between model time
  and physical time in distributed real-time systems. It allows out-of-order execution without
  losing determinism, assuming bounded clock-synchronization error.
- **Lingua Franca (`lohstroh2021`).** Lingua Franca is a deterministic reactor language with logical
  time. Connections can carry logical `after` delays. A deadline compares physical with logical
  time, and when it is missed a deadline handler runs instead of the normal reaction. This is the
  closest match to C4's wall-clock deadline plus fallback.
- **Delayed actions in decision making (`katsikopoulos2003`, `ramstedt2019`, `xiao2020`).**
  Katsikopoulos and Engelbrecht define MDPs with action and observation delays. Ramstedt and Pal
  formalize "real-time RL", where the action takes effect one step later because computing it takes
  time. Xiao et al. let the agent choose the next action while the previous one is still running.
  All three treat decision latency as part of the model, which is what a declared decision delay
  does.
- **AI Metropolis (`xie2025`).** AI Metropolis speeds up LLM-agent simulations by tracking real
  dependencies between agents and executing out of order, so slow LLM calls overlap (1.3–4.15× over
  global synchronization). It is evidence that overlapping LLM calls inside a simulation is a known
  need. The abstract does not discuss determinism, and the method does not target building
  co-simulation.

### H. Building co-simulation tools

- **BCVTB and EnergyPlus External Interface (`wetter2011`, `doe_extif`).** Data are exchanged at a
  fixed synchronization step with no iteration. EnergyPlus reads external values at the start of
  each zone time step and holds them during it, so the zone time step is the exchange step. Values
  carry no time meaning.
- **EnergyPlus FMU import/export (`nouidui2014`, `lbnl_fmuexport`).** The export guide requires the
  EnergyPlus time steps per hour to equal the FMU's sampling time, since the External Interface
  synchronizes at the (constant) zone time step. This is relevant to C5.
- **Spawn of EnergyPlus (`wetter2020`).** Modelica controls and HVAC are coupled to EnergyPlus
  envelope models. Its `OutputVariable` block reads an EnergyPlus output at every zone time step. The
  documentation does not say whether the value is averaged, summed or sampled.
- **Sinergym (`jimenez2021`, `campoy2025`), COBS (`zhang2020cobs`), Energym (`scharnhorst2021`),
  CityLearn (`vazquez2019`, `nweye2025`).** These are Gym-style environments with a synchronous
  `step()`. Sinergym's step interrupts EnergyPlus, sets the controls and resumes. In their papers and
  docs I found no time-meaning metadata, no record/replay and no asynchronous AI calls. I did not
  re-read their current source code.
- **BOPTEST (`blum2021`).** BOPTEST is a REST API with `advance` (sets inputs, returns
  measurements), an adjustable control `step`, a `forecast` (horizon, interval) and `results`. The
  API README says nothing about whether measurements are instantaneous or step-averaged and describes
  no real-time or asynchronous mode. KPIs are computed by the testbed.
- **Alfalfa (`benne2024`).** Alfalfa runs EnergyPlus as a web service that a controller reads and
  writes while the simulation runs, in real time or faster. When it is paced by the wall clock, what
  a slow controller sees depends on timing. That is the behaviour C4 avoids.

### I. LLM/AI in building simulation and control (2023–2026)

- **Control:** ChatGPT for HVAC control (`ahn2023`, existing). GPT-4 as a training-free HVAC
  controller using in-context demonstrations (`song2023`). LLM-based interpretable ML control
  (`zhangchen2024`, existing). LLM HVAC temperature/humidity optimization (`zhu2025`, existing).
  Multimodal foundation models in the HVAC loop of a real office, "office-in-the-loop"
  (`sawada2024`, `sawada2025`). Retrieval-augmented interpretable HVAC control, DARLIN (`ko2025`).
  RAG-LLM chiller optimization, Co-LLM (`liu2026colllm`; the 2026 review reports about 2 s per
  control step).
- **LLM occupants:** an LLM-driven agent-based framework for evaluating occupant-centric HVAC control
  (`sasaki2025`).
- **Model generation and editing:** EPlus-LLM (`jiang2024`, existing); GPT-4 multi-agent revision of
  EnergyPlus models (`zhang2024bem`); a four-agent workflow that generates and debugs EnergyPlus
  models (`zhang2025agentic`); EnergyPlus-MCP server (`li2025mcp`); LLM agents translating HVAC
  instructions into OpenStudio Measures (`dieng2026`).
- **Review (`neubauer2026`, arXiv).** The review notes that multi-step LLM pipelines add seconds per
  decision, which suits only slow supervisory loops. It calls for designs with bounded latency and
  physics guardrails, and it counts a study as reproducible only if it shares artefacts or reports
  repeated runs. It is a good citation for the motivation of C3 and C4. It is a preprint: replace it
  with the journal version if one appears.
- **Gap:** none of these papers offers infrastructure for deterministic timing of slow LLM calls,
  for replaying LLM decisions, or for quality-flagging LLM outputs inside a co-simulation. They call
  the model synchronously or offline.

---

## 3. New verified references (paste into `references.py`)

All entries below were checked as stated in `verified`. Fields that could not be checked are `None`.
Docs pages have no fixed year, so cite them with an access date (28 Oct 2026).

```python
NEW_REFS = {
    # ---------------- Distributed simulation foundations ----------------
    "ieee1516_2010": dict(type="standard", authors=["IEEE"], year=2010,
        title="IEEE Standard for Modeling and Simulation (M&S) High Level Architecture (HLA)—Framework and Rules",
        journal_or_booktitle="IEEE Std 1516-2010", volume=None, pages=None, publisher="IEEE",
        doi="10.1109/IEEESTD.2010.5553440", url=None,
        verified="Crossref DOI resolved (type=standard; ISBN 9780738162515); year from standard number"),
    "ieee1516_1_2010": dict(type="standard", authors=["IEEE"], year=2010,
        title="IEEE Standard for Modeling and Simulation (M&S) High Level Architecture (HLA)—Federate Interface Specification",
        journal_or_booktitle="IEEE Std 1516.1-2010", volume=None, pages=None, publisher="IEEE",
        doi="10.1109/IEEESTD.2010.5557728", url=None,
        verified="Crossref DOI resolved (type=standard; ISBN 9780738162478); year from standard number"),
    "fujimoto2000": dict(type="book", authors=["Fujimoto, R.M."], year=2000,
        title="Parallel and Distributed Simulation Systems", journal_or_booktitle=None, volume=None,
        pages=None, publisher="Wiley-Interscience", doi=None,
        url="https://www.wiley-vch.de/en/areas-interest/computing-computer-sciences/parallel-and-distributed-simulation-systems-978-0-471-18383-9",
        verified="Wiley product pages: ISBN 978-0-471-18383-9, Jan 2000, Wiley Series on Parallel and Distributed Computing"),
    "fujimoto1990": dict(type="article", authors=["Fujimoto, R.M."], year=1990,
        title="Parallel discrete event simulation", journal_or_booktitle="Communications of the ACM",
        volume="33", pages="30–53", publisher="ACM", doi="10.1145/84537.84545", url=None,
        verified="Crossref DOI resolved; issue 10"),
    "chandy1979": dict(type="article", authors=["Chandy, K.M.", "Misra, J."], year=1979,
        title="Distributed simulation: A case study in design and verification of distributed programs",
        journal_or_booktitle="IEEE Transactions on Software Engineering", volume="SE-5", pages="440–452",
        publisher="IEEE", doi="10.1109/TSE.1979.230182", url=None, verified="Crossref DOI resolved; issue 5"),
    "jefferson1985": dict(type="article", authors=["Jefferson, D.R."], year=1985, title="Virtual time",
        journal_or_booktitle="ACM Transactions on Programming Languages and Systems", volume="7",
        pages="404–425", publisher="ACM", doi="10.1145/3916.3988", url=None,
        verified="Crossref DOI resolved; issue 3"),
    "ciraci2014": dict(type="inproceedings",
        authors=["Ciraci, S.", "Daily, J.", "Agarwal, K.", "Fuller, J.", "Marinovici, L.", "Fisher, A."], year=2014,
        title="Synchronization algorithms for co-simulation of power grid and communication networks",
        journal_or_booktitle="2014 IEEE 22nd International Symposium on Modelling, Analysis & Simulation of Computer and Telecommunication Systems (MASCOTS)",
        volume=None, pages="355–364", publisher="IEEE", doi="10.1109/MASCOTS.2014.51", url=None,
        verified="Crossref DOI resolved; abstract on PNNL publication page"),

    # ---------------- Energy-domain co-simulation, FMI/SSP/DCP ----------------
    "ofenloch2022": dict(type="inproceedings",
        authors=["Ofenloch, A.", "Schwarz, J.S.", "Tolk, D.", "Brandt, T.", "Eilers, R.", "Ramirez, R.", "Raub, T.", "Lehnhoff, S."],
        year=2022, title="MOSAIK 3.0: Combining time-stepped and discrete event simulation",
        journal_or_booktitle="2022 Open Source Modelling and Simulation of Energy Systems (OSMSES)", volume=None,
        pages="1–5", publisher="IEEE", doi="10.1109/OSMSES54027.2022.9769116", url=None,
        verified="Crossref DOI resolved"),
    "mosaik_docs": dict(type="misc", authors=["mosaik developers (OFFIS)"], year=None,
        title="mosaik 3.5.0 documentation: 'Measurements and Events' and 'Scheduling and simulation execution'",
        journal_or_booktitle=None, volume=None, pages=None, publisher="OFFIS", doi=None,
        url="https://mosaik.readthedocs.io/en/3.5.0/scheduler.html",
        verified="Pages read 28 Oct 2026 (also .../3.5.0/explanations/measurements-and-events.html); year not stated"),
    "helics_docs": dict(type="misc", authors=["HELICS developers (GMLC-TDC)"], year=None,
        title="HELICS documentation: Configuration options reference; Value and message federates; Multi-source inputs; Apps (Recorder, Player, Connector); Asynchronous time request",
        journal_or_booktitle=None, volume=None, pages=None, publisher=None, doi=None,
        url="https://docs.helics.org/en/latest/references/configuration_options_reference.html",
        verified="Pages read 28 Oct 2026 (HELICS 3.x docs); year not stated"),
    "junghanns2021": dict(type="inproceedings",
        authors=["Junghanns, A.", "Gomes, C.", "Schulze, C.", "Schuch, K.", "Mai, P.R.", "Blaesken, M.", "Zacharias, I.",
                 "Pillekeit, A.", "Wernersson, K.", "Sommer, T.", "Bertsch, C.", "Blochwitz, T.", "Najafi, M."],
        year=2021, title="The Functional Mock-up Interface 3.0 – New features enabling new applications",
        journal_or_booktitle="Proceedings of the 14th International Modelica Conference 2021, Linköping, Sweden (Linköping Electronic Conference Proceedings 181)",
        volume="181", pages="17–26", publisher="Linköping University Electronic Press", doi="10.3384/ecp2118117",
        url="https://ecp.ep.liu.se/index.php/modelica/article/view/178",
        verified="Crossref DOI resolved (no pages in Crossref); pages 17–26 from the Modelica 2021 proceedings listing via web search; 'Mai' from conference author page"),
    "gomes2021clocks": dict(type="inproceedings",
        authors=["Gomes, C.", "Blochwitz, T.", "Bertsch, C.", "Wernersson, K.", "Schuch, K.", "Mai, P.R.", "Kotte, O.",
                 "Zacharias, I.", "Blesken, M.", "Sommer, T.", "Najafi, M.", "Junghanns, A."],
        year=2021, title="The FMI 3.0 standard interface for clocked and scheduled simulations",
        journal_or_booktitle="Proceedings of the 14th International Modelica Conference 2021, Linköping, Sweden (Linköping Electronic Conference Proceedings 181)",
        volume="181", pages=None, publisher="Linköping University Electronic Press", doi="10.3384/ecp2118127",
        url="https://ecp.ep.liu.se/index.php/modelica/article/view/179",
        verified="Crossref DOI resolved and LiU article page; pages not listed"),
    "fmi3_2022": dict(type="standard", authors=["Modelica Association"], year=2022,
        title="Functional Mock-up Interface Specification, Version 3.0", journal_or_booktitle=None, volume=None,
        pages=None, publisher="Modelica Association", doi=None, url="https://fmi-standard.org/docs/3.0/",
        verified="Specification page: version 3.0 released 2022-05-10"),
    "ssp2_2024": dict(type="standard", authors=["Modelica Association"], year=2024,
        title="System Structure and Parameterization (SSP), Version 2.0", journal_or_booktitle=None, volume=None,
        pages=None, publisher="Modelica Association", doi=None, url="https://ssp-standard.org/docs/2.0",
        verified="Specification page: release 2.0 dated 2024-12-20 (2.0.1 released 2026-05-05)"),
    "krammer2019": dict(type="inproceedings",
        authors=["Krammer, M.", "Schuch, K.", "Kater, C.", "Alekeish, K.", "Blochwitz, T.", "Materne, S.", "Soppa, A.", "Benedikt, M."],
        year=2019, title="Standardized integration of real-time and non-real-time systems: The Distributed Co-Simulation Protocol",
        journal_or_booktitle="Proceedings of the 13th International Modelica Conference, Regensburg, Germany (Linköping Electronic Conference Proceedings 157)",
        volume="157", pages="87–96", publisher="Linköping University Electronic Press", doi="10.3384/ecp1915787",
        url=None, verified="Crossref DOI resolved"),

    # ---------------- Coupling semantics, determinacy, energy conservation ----------------
    "gomes2019semantic": dict(type="article",
        authors=["Gomes, C.", "Meyers, B.", "Denil, J.", "Thule, C.", "Lausdahl, K.", "Vangheluwe, H.", "De Meulenaere, P."],
        year=2019, title="Semantic adaptation for FMI co-simulation with hierarchical simulators",
        journal_or_booktitle="Simulation", volume="95", pages="241–269", publisher="SAGE",
        doi="10.1177/0037549718759775", url=None, verified="Crossref DOI resolved; issue 3; online 2018"),
    "tripakis2015": dict(type="inproceedings", authors=["Tripakis, S."], year=2015,
        title="Bridging the semantic gap between heterogeneous modeling formalisms and FMI",
        journal_or_booktitle="2015 International Conference on Embedded Computer Systems: Architectures, Modeling, and Simulation (SAMOS)",
        volume=None, pages="60–69", publisher="IEEE", doi="10.1109/SAMOS.2015.7363660", url=None,
        verified="Crossref DOI resolved"),
    "broman2013": dict(type="inproceedings",
        authors=["Broman, D.", "Brooks, C.", "Greenberg, L.", "Lee, E.A.", "Masin, M.", "Tripakis, S.", "Wetter, M."],
        year=2013, title="Determinate composition of FMUs for co-simulation",
        journal_or_booktitle="2013 Proceedings of the International Conference on Embedded Software (EMSOFT)",
        volume=None, pages="1–12", publisher="IEEE", doi="10.1109/EMSOFT.2013.6658580", url=None,
        verified="Crossref DOI resolved"),
    "lee1998": dict(type="article", authors=["Lee, E.A.", "Sangiovanni-Vincentelli, A."], year=1998,
        title="A framework for comparing models of computation",
        journal_or_booktitle="IEEE Transactions on Computer-Aided Design of Integrated Circuits and Systems",
        volume="17", pages="1217–1229", publisher="IEEE", doi="10.1109/43.736561", url=None,
        verified="Crossref DOI resolved; issue 12"),
    "benedikt2013nepce": dict(type="inproceedings",
        authors=["Benedikt, M.", "Watzenig, D.", "Zehetner, J.", "Hofer, A."], year=2013,
        title="NEPCE – A nearly energy-preserving coupling element for weak-coupled problems and co-simulations",
        journal_or_booktitle="COUPLED V: Proceedings of the V International Conference on Computational Methods for Coupled Problems in Science and Engineering, Ibiza, Spain, 17–19 June 2013",
        volume=None, pages="1021–1032", publisher="CIMNE", doi=None, url="https://hdl.handle.net/2117/192675",
        verified="UPC repository record (UPCommons): authors, pages, ISBN 978-84-941407-6-1; no DOI"),
    "benedikt2013": dict(type="article", authors=["Benedikt, M.", "Watzenig, D.", "Hofer, A."], year=2013,
        title="Modelling and analysis of the non-iterative coupling process for co-simulation",
        journal_or_booktitle="Mathematical and Computer Modelling of Dynamical Systems", volume="19",
        pages="451–470", publisher="Taylor & Francis", doi="10.1080/13873954.2013.784340", url=None,
        verified="Crossref DOI resolved; issue 5"),
    "sadjina2017": dict(type="article",
        authors=["Sadjina, S.", "Kyllingstad, L.T.", "Skjong, S.", "Pedersen, E."], year=2017,
        title="Energy conservation and power bonds in co-simulations: Non-iterative adaptive step size control and error estimation",
        journal_or_booktitle="Engineering with Computers", volume="33", pages="607–620", publisher="Springer",
        doi="10.1007/s00366-016-0492-8", url=None, verified="Crossref DOI resolved; issue 3; online Nov 2016"),
    "mathworks_rt": dict(type="misc", authors=["MathWorks"], year=None,
        title="Rate Transition block reference and 'Handle Rate Transitions' (Simulink documentation)",
        journal_or_booktitle=None, volume=None, pages=None, publisher="The MathWorks, Inc.", doi=None,
        url="https://www.mathworks.com/help/simulink/slref/ratetransition.html",
        verified="MathWorks documentation pages (via search, 28 Oct 2026); year not stated"),

    # ---------------- Time semantics, quality, provenance in data standards ----------------
    "cf2023": dict(type="standard",
        authors=["Eaton, B.", "Gregory, J.", "Drach, B.", "Taylor, K.", "Hankin, S.", "et al."], year=2023,
        title="NetCDF Climate and Forecast (CF) Metadata Conventions, Version 1.11", journal_or_booktitle=None,
        volume=None, pages=None, publisher=None, doi=None,
        url="https://cfconventions.org/Data/cf-conventions/cf-conventions-1.11/cf-conventions.html",
        verified="Document page: version 1.11 dated 05 Dec 2023, first five authors; Section 7.3 cell_methods read"),
    "ogc_waterml2014": dict(type="standard", authors=["Taylor, P. (ed.)"], year=2014,
        title="OGC WaterML 2.0: Part 1 – Timeseries, Version 2.0.1 (OGC 10-126r4)", journal_or_booktitle=None,
        volume=None, pages=None, publisher="Open Geospatial Consortium", doi=None,
        url="https://repository.oceanbestpractices.org/handle/11329/1083",
        verified="Ocean Best Practices repository record (editor, year, doc number); interpolation-type list confirmed via OGC wiki and a CityGML Energy ADE catalogue, not the normative text"),
    "doe2024rdd": dict(type="misc", authors=["DOE"], year=2024,
        title="EnergyPlus Version 24.1 Output Details and Examples: eplusout.rdd", journal_or_booktitle=None,
        volume=None, pages=None, publisher="U.S. Department of Energy", doi=None,
        url="https://bigladdersoftware.com/epx/docs/24-1/output-details-and-examples/eplusout-rdd.html",
        verified="Page read 28 Oct 2026; year inferred from version 24.1"),
    "doe_extif": dict(type="misc", authors=["DOE"], year=None,
        title="EnergyPlus Version 9.0 External Interface(s) Application Guide: BCVTB Examples", journal_or_booktitle=None,
        volume=None, pages=None, publisher="U.S. Department of Energy", doi=None,
        url="https://bigladdersoftware.com/epx/docs/9-0/external-interfaces-application-guide/bcvtb-examples.html",
        verified="Page read 28 Oct 2026 (values obtained at start of each zone time step, held during it); release year not on page"),
    "lbnl_fmuexport": dict(type="misc", authors=["LBNL"], year=None,
        title="FMU Export of EnergyPlus User Guide, Section 5: Usage of EnergyPlus as an FMU", journal_or_booktitle=None,
        volume=None, pages=None, publisher="Lawrence Berkeley National Laboratory", doi=None,
        url="https://simulationresearch.lbl.gov/fmu/EnergyPlus/export/userGuide/usage.html",
        verified="Page read 28 Oct 2026 (copyright 2013–2021; time steps per hour must equal FMU sampling time)"),
    "opcua_part8": dict(type="standard", authors=["OPC Foundation"], year=None,
        title="OPC 10000-8: OPC Unified Architecture, Part 8: Data Access (v1.05.07), Section 7.3 Data Access status codes",
        journal_or_booktitle=None, volume=None, pages=None, publisher="OPC Foundation", doi=None,
        url="https://reference.opcfoundation.org/specs/OPC-10000-8/7.3",
        verified="OPC Foundation online reference read 28 Oct 2026; release date not shown"),
    "iec61850_7_3": dict(type="standard", authors=["IEC"], year=2010,
        title="IEC 61850-7-3:2010 Communication networks and systems for power utility automation – Part 7-3: Basic communication structure – Common data classes",
        journal_or_booktitle=None, volume=None, pages=None, publisher="International Electrotechnical Commission",
        doi=None, url="https://webstore.iec.ch/publication/6016",
        verified="IEC webstore listing (published 2010-12-16; consolidated with AMD1:2020); quality attribute content from secondary vendor/CIM docs only"),
    "moreau2013prov": dict(type="standard", authors=["Moreau, L. (ed.)", "Missier, P. (ed.)"], year=2013,
        title="PROV-DM: The PROV Data Model. W3C Recommendation 30 April 2013", journal_or_booktitle=None,
        volume=None, pages=None, publisher="World Wide Web Consortium (W3C)", doi=None,
        url="http://www.w3.org/TR/2013/REC-prov-dm-20130430/", verified="W3C page read 28 Oct 2026"),
    "ruscheinski2017": dict(type="inproceedings", authors=["Ruscheinski, A.", "Uhrmacher, A."], year=2017,
        title="Provenance in modeling and simulation studies – Bridging gaps",
        journal_or_booktitle="2017 Winter Simulation Conference (WSC)", volume=None, pages="872–883",
        publisher="IEEE", doi="10.1109/WSC.2017.8247839", url=None, verified="Crossref DOI resolved"),
    "ruscheinski2025": dict(type="article",
        authors=["Ruscheinski, A.", "Wolpers, A.", "Henning, P.", "Wilsdorf, P.", "Uhrmacher, A.M."], year=2025,
        title="SIMPROV: Provenance capturing for simulation studies", journal_or_booktitle="PLOS One",
        volume="20", pages="e0327607", publisher="Public Library of Science", doi="10.1371/journal.pone.0327607",
        url=None, verified="Crossref DOI resolved (issue 7) and journal page"),

    # ---------------- Publish/subscribe middleware and broker-based co-simulation ----------------
    "omg_dds2015": dict(type="standard", authors=["Object Management Group"], year=2015,
        title="Data Distribution Service (DDS), Version 1.4 (formal/15-04-10)", journal_or_booktitle=None,
        volume=None, pages=None, publisher="Object Management Group", doi=None,
        url="https://www.omg.org/spec/DDS/1.4/", verified="OMG specification page"),
    "oasis_mqtt2019": dict(type="standard",
        authors=["Banks, A. (ed.)", "Briggs, E. (ed.)", "Borgendale, K. (ed.)", "Gupta, R. (ed.)"], year=2019,
        title="MQTT Version 5.0. OASIS Standard, 07 March 2019", journal_or_booktitle=None, volume=None,
        pages=None, publisher="OASIS", doi=None,
        url="https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html", verified="OASIS standard page read"),
    "katipamula2016": dict(type="article",
        authors=["Katipamula, S.", "Haack, J.", "Hernandez, G.", "Akyol, B.", "Hagerman, J."], year=2016,
        title="VOLTTRON: An open-source software platform of the future",
        journal_or_booktitle="IEEE Electrification Magazine", volume="4", pages="15–22", publisher="IEEE",
        doi="10.1109/MELE.2016.2614178", url=None, verified="Crossref DOI resolved; issue 4"),
    "volttron_docs": dict(type="misc", authors=["VOLTTRON developers"], year=None,
        title="VOLTTRON documentation: Messaging and Topics", journal_or_booktitle=None, volume=None, pages=None,
        publisher=None, doi=None,
        url="https://volttron.readthedocs.io/en/main/platform-features/message-bus/topics.html",
        verified="Page read 28 Oct 2026; year not stated"),
    "wen2024": dict(type="article",
        authors=["Wen, J.", "Jiang, L.", "Chen, Y.", "Chu, C.-C.", "Zhu, J.", "Qiu, Z."], year=2024,
        title="Design of data distributed service-based distributed co-simulation platform of power systems",
        journal_or_booktitle="IEEE Transactions on Industry Applications", volume="60", pages="8115–8127",
        publisher="IEEE", doi="10.1109/TIA.2024.3429466", url=None, verified="Crossref DOI resolved; issue 6"),
    "paduani2023": dict(type="inproceedings",
        authors=["Paduani, V.", "Kadavil, R.", "Hooshyar, H.", "Haddadi, A.", "Jakaria, A.H.M.", "Huque, A."], year=2023,
        title="Real-time T&D co-simulation for testing grid impact of high DER participation",
        journal_or_booktitle="2023 IEEE PES Grid Edge Technologies Conference & Exposition (Grid Edge)",
        volume=None, pages="1–5", publisher="IEEE", doi="10.1109/GridEdge54130.2023.10102714", url=None,
        verified="Crossref DOI resolved; MQTT use confirmed in arXiv:2302.01120 abstract"),

    # ---------------- Record/replay and reproducibility ----------------
    "chen2015replay": dict(type="article",
        authors=["Chen, Y.", "Zhang, S.", "Guo, Q.", "Li, L.", "Wu, R.", "Chen, T."], year=2015,
        title="Deterministic replay: A survey", journal_or_booktitle="ACM Computing Surveys", volume="48",
        pages="1–47", publisher="ACM", doi="10.1145/2790077", url=None,
        verified="Crossref DOI resolved (issue 2; subtitle 'A Survey')"),
    "feng2025agentrr": dict(type="misc",
        authors=["Feng, E.", "Zhou, W.", "Liu, Z.", "Chen, L.", "Dong, Y.", "Zhang, C.", "Zhao, Y.", "Du, D.",
                 "Hua, Z.", "Xia, Y.", "Chen, H."], year=2025,
        title="Get experience from practice: LLM agents with record & replay",
        journal_or_booktitle="arXiv preprint arXiv:2505.17716", volume=None, pages=None, publisher="arXiv",
        doi=None, url="https://arxiv.org/abs/2505.17716", verified="arXiv abstract page (submitted 23 May 2025)"),
    "ouyang2025": dict(type="article", authors=["Ouyang, S.", "Zhang, J.M.", "Harman, M.", "Wang, M."], year=2025,
        title="An empirical study of the non-determinism of ChatGPT in code generation",
        journal_or_booktitle="ACM Transactions on Software Engineering and Methodology", volume="34",
        pages="1–28", publisher="ACM", doi="10.1145/3697010", url=None,
        verified="Crossref DOI resolved (issue 2); temperature-0 finding from arXiv:2308.02828 abstract"),
    "henderson2018": dict(type="inproceedings",
        authors=["Henderson, P.", "Islam, R.", "Bachman, P.", "Pineau, J.", "Precup, D.", "Meger, D."], year=2018,
        title="Deep reinforcement learning that matters",
        journal_or_booktitle="Proceedings of the AAAI Conference on Artificial Intelligence", volume="32",
        pages=None, publisher="AAAI", doi="10.1609/aaai.v32i1.11694", url=None,
        verified="Crossref DOI resolved (issue 1; no pages in Crossref)"),

    # ---------------- Deterministic timing, latency, asynchrony ----------------
    "henzinger2003": dict(type="article", authors=["Henzinger, T.A.", "Horowitz, B.", "Kirsch, C.M."], year=2003,
        title="Giotto: A time-triggered language for embedded programming", journal_or_booktitle="Proceedings of the IEEE",
        volume="91", pages="84–99", publisher="IEEE", doi="10.1109/JPROC.2002.805825", url=None,
        verified="Crossref DOI resolved; issue 1"),
    "zhao2007": dict(type="inproceedings", authors=["Zhao, Y.", "Liu, J.", "Lee, E.A."], year=2007,
        title="A programming model for time-synchronized distributed real-time systems",
        journal_or_booktitle="13th IEEE Real Time and Embedded Technology and Applications Symposium (RTAS'07)",
        volume=None, pages="259–268", publisher="IEEE", doi="10.1109/RTAS.2007.5", url=None,
        verified="Crossref DOI resolved"),
    "lohstroh2021": dict(type="article", authors=["Lohstroh, M.", "Menard, C.", "Bateni, S.", "Lee, E.A."], year=2021,
        title="Toward a Lingua Franca for deterministic concurrent systems",
        journal_or_booktitle="ACM Transactions on Embedded Computing Systems", volume="20", pages="1–27",
        publisher="ACM", doi="10.1145/3448128", url=None,
        verified="Crossref DOI resolved (issue 4); deadline semantics from lf-lang.org docs"),
    "katsikopoulos2003": dict(type="article", authors=["Katsikopoulos, K.V.", "Engelbrecht, S.E."], year=2003,
        title="Markov decision processes with delays and asynchronous cost collection",
        journal_or_booktitle="IEEE Transactions on Automatic Control", volume="48", pages="568–574",
        publisher="IEEE", doi="10.1109/TAC.2003.809799", url=None, verified="Crossref DOI resolved; issue 4"),
    "ramstedt2019": dict(type="inproceedings", authors=["Ramstedt, S.", "Pal, C."], year=2019,
        title="Real-time reinforcement learning",
        journal_or_booktitle="Advances in Neural Information Processing Systems 32 (NeurIPS 2019)", volume="32",
        pages="3073–3082", publisher="Curran Associates", doi=None,
        url="https://papers.nips.cc/paper/8571-real-time-reinforcement-learning",
        verified="NeurIPS proceedings page and metadata JSON (pages); publisher name not checked on page"),
    "xiao2020": dict(type="inproceedings",
        authors=["Xiao, T.", "Jang, E.", "Kalashnikov, D.", "Levine, S.", "Ibarz, J.", "Hausman, K.", "Herzog, A."],
        year=2020, title="Thinking while moving: Deep reinforcement learning with concurrent control",
        journal_or_booktitle="International Conference on Learning Representations (ICLR 2020)", volume=None,
        pages=None, publisher=None, doi=None, url="https://arxiv.org/abs/2004.06089",
        verified="arXiv page ('Published as a conference paper at ICLR 2020') and ICLR virtual poster page"),
    "xie2025": dict(type="inproceedings",
        authors=["Xie, Z.", "Kang, H.", "Sheng, Y.", "Krishna, T.", "Fatahalian, K.", "Kozyrakis, C."], year=2025,
        title="AI Metropolis: Scaling large language model-based multi-agent simulation with out-of-order execution",
        journal_or_booktitle="Proceedings of Machine Learning and Systems 7 (MLSys 2025)", volume="7", pages=None,
        publisher=None, doi=None,
        url="https://proceedings.mlsys.org/paper_files/paper/2025/hash/4f31327e046913c7238d5b671f5d820e-Abstract-Conference.html",
        verified="MLSys 2025 proceedings page"),

    # ---------------- LLM/AI in building simulation and control ----------------
    "song2023": dict(type="misc", authors=["Song, L.", "Zhang, C.", "Zhao, L.", "Bian, J."], year=2023,
        title="Pre-trained large language models for industrial control",
        journal_or_booktitle="arXiv preprint arXiv:2308.03028", volume=None, pages=None, publisher="arXiv",
        doi=None, url="https://arxiv.org/abs/2308.03028", verified="arXiv page and Microsoft Research page"),
    "zhang2024bem": dict(type="article", authors=["Zhang, L.", "Chen, Z.", "Ford, V."], year=2024,
        title="Advancing building energy modeling with large language models: Exploration and case studies",
        journal_or_booktitle="Energy and Buildings", volume="323", pages="114788", publisher="Elsevier",
        doi="10.1016/j.enbuild.2024.114788", url=None, verified="Crossref DOI resolved"),
    "zhang2025agentic": dict(type="article", authors=["Zhang, L.", "Ford, V.", "Chen, Z.", "Chen, J."], year=2025,
        title="Automatic building energy model development and debugging using large language models agentic workflow",
        journal_or_booktitle="Energy and Buildings", volume="327", pages="115116", publisher="Elsevier",
        doi="10.1016/j.enbuild.2024.115116", url=None, verified="Crossref DOI resolved"),
    "sawada2024": dict(type="inproceedings",
        authors=["Sawada, T.", "Hasegawa, T.", "Yokoyama, K.", "Mizuno, M."], year=2024,
        title="Office-in-the-loop for building HVAC control with multimodal foundation models",
        journal_or_booktitle="Proceedings of the 11th ACM International Conference on Systems for Energy-Efficient Buildings, Cities, and Transportation (BuildSys '24)",
        volume=None, pages="110–120", publisher="ACM", doi="10.1145/3671127.3698182", url=None,
        verified="Crossref DOI resolved"),
    "sawada2025": dict(type="article",
        authors=["Sawada, T.", "Mizuno, M.", "Hasegawa, T.", "Yokoyama, K.", "Kono, M."], year=2025,
        title="Office-in-the-Loop: An investigation into agentic AI for advanced building HVAC control systems",
        journal_or_booktitle="Data-Centric Engineering", volume="6", pages="e31", publisher="Cambridge University Press",
        doi="10.1017/dce.2025.10010", url=None, verified="Crossref DOI resolved (article e31)"),
    "sasaki2025": dict(type="inproceedings",
        authors=["Sasaki, S.", "Techasarntikul, N.", "Ohsita, Y.", "Shimonishi, H."], year=2025,
        title="LLM-driven agent-based evaluation framework for occupant-centric HVAC control considering individual differences and movement behavior",
        journal_or_booktitle="Proceedings of the 12th ACM International Conference on Systems for Energy-Efficient Buildings, Cities, and Transportation (BuildSys '25)",
        volume=None, pages="376–383", publisher="ACM", doi="10.1145/3736425.3772325", url=None,
        verified="Crossref DOI resolved"),
    "ko2025": dict(type="inproceedings", authors=["Ko, Y.-D.", "Jain, R."], year=2025,
        title="DARLIN: Domain-guided augmented retrieval for LLM-based interpretable HVAC control",
        journal_or_booktitle="Proceedings of the 12th ACM International Conference on Systems for Energy-Efficient Buildings, Cities, and Transportation (BuildSys '25)",
        volume=None, pages="440–443", publisher="ACM", doi="10.1145/3736425.3772322", url=None,
        verified="Crossref DOI resolved"),
    "dieng2026": dict(type="inproceedings", authors=["Dieng, O.", "Zhao, K.", "Lee, S."], year=2026,
        title="Knowledge-guided self-reflection LLM agents for HVAC simulation in EnergyPlus",
        journal_or_booktitle="Proceedings of the 13th ACM International Conference on Systems for Energy-Efficient Buildings, Cities, and Transportation (BuildSys '26)",
        volume=None, pages="103–114", publisher="ACM", doi="10.1145/3744256.3812593", url=None,
        verified="Crossref DOI resolved"),
    "li2025mcp": dict(type="article", authors=["Li, H.", "Xu, Y.", "Hong, T."], year=2025,
        title="EnergyPlus-MCP: A model-context-protocol server for AI-driven building energy modeling",
        journal_or_booktitle="SoftwareX", volume="32", pages="102367", publisher="Elsevier",
        doi="10.1016/j.softx.2025.102367", url=None, verified="Crossref DOI resolved"),
    "liu2026colllm": dict(type="article",
        authors=["Liu, G.", "Wang, Y.", "Lu, Y.", "Wu, H.", "Liu, K.", "Liu, L.", "Chen, J.", "Fu, Q."], year=2026,
        title="Co-LLM: A retrieval-augmented language model framework for chiller optimization control",
        journal_or_booktitle="Journal of Building Engineering", volume="121", pages="115683", publisher="Elsevier",
        doi="10.1016/j.jobe.2026.115683", url=None,
        verified="Crossref DOI resolved; ~2 s/step latency figure only as reported by neubauer2026"),
    "neubauer2026": dict(type="misc",
        authors=["Neubauer, A.", "Hong, T.", "Li, H.", "Yu, M.", "Darbandi, A.", "Fürst, Y.", "Kriegel, M."], year=2026,
        title="Large language models for HVAC operations in building energy systems: A critical review of methods, applications, and deployment readiness",
        journal_or_booktitle="arXiv preprint arXiv:2609.05314", volume=None, pages=None, publisher="arXiv",
        doi=None, url="https://arxiv.org/abs/2609.05314", verified="arXiv abstract and HTML pages (submitted 4 Sep 2026)"),
}
```

**Count: 59 new verified references.** Some fields are `None` (see each `verified` note). For the
paper, a lean core of about 25 is enough: `ieee1516_1_2010`, `fujimoto2000`, `jefferson1985`,
`ofenloch2022`, `junghanns2021`, `ssp2_2024`, `gomes2019semantic`, `broman2013`, `lee1998`,
`benedikt2013nepce`, `sadjina2017`, `cf2023`, `ogc_waterml2014`, `doe2024rdd`, `doe_extif`,
`opcua_part8`, `oasis_mqtt2019`, `katipamula2016`, `chen2015replay`, `ouyang2025`, `henzinger2003`,
`lohstroh2021`, `ramstedt2019`, `xie2025`, `helics_docs`, `mosaik_docs`, plus 3–4 of the LLM
papers (`song2023`, `zhang2025agentic`, `sawada2025`, `neubauer2026`).

**Considered but not included (could not verify):**
- The Brick schema "aggregate" property (no Brick documentation found).
- The FNCS TMS/DEVS 2014 paper (no DOI or full record found; the MASCOTS paper is used instead).
- Alfalfa's clock parameter names.
- The exact normative text of WaterML interpolation types and IEC 61850 quality (secondary sources
  only, noted above).

---

## 4. How to position the novelty

1. Do not frame Py-CoSim as "message-based" or "publish/subscribe" co-simulation in itself. HLA,
   HELICS, mosaik, DDS/MQTT platforms and VOLTTRON already do that, and reviewers in co-simulation
   will know it. Open the related-work section by granting that, then show a feature table (HLA,
   HELICS, mosaik, FMI 3.0/SSP, BCVTB/FMU, Sinergym/BOPTEST vs Py-CoSim) with rows for interval time
   semantics, multi-rate alignment, per-value quality, record/replay, async AI with deterministic
   timing, and direct EnergyPlus API coupling.
2. Put the main novelty claim on C1: making the time meaning of every exchanged value explicit, as
   CF conventions and WaterML already do for stored data and EnergyPlus does for its own reports,
   and having the hub align values automatically to each component's step window. Credit semantic
   adaptation (Gomes et al. 2019) as the closest co-simulation idea, and stress that here the
   adaptation is derived, not hand-written.
3. Present C3 and C4 as the established principles of record/replay, status codes, provenance and
   logical execution time with deadline handlers, *transferred to AI components in building energy
   co-simulation*. This is defensible as category (b) and is the right fit for *Building
   Simulation*. Back it with the reproducibility and latency gaps that recent LLM-for-HVAC work
   reports (Ouyang et al. 2025; Neubauer et al. 2026).
4. Treat C2, C5 and C6 as engineering features that make the framework usable, not as
   contributions. Report the ~2 % cost bias as a quantified illustration of a known kind of alignment
   error (Kübler and Schiehlen 2000; Gomes et al. 2018), measured in a realistic EnergyPlus + PV +
   battery + tariff case.
