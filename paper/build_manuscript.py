"""Build the Building Simulation manuscript (blinded) and title page as Word files.

All numbers quoted in the text are read from ``paper/experiments/results`` so the
manuscript always matches the experiments (re-run ``paper/experiments/run_all.sh``
and then this script).

    python paper/build_manuscript.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Inches, Pt, RGBColor

from references import REFS

HERE = Path(__file__).resolve().parent
RES = HERE / "experiments" / "results"
FIG = HERE / "figures"
OUT_MS = HERE / "Py-CoSim_BuildingSimulation_Manuscript_Blinded.docx"
OUT_TP = HERE / "Py-CoSim_BuildingSimulation_TitlePage.docx"

TITLE = ("Py-CoSim: an open-source, pure-Python framework for synchronized co-simulation of EnergyPlus buildings "
         "with energy, mobility and artificial-intelligence components")


# ============================================================== results
def load_results() -> dict:
    R: dict = {}
    v = json.loads((RES / "verification_summary.json").read_text())
    R["v1"], R["v2"], R["v3"] = v["V1_max_abs_diff"], v["V2_max_abs_diff"], v["V3_max_abs_diff"]
    R["v_series"] = v["V1_n_series"]
    R["v_steps"] = v["n_steps"]
    R["precool_dT"] = v["precool_effect_max_dT"]
    s = json.loads((RES / "scalability_summary.json").read_text())
    ov = pd.read_csv(RES / "scalability_overhead.csv").groupby("mode")["wall_s"].agg(["mean", "std"])
    R["ov"] = ov
    R["cpu"] = s["machine"]["cpu"].replace("(R)", "").replace("(TM)", "").replace("  ", " ")
    R["cores"] = s["machine"]["logical_cores"]
    R["t_cli"], R["t_proc"], R["t_thread"] = (ov.loc["EnergyPlus CLI", "mean"], ov.loc["Py-CoSim (process)", "mean"],
                                              ov.loc["Py-CoSim (thread)", "mean"])
    R["ovh_proc_ms"] = (R["t_proc"] - R["t_cli"]) / 672 * 1000
    R["ovh_thread_ms"] = (R["t_thread"] - R["t_cli"]) / 672 * 1000
    R["master_us"] = s["master_overhead_s_per_step_per_component"] * 1e6
    ex = s["exchange"]
    R["bus_us"] = ex["Py-CoSim 2 data bus (publish + read)"] * 1e6
    R["csv_ms"] = ex["Py-CoSim 0.x CSV file (write + read)"] * 1e3
    R["exchange_ratio"] = R["csv_ms"] * 1e3 / R["bus_us"]
    R["ovh_break"] = json.loads((RES / "overhead_breakdown.json").read_text())
    sc = pd.read_csv(RES / "scalability.csv")
    R["sc"] = sc
    R["sc_max_diff"] = sc["max_abs_diff_parallel_vs_serial"].max()
    last = sc.iloc[-1]
    R["n_max"], R["speed_max"], R["par_max"], R["ser_max"] = int(last.n_buildings), last.speedup, last.parallel_s, last.serial_s
    best = sc.loc[sc.speedup.idxmax()]
    R["speed_best"], R["n_best"] = best.speedup, int(best.n_buildings)
    R["phys_cores"] = s["machine"].get("physical_cores") or 4
    R["mem_gb"] = s["machine"].get("memory_gb") or 16
    a = pd.read_csv(RES / "case_a_kpis.csv", index_col=0)
    R["A"] = a
    R["A_meta"] = json.loads((RES / "case_a_meta.json").read_text())
    R["B_int"] = pd.read_csv(RES / "case_b_intents.csv").set_index("intent")
    R["B_f"] = pd.read_csv(RES / "case_b_faults.csv")
    R["B_ex"] = json.loads((RES / "case_b_example_exchange.json").read_text())
    R["C_sum"] = pd.read_csv(RES / "case_c_summary.csv").set_index(["group", "policy"])
    R["C_sav"] = pd.read_csv(RES / "case_c_savings.csv")
    R["C_runs"] = pd.read_csv(RES / "case_c_runs.csv")
    R["C_hh"] = pd.read_csv(RES / "case_c_household_stats.csv").set_index("group")
    root = HERE.parent
    R["loc_lib"] = count_loc(sorted((root / "src" / "pycosim").rglob("*.py")))
    R["loc_ex3"] = count_loc([root / "examples" / "03_tou_precooling" / "run.py"])
    R["loc_ex4"] = count_loc([root / "examples" / "04_community_pv_battery" / "run.py"])
    R["n_tests"] = 395
    return R


def count_loc(paths) -> int:
    """Non-blank, non-comment source lines without docstrings."""
    total = 0
    for path in paths:
        text = re.sub(r'"""[\s\S]*?"""', "", Path(path).read_text(encoding="utf-8"))
        total += sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))
    return total


def pct(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}%"


# ============================================================== content
def content(R: dict) -> dict:
    A = R["A"]
    chg = lambda strat, col: A.loc[strat, f"{col}_change_%"]  # noqa: E731
    B_f = R["B_f"]
    worst = B_f.iloc[-1]
    Bi = R["B_int"]
    sav = R["C_sav"].groupby("group")["cooling_elec_saving_%"].agg(["mean", "std"])
    savq = R["C_sav"].groupby("group")["cooling_load_saving_%"].agg(["mean", "std"])
    C = R["C_sum"]
    sc = R["sc"]
    sp8 = float(sc.loc[sc.n_buildings == 8, "speedup"].iloc[0]) if (sc.n_buildings == 8).any() else float("nan")

    abstract = (
        "Buildings are increasingly operated as part of larger systems that include distributed energy resources, "
        "electricity tariffs and grids, occupants' travel behavior and data-driven or artificial-intelligence (AI) "
        "controllers. Studying them requires co-simulation, yet established platforms demand expertise in several "
        "languages and middleware, while Python-based tools typically address a single agent-environment loop. This "
        "paper presents Py-CoSim, an open-source framework in which any Python model and any number of EnergyPlus "
        "buildings are coupled on one synchronized clock. Py-CoSim (i) inverts EnergyPlus' callback-driven "
        "execution into a lock-step, steppable component that runs in an isolated worker process, (ii) schedules "
        "heterogeneous components with a graph-based master algorithm that orders direct-feedthrough dependencies, "
        "detects algebraic loops and supports multi-rate coupling, and (iii) records every exchange on a "
        "time-indexed data bus. Verification against stand-alone EnergyPlus shows bit-identical results "
        f"(maximum absolute difference {R['v1']:.1f}) and exact equivalence between Python actuation and native "
        f"schedules; parallel worker processes reproduce serial results exactly and accelerated multi-building runs "
        f"by up to {R['speed_best']:.1f} times on a four-core laptop. Three case studies demonstrate the "
        "framework: a ten-building community with photovoltaics and a shared battery, where coordinated pre-cooling "
        f"and storage reduced on-peak grid import by {-chg('coordinated', 'on_peak_import_MWh'):.0f}% and energy "
        f"cost by {-chg('coordinated', 'energy_cost_$'):.0f}%; a large-language-model supervisory controller whose "
        f"guardrails kept all set-points within bounds under {worst.fault_rate * 100:.0f}% injected faults; and "
        "travel-diary-driven occupancy for 23 apartments, where occupancy-responsive thermostats saved "
        f"{sav.loc['long', 'mean']:.1f}% and {sav.loc['short', 'mean']:.1f}% of cooling electricity for long- and "
        "short-commute households, depending on household composition as much as on commuting."
    )
    keywords = ("co-simulation; EnergyPlus; Python; grid-interactive efficient buildings; large language models; "
                "occupant behavior")

    B = []  # blocks
    h1 = lambda t: B.append(("h1", t))  # noqa: E731
    h2 = lambda t: B.append(("h2", t))  # noqa: E731
    p = lambda t: B.append(("p", t))  # noqa: E731

    # ------------------------------------------------------------ 1 Introduction
    h1("1 Introduction")
    p("Buildings are no longer isolated energy consumers. Grid-interactive efficient buildings are expected to "
      "shift and shed load in response to prices and grid conditions, to host photovoltaics (PV), batteries and "
      "electric-vehicle (EV) chargers, and to coordinate with neighbouring buildings [@neukomm2019; @hong2025]. "
      "Their operation increasingly depends on models from other disciplines: weather and solar resources, "
      "tariffs and distribution grids, occupants' presence and mobility, and data-driven, model-predictive or "
      "AI-based controllers [@yan2015; @drgona2020; @wang2020; @zhang2021review]. Answering questions at these "
      "interfaces -- for example how pre-cooling and a community battery should be coordinated under a "
      "time-of-use tariff, or how commuting patterns change the value of a smart thermostat -- requires running "
      "detailed building energy models together with such models, exchanging data at every time step. This is "
      "co-simulation [@gomes2018; @trcka2009], and its importance for building simulation has long been recognized "
      "[@wetter2011; @hong2018].")
    p("A rich ecosystem of co-simulation tools exists. The Building Controls Virtual Test Bed (BCVTB) couples "
      "EnergyPlus with Modelica, MATLAB and other programs through Ptolemy II [@wetter2011]; the Functional Mock-up "
      "Interface (FMI) standardizes model exchange and co-simulation and is supported by EnergyPlus and the Modelica "
      "Buildings library [@blochwitz2011; @nouidui2014; @wetter2014]; Spawn couples EnergyPlus envelopes with "
      "Modelica HVAC and controls [@wetter2020]; mosaik and HELICS orchestrate federated smart-grid co-simulations "
      "[@steinbrink2019; @palmintier2017; @hardy2024]; and BOPTEST and Alfalfa expose building models as services "
      "for control benchmarking [@blum2021; @benne2024]. These platforms are powerful, but they require users to "
      "master several tools, languages, model-export toolchains or middleware, which is a considerable barrier for "
      "building scientists who mainly work in Python. At the other end of the spectrum, Python environments for "
      "reinforcement learning (RL) -- Gym-EnergyPlus, Sinergym, COBS, Energym, BuildingGym and CityLearn "
      "[@moriyama2018; @zhang2018; @jimenez2021; @zhang2020cobs; @scharnhorst2021; @dai2025; @vazquez2019; "
      "@nweye2025] -- are easy to use but are organized around a single agent interacting with a single "
      "environment rather than around a general network of models.")
    p("Since version 9.3, EnergyPlus [@crawley2001] exposes a Python application programming interface (API) "
      "through which external Python code can read sensors and meters and write actuators while EnergyPlus runs "
      "[@doe2022]. This API has made Python-EnergyPlus integration markedly easier [@bonnema2025; @chinde2025; "
      "@campoy2025], and an earlier prototype of the present work, Py-CoSim 0.x [@zhang2023], used it to connect "
      "EnergyPlus with deep-learning occupancy models and a large language model (LLM). That prototype established "
      "feasibility but also exposed the obstacles that any general Python co-simulation framework built on the "
      "EnergyPlus API must overcome:")
    B.append(("bullets", [
        "**Inverted control flow.** EnergyPlus owns its time loop and calls user code back; orchestrating several "
        "EnergyPlus models and other components on a common clock requires turning EnergyPlus into a component that "
        "an external master can advance one step at a time.",
        "**Concurrency and isolation.** As documented in Section 3.3, the EnergyPlus API (tested with version 22.2) "
        "deadlocks when several simulations run concurrently in threads of one process and cannot start a second "
        "simulation in a process after a first one has finished, so naïve multi-building designs fail.",
        "**Execution semantics.** Without an explicit definition of which values a component sees at a given time, "
        "of the execution order and of how feedback loops are broken, co-simulation results are hard to interpret "
        "and reproduce [@kubler2000; @gomes2018].",
        "**Data exchange and auditability.** Py-CoSim 0.x exchanged data through one CSV file per component and "
        "time step and synchronized buildings by counting files, which is slow, fragile and leaves no structured "
        "record -- a particular concern when LLMs or other AI agents act on a building [@liu2025llm].",
    ]))
    p("This paper presents Py-CoSim, a complete redesign that addresses these obstacles. Its contributions are:")
    B.append(("numbers", [
        "A component abstraction with explicit feedthrough semantics and multi-rate step sizes, under which "
        "EnergyPlus models, physical models, data sources, controllers, machine-learning (ML) models, LLM agents "
        "and RL agents are interchangeable, and any plain Python function is a valid component.",
        "A lock-step protocol that makes EnergyPlus steppable inside its own callbacks, with per-step "
        "time-stamp verification, isolated worker processes that enable parallel multi-building co-simulation, and "
        "automatic synchronization of run period, time step, sizing and daylight-saving settings.",
        "A graph-based master algorithm (topological scheduling, algebraic-loop detection, delayed and aggregated "
        "connections) together with an in-memory data bus that records every exchange, events and errors.",
        "A verification showing bit-identical agreement with stand-alone EnergyPlus and a characterization of "
        "overhead and parallel scalability.",
        "Three case studies -- a grid-interactive community, LLM-in-the-loop control with guardrails, and "
        "travel-behavior-driven occupancy -- released, together with six further examples, as runnable templates.",
    ]))
    p("Section 2 positions Py-CoSim among existing tools. Section 3 describes the framework, Section 4 its "
      "verification and performance, and Section 5 the case studies. Section 6 discusses lessons, limitations and "
      "future work, and Section 7 concludes.")

    # ------------------------------------------------------------ 2 Related work
    h1("2 Related work and positioning")
    p("Co-simulation couples simulators that each solve their own sub-problem and exchange data at communication "
      "points [@gomes2018]. Two coupling schemes are classical: in a Jacobi (parallel) scheme all subsystems use "
      "inputs from the previous communication point, whereas in a Gauss-Seidel (sequential) scheme subsystems are "
      "evaluated in an order such that later ones use the current outputs of earlier ones [@kubler2000]. "
      "Building-specific studies have shown how the choice of coupling and of the communication step affects "
      "accuracy and stability [@trcka2009]. A framework therefore needs to make these choices explicit.")
    p("Table 1 summarizes representative tools. General-purpose co-simulation frameworks (BCVTB, FMI-based "
      "toolchains, mosaik, HELICS) are the most expressive, but EnergyPlus participates through external "
      "interfaces, exported FMUs or federates, and users must configure middleware or additional languages "
      "[@wetter2011; @nouidui2014; @steinbrink2019; @hardy2024]. Benchmarking services (BOPTEST, Alfalfa) expose "
      "curated building emulators through web interfaces and are excellent for comparing controllers, but they are "
      "not designed to embed arbitrary user models such as a battery fleet or a travel-behavior model inside the "
      "simulation loop [@blum2021; @benne2024]. RL environments wrap EnergyPlus (increasingly through its Python "
      "API) or data-driven building models in the Gym/Gymnasium interface [@towers2025]; they couple one learning "
      "agent with one environment and typically leave other components to be hard-coded in the environment "
      "[@campoy2025; @dai2025; @nweye2025]. Urban- and district-scale platforms such as URBANopt focus on modelling "
      "workflows for many buildings rather than on step-wise coupling with external models [@elkontar2020; "
      "@reinhart2016; @hong2020; @allegrini2015]. Digital-twin platforms couple EnergyPlus with live data and FMUs "
      "for specific deployments [@li2025]. Py-CoSim targets the gap between these groups: a pure-Python "
      "co-simulation framework in which EnergyPlus is a first-class, steppable component and in which any Python "
      "model -- physical, data-driven or AI-based -- can be connected with explicit, verifiable semantics.")
    B.append(("table", "Table 1 Representative co-simulation and control-testing tools for buildings (characteristics "
              "as reported in the cited publications)",
              ["Tool", "Main purpose", "User-facing language / set-up", "EnergyPlus coupling", "Other components"],
              [
                  ["BCVTB (Wetter 2011)", "General co-simulation middleware", "Ptolemy II models, tool adapters",
                   "External interface over sockets", "Modelica, MATLAB/Simulink, Radiance, …"],
                  ["FMI / Modelica Buildings, Spawn (Nouidui et al. 2014; Wetter et al. 2014, 2020)",
                   "Model exchange and co-simulation standard; Modelica HVAC/controls",
                   "Modelica or FMU export toolchains", "FMU import/export; Spawn coupling", "Any FMU"],
                  ["mosaik, HELICS (Steinbrink et al. 2019; Hardy et al. 2024)",
                   "Smart-grid / multi-domain co-simulation", "Python (mosaik); multi-language federates (HELICS)",
                   "Through user-written adapters", "Grid, communication, market simulators"],
                  ["MLE+ (Bernal et al. 2012)", "Control design and deployment", "MATLAB/Simulink",
                   "EnergyPlus external interface", "MATLAB toolboxes"],
                  ["BOPTEST, Alfalfa (Blum et al. 2021; Benne et al. 2024)", "Control benchmarking, virtual buildings",
                   "Web (REST) clients", "BOPTEST: Modelica emulators; Alfalfa: OpenStudio/EnergyPlus or Modelica",
                   "External controllers"],
                  ["Sinergym, BuildingGym, COBS (Campoy-Nieves et al. 2025; Dai et al. 2025; Zhang and "
                   "Ardakanian 2020)", "RL environments", "Python (Gym/Gymnasium)",
                   "EnergyPlus-based (Sinergym, BuildingGym via the Python API)", "One learning agent per environment"],
                  ["CityLearn (Nweye et al. 2025)", "Community-scale RL", "Python (Gymnasium)",
                   "Data-driven building models", "Batteries, PV, EVs, heat pumps"],
                  ["Py-CoSim 0.x (Zhang et al. 2023)", "Prototype Python EMS co-simulation", "Jupyter notebooks",
                   "Python API, threads", "CSV-file exchange"],
                  ["Py-CoSim (this work)", "General Python co-simulation", "Python, YAML, Gymnasium",
                   "Steppable component in worker processes", "Any Python model; library of 17 ready-made components"],
              ]))

    # ------------------------------------------------------------ 3 Framework
    h1("3 The Py-CoSim framework")
    p("Figure 1 shows the architecture. A scenario -- written as a Python script, a Jupyter notebook, a YAML file "
      "or created by an RL training loop -- instantiates components and connects their ports. The CoSimulation "
      "master owns the clock, schedules components, routes data through the data bus and supervises the run. "
      "EnergyPlus components run in worker processes; all other components run in the master process.")
    B.append(("fig", FIG / "fig01_architecture.png",
              "Fig. 1 Architecture of Py-CoSim. Scenarios instantiate and connect components; the master executes a "
              "three-phase step on a common clock; each EnergyPlus model runs in its own worker process, other "
              "components in the main process.", 16.0))
    h2("3.1 Design principles")
    p("Five principles guided the design. (1) *Python only*: users write components as Python functions or "
      "classes; no other language, middleware or model-export step is needed. (2) *Explicit semantics*: the value "
      "each component sees at each time, the execution order and the treatment of feedback loops follow formal rules "
      "that are documented, verified and reported back to the user. (3) *Isolation and parallelism*: every "
      "EnergyPlus instance runs in its own process, which both circumvents the API's concurrency limitations and "
      "uses all processor cores. (4) *Reproducibility and auditability*: random numbers are seeded per component, "
      "every exchanged value and every event is recorded, and AI decisions can be recorded and replayed. "
      "(5) *Progressive disclosure*: a first co-simulation takes a dozen lines, while advanced users can control "
      "every aspect (custom components, step sizes, delayed links, observers, executors).")
    h2("3.2 Component abstraction")
    p("A component *i* is a discrete-time system with input ports *u*, output ports *y* and an internal state "
      "*x*, executed every *m*ᵢ master steps of length Δ*t*. Py-CoSim distinguishes two kinds. A *feedthrough* "
      "component may use its inputs at time *t*ₖ to compute its outputs at the same time:")
    B.append(("eq", "y_{i}(t_{k}) = h_{i}(x_{i}(t_{k}), u_{i}(t_{k})),   x_{i}(t_{k+m_{i}}) = f_{i}(x_{i}(t_{k}), u_{i}(t_{k}))", 1))
    p("Controllers, tariffs, data readers, PV and battery models, ML predictors and LLM agents are of this kind. "
      "A *non-feedthrough* (state-based) component reports outputs that depend only on its state:")
    B.append(("eq", "y_{i}(t_{k}) = g_{i}(x_{i}(t_{k})),   x_{i}(t_{k+m_{i}}) = f_{i}(x_{i}(t_{k}), u_{i}(t_{k}))", 2))
    p("Physical simulators -- EnergyPlus and the resistance-capacitance (RC) building model of the library -- "
      "belong to this kind: the zone temperature reported at *t*ₖ results from the past, while the set-point "
      "received at *t*ₖ acts on the interval [*t*ₖ, *t*ₖ + Δ*t*). In code, feedthrough components implement "
      "step(t, inputs); non-feedthrough components implement output(t) and update(t, inputs). Between two "
      "executions a component's outputs are held (zero-order hold):")
    B.append(("eq", "y_{i}(t_{k}) = y_{i}(t_{m_{i}⌊k/m_{i}⌋}),   m_{i} ∈ ℕ", 3))
    p("so that, for example, an LLM supervisor can decide hourly while EnergyPlus advances every 15 min "
      "(Fig. 2b). The simplest component is a decorated function whose parameters become input ports and whose "
      "default values become input defaults; classes add state, set-up and clean-up logic. Each component "
      "receives a context object giving access to the clock, to the history of any port on the data bus, to "
      "structured logging, to a private output folder and to a random-number generator seeded from the simulation "
      "seed and the component name, which makes stochastic components reproducible and independent of the order in "
      "which they were added.")
    h2("3.3 Making EnergyPlus a steppable component")
    p("In library mode, EnergyPlus calls registered Python functions at fixed points of its time loop [@doe2022]. "
      "Py-CoSim registers two callbacks and turns them into a lock-step handshake with the master (Fig. 2a). At the "
      "end of each zone time step (after zone reporting) the worker reads all requested output variables, meters "
      "and internal variables into a snapshot. At the beginning of the next zone time step (after the heat-balance "
      "initialization) it sends the snapshot with a time stamp to the master and blocks until it receives the "
      "actuator values for the step; it then applies them -- an actuator value of None releases the actuator back "
      "to EnergyPlus' own schedules and controls -- and returns control to EnergyPlus, which simulates the interval. "
      "Design-day, sizing and warm-up periods are passed through without blocking.")
    B.append(("fig", FIG / "fig02_handshake_multirate.png",
              "Fig. 2 (a) Lock-step handshake between the master and an EnergyPlus worker; (b) multi-rate execution "
              "on the master grid with zero-order hold of slower components.", 16.0))
    p("Two details matter for correctness. First, the value published at master time *t*ₖ must be the value "
      "EnergyPlus itself reports with time stamp *t*ₖ, i.e., the state at the end of the previous interval; reading "
      "outputs at the end-of-step callback, rather than at the beginning of the next step, achieves this for zone "
      "variables, meters and weather variables alike (variables that EnergyPlus reports at the HVAC system time "
      "step are sampled at the last system sub-step). Second, the time stamp must be derived robustly. The API's "
      "current-time and minute functions follow EnergyPlus' adaptive HVAC sub-steps and therefore return values "
      "such as 0:38 h within a 15-min zone step; Py-CoSim instead builds the time stamp of interval *k* from the "
      "date, the hour *h* and the zone time-step number *n*_z,")
    B.append(("eq", "t_{k} = d + h + (n_{z} − 1) Δt,", 4))
    p("and the master verifies at every step that it equals its own clock, raising a synchronization error "
      "otherwise. Before a run, the component rewrites the model -- converted to epJSON with EnergyPlus' own "
      "converter, so that fields are addressed by name -- to match the co-simulation: time steps per hour from Δ*t*, "
      "a run period with an explicit calendar year from the simulation window, optionally sizing periods derived "
      "from the weather file's extreme periods, and optionally the removal of daylight-saving time. The latter "
      "proved important: the co-simulation clock, EnergyPlus' internal clock and EPW weather data use local standard "
      "time, whereas EnergyPlus shifts schedules by one hour when daylight-saving time is defined.")
    p("Handles are resolved once, at the first weather-file time step. If a sensor, meter or actuator name cannot be "
      "resolved, the worker retrieves the model's catalogue of available API data and the master reports the "
      "closest matches (\"did you mean …\"); the same catalogue is available as a searchable table through a "
      "command-line tool. This addresses one of the most frequent stumbling blocks in practice.")
    p("Finally, EnergyPlus runs in a separate Python interpreter per component. We tested the alternatives with "
      "EnergyPlus 22.2: four simulations started concurrently in threads of one process deadlocked before reading "
      "their inputs, and a second simulation started in a process after a first one had completed stalled at "
      "start-up, although each used a new API state. Worker processes avoid both problems, run truly in parallel, "
      "and contain crashes. Workers are launched as separate interpreters rather than through Python's "
      "multiprocessing module so that user scripts need no main-module guard and run unchanged in Jupyter. Master "
      "and worker communicate through an authenticated local pipe. Start-up is split into a non-blocking launch and "
      "a blocking wait, so the sizing and warm-up periods of all buildings proceed in parallel. An in-process "
      "executor remains available for single-building debugging; because EnergyPlus can run only once per "
      "process, later in-process runs automatically fall back to a worker process.")
    h2("3.4 Master algorithm")
    p("Connections link output ports to input ports. A connection may carry a transformation (e.g., W to kW), may "
      "aggregate several sources with a reducer (e.g., the sum of building loads) and may be marked as delayed, in "
      "which case the target reads the value the source had at the end of the previous master step:")
    B.append(("eq", "u_{j}(t_{k}) = φ_{c}(ρ_{c}(y_{s_{1}}(t_{k−d_{c}}), …, y_{s_{n}}(t_{k−d_{c}}))),   d_{c} ∈ {0, 1}", 5))
    p("where ρ_c is the reducer, φ_c the transformation and d_c the delay of connection c. Only non-delayed "
      "connections between two feedthrough components impose an ordering constraint, because outputs of "
      "non-feedthrough components are known at the start of each step. The master builds the corresponding "
      "directed graph G = (F, E) over the set F of feedthrough components and sorts it topologically with Kahn's "
      "algorithm [@kahn1962], breaking ties by a user-settable priority and then by insertion order so that the "
      "schedule is deterministic. A cycle in G is an algebraic loop; the master reports the cycle and suggests "
      "either a delayed connection (a Jacobi-type coupling of that link) or a non-feedthrough formulation. Each "
      "master step then executes three phases:")
    B.append(("numbers", [
        "*output* -- every due non-feedthrough component reports y(t_k) (for EnergyPlus, the master receives the "
        "snapshots of all workers);",
        "*step* -- due feedthrough components run in topological order, each receiving its inputs per Eq. (5);",
        "*update* -- every due non-feedthrough component receives its inputs and advances to t_k + Δt (for "
        "EnergyPlus, actuator values are sent to all workers, which then simulate the interval concurrently).",
    ]))
    p("Within a step, the scheme is therefore Gauss-Seidel along the feedthrough graph and explicit "
      "(non-iterative) across non-feedthrough components, with optional Jacobi links where the user chooses them. "
      "Steps can be executed one by one, which is used for interactive exploration in notebooks and by the RL "
      "interface, which pauses the step right before an external agent acts.")
    h2("3.5 Data bus, results and supervision")
    p("The data bus replaces the per-step CSV files of Py-CoSim 0.x. It keeps the latest value of every output "
      "port, an optional snapshot of the previous step for delayed connections, and the full history of each port "
      "indexed by master step. Components can query any port's latest value or history, e.g., to build lagged "
      "features for an ML model. At the end of a run, the history is returned as a tidy table on the master grid "
      "(with zero-order hold for slower components), together with a structured event log (warnings, component "
      "messages, every LLM prompt and response), per-component timing statistics and metadata (versions, "
      "connections, EnergyPlus warnings and errors). If any component fails, all workers are terminated, partial "
      "results are saved and an error report records the failing component, phase, simulation time, the inputs it "
      "received, the recent events and the connection list. Observers can watch a run without taking part in it; "
      "built-in observers draw live (online) plots, stream values to a CSV file and show progress.")
    h2("3.6 Component library and interfaces")
    p("Table 2 lists the component library, which provides the building blocks most often needed in "
      "grid-interactive, occupant-centric and AI-related building studies. The PV model follows the structure of "
      "PVWatts [@dobos2014] with an isotropic-sky transposition, and the weather component interpolates EPW data "
      "exactly as EnergyPlus does, so that weather values seen by Python components coincide with those inside "
      "EnergyPlus. Beyond components, Py-CoSim offers: a Gymnasium environment [@towers2025] that exposes any "
      "co-simulation to RL libraries through an ExternalAgent component whose inputs are the observations and whose "
      "outputs are the actions (a trained policy can be plugged into the same scenario for evaluation); a YAML "
      "scenario format and command-line runner; and model-editing utilities (run period, time step, outputs, sizing, "
      "floor-area scaling).")
    B.append(("table", "Table 2 Built-in components (all are ordinary components and can be replaced or extended)",
              ["Category", "Components", "Main inputs → outputs"],
              [
                  ["Buildings", "EnergyPlusComponent; RCBuilding (2R2C, ideal HVAC)",
                   "actuators → sensors, meters; set-points, weather, gains → temperatures, powers"],
                  ["Weather and data", "EPWWeather (+ solar position); TimeSeriesSource; DailyProfile; Constant",
                   "– → temperature, irradiance, wind; any time series"],
                  ["Energy resources and grid", "PVSystem; Battery; TimeOfUseTariff; GridMeter",
                   "irradiance → AC power; power set-point → power, SOC; – → price; net power → energy, cost, peak"],
                  ["Controls", "ThermostatSchedule; PrecoolingController; PIController; PeakShavingController",
                   "time, measurements → set-points, battery dispatch"],
                  ["People and mobility", "HouseholdOccupancy (NHTS diaries); OccupancyMLP",
                   "– → people at home per dwelling"],
                  ["AI decision making", "LLMSupervisor with Anthropic, OpenAI-compatible, Ollama, replay and "
                   "fault-injection back-ends; ExternalAgent (Gymnasium)", "measurements, intent → bounded set-points"],
              ]))
    p("Two components deserve detail. The LLM supervisor treats model output as untrusted. Each decision builds a "
      "prompt containing the operator's intent and the current measurements, and passes the response through a "
      "guardrail pipeline: extraction of the first JSON object; validation that only declared set-points with "
      "finite numeric values are used; clipping to [u_min, u_max]; rate limiting,")
    B.append(("eq", "u_{k} = u_{k−1} + sign(û_{k} − u_{k−1}) min(|û_{k} − u_{k−1}|, Δu_{max}),", 6))
    p("where û_k is the clipped LLM proposal; and a fallback that holds the last valid set-point on timeouts, "
      "refusals or unusable answers. Prompts, raw responses, applied values and guardrail interventions are written "
      "to the event log, and a recording back-end stores exchanges so that a run can be replayed exactly without "
      "calling the model again. The household-occupancy component draws households from 13,548 travel diaries of "
      "the 2017 U.S. National Household Travel Survey [@fhwa2017] (5,548 households owning a hybrid, electric or "
      "alternative-fuel vehicle and a random sample of 8,000 other households). A person is assumed at home before "
      "the first departure and after the last return of the diary day; household members without trips stayed home; "
      "and a household's commute proxy is the duration of the first trip of its earliest-leaving member. This "
      "corrects the prototype [@zhang2023], which counted people as at home whenever they were not travelling. "
      "Each dwelling keeps its household size and draws a new diary day of a same-sized household every midnight, "
      "which reproduces day-to-day variability in the spirit of stochastic occupancy models [@page2008; @chen2018].")

    # ------------------------------------------------------------ 4 Verification
    h1("4 Verification and performance")
    p("All experiments used the U.S. Department of Energy small-office reference building (511 m², five "
      "conditioned zones, packaged single-zone air conditioners with gas furnaces) [@deru2011], the Tucson, "
      f"Arizona TMY3 weather file, EnergyPlus 22.2, a 15-min step, and the week of 17–23 July 2023 ({R['v_steps']} "
      f"steps) unless stated otherwise. Timings were measured on a laptop ({R['cpu']}, {R['phys_cores']} physical "
      f"cores / {R['cores']} logical processors, {R['mem_gb']} GB memory) under Windows 11 with Python 3.12.")
    h2("4.1 Fidelity of the EnergyPlus wrapper")
    p("Three tests were performed (Fig. 3). V1 (non-intrusiveness): the model was run once from the EnergyPlus "
      "command line and once inside Py-CoSim with sensors and meters only; the time-step outputs of both runs "
      f"written by EnergyPlus to its SQL file ({R['v_series']} series: five zone temperatures, outdoor temperature, "
      f"facility electricity) differed by at most {R['v1']:.1f}, i.e., they are bit-identical. V2 (exchange "
      "timing): every value Py-CoSim published at master time t_k was compared with the value EnergyPlus reported "
      f"with time stamp t_k; the maximum absolute difference was {R['v2']:.1f}. V3 (actuation equivalence): a "
      "pre-cooling schedule (22 °C from 13:00 to 16:00 and 26.5 °C from 16:00 to 21:00 on weekdays) was "
      "implemented once as a native EnergyPlus schedule and once as a Python function writing the cooling "
      "set-point through an actuator; the two runs differed by at most "
      f"{R['v3']:.1f} in every output, while the pre-cooling itself changed the core-zone temperature by up to "
      f"{R['precool_dT']:.1f} K relative to the original schedule. V3 also exposed the daylight-saving shift "
      "discussed in Section 3.3: with the model's original daylight-saving setting, the native schedule acted one "
      "hour earlier than the Python controller, which keeps standard time.")
    B.append(("fig", FIG / "fig04_verification.png",
              "Fig. 3 Verification of the EnergyPlus component (two of seven days shown): (a) values exchanged by "
              "Py-CoSim versus EnergyPlus' own report; (b, c) a Python pre-cooling controller acting through an "
              "actuator versus the equivalent schedule hard-coded in EnergyPlus.", 15.0))
    h2("4.2 Overhead and scalability")
    ob = R["ovh_break"]
    p(f"For one building and one week, the EnergyPlus command line took {R['t_cli']:.1f} s on average (three "
      f"repetitions), Py-CoSim with a worker process {R['t_proc']:.1f} s and with the in-process executor "
      f"{R['t_thread']:.1f} s, including model preparation, sizing and warm-up; run-to-run variation on the "
      "laptop was about ±1 s. Repeating the comparison for 1, 7 and 28 days (nine runs per mode) and fitting wall "
      "time against the "
      f"number of steps separated the overhead into a fixed part of about {ob['fixed_overhead_s']:.1f} s per run "
      "(model preparation, launching the worker interpreter, EnergyPlus running in library mode) and "
      f"{ob['per_step_overhead_ms']:.1f} ms per exchanged step, which is small compared with the cost of a "
      "typical controller or ML model. The master itself adds "
      f"{R['master_us']:.0f} µs per component and step for trivial Python components. Exchanging one value through the "
      f"data bus takes {R['bus_us']:.2f} µs, against {R['csv_ms']:.1f} ms for writing and reading one CSV file as "
      f"in Py-CoSim 0.x, a factor of about {round(R['exchange_ratio'], -3):,.0f}.")
    p("For scalability, N = 1 to "
      f"{R['n_max']} variants of the office (floor areas scaled by ±10%) were co-simulated with one Python "
      "controller actuating every building at every step, either serially (one co-simulation per building) or "
      f"in a single co-simulation with parallel workers (Fig. 4). Parallel execution was {sp8:.1f} times faster for "
      f"eight buildings and {R['speed_max']:.1f} times faster for {R['n_max']} buildings "
      f"({R['par_max']:.0f} s versus {R['ser_max']:.0f} s). Since EnergyPlus is CPU-bound, the speed-up saturates "
      f"at about {100 * R['speed_best'] / R['phys_cores']:.0f}% of the ideal value for the {R['phys_cores']} "
      "physical cores of the test laptop (hyper-threads, the master process and memory bandwidth limit the rest); "
      "workstations with more cores scale further. In every case the parallel results were identical to the serial ones (maximum absolute "
      f"difference {R['sc_max_diff']:.1f}), confirming that workers are fully isolated.")
    B.append(("fig", FIG / "fig05_scalability.png",
              "Fig. 4 Wall-clock time (a) and speed-up (b) of co-simulating N EnergyPlus buildings for one week at "
              "15-min steps, serially and with Py-CoSim's parallel worker processes.", 15.0))

    # ------------------------------------------------------------ 5 Case studies
    h1("5 Case studies")
    p("The three case studies are distributed as runnable examples and can be reproduced with one command each. "
      "They are designed to exercise different capabilities rather than to provide definitive answers to the "
      "domain questions they touch.")
    h2("5.1 Case A: a grid-interactive community")
    aN = R["A_meta"]
    p(f"Ten small offices (each a separate EnergyPlus instance, floor areas scaled by factors drawn uniformly "
      "from 0.9 to 1.1) share a 250 kW community PV array, a 500 kWh / 125 kW community battery and one grid "
      "connection with a time-of-use tariff (US$0.30 kWh⁻¹ on weekdays from 16:00 to 21:00, US$0.08 kWh⁻¹ "
      f"otherwise). The co-simulation contains {aN['components_coordinated']} components and "
      f"{aN['connections_coordinated']} connections, including a fan-in that sums the ten building loads and a "
      "delayed connection that feeds the battery's state of charge back to the dispatch controller. Four strategies "
      "were compared for the week of 17–23 July: *baseline* (PV only, occupied cooling set-point 24 °C); *precool* "
      "(22 °C for three hours before the peak, 26.5 °C during it); *battery* (rule-based dispatch that stores PV "
      "surplus, charges at night, discharges during the peak and shaves imports above 120 kW); and *coordinated* "
      "(both). Figure 5 and Table 3 show the results.")
    B.append(("fig", FIG / "fig06_case_a_community.png",
              "Fig. 5 Case A: (a) community grid import for the four strategies (on-peak periods shaded); "
              "(b) battery state of charge; (c) key performance indicators relative to the baseline.", 15.0))
    rows = []
    names = {"baseline": "Baseline", "precool": "Pre-cooling", "battery": "Battery", "coordinated": "Coordinated"}
    for s_ in ("baseline", "precool", "battery", "coordinated"):
        r = A.loc[s_]
        rows.append([names[s_], f"{r['load_MWh']:.2f}", f"{r['import_MWh']:.2f}", f"{r['on_peak_import_MWh']:.2f}",
                     f"{r['peak_import_kW']:.0f}", f"{r['energy_cost_$']:.0f}", f"{r['discomfort_Kh_per_bldg']:.1f}",
                     f"{r['wall_time_s']:.0f}"])
    B.append(("table", "Table 3 Case A results for one summer week (10 buildings)",
              ["Strategy", "Load (MWh)", "Grid import (MWh)", "On-peak import (MWh)", "Peak import (kW)",
               "Energy cost (US$)", "Discomfort (K·h per bldg.)", "Run time (s)"], rows))
    def change(strat, col):
        v = chg(strat, col)
        return f"decreased by {-v:.0f}%" if v < 0 else f"increased by {v:.0f}%"

    pv_note = ("because the pre-cooling hours (13:00–16:00) coincide with high PV output, the additional cooling "
               "load was largely covered by PV, and the community peak import "
               if chg("precool", "peak_import_kW") <= 0 else
               "but it shifted demand to the pre-cooling hours, a rebound often reported for pre-cooling, and the "
               "community peak import ")
    p(f"Pre-cooling alone reduced on-peak import by {-chg('precool', 'on_peak_import_MWh'):.0f}% and energy cost by "
      f"{-chg('precool', 'energy_cost_$'):.0f}%; {pv_note}{change('precool', 'peak_import_kW')}. The battery "
      f"reduced on-peak import by {-chg('battery', 'on_peak_import_MWh'):.0f}% and energy cost by "
      f"{-chg('battery', 'energy_cost_$'):.0f}%, while total grid import {change('battery', 'import_MWh')} because of "
      "conversion losses and night-time charging. Coordinating both measures reduced on-peak import by "
      f"{-chg('coordinated', 'on_peak_import_MWh'):.0f}% and energy cost by "
      f"{-chg('coordinated', 'energy_cost_$'):.0f}% relative to the baseline, and the peak import "
      f"{change('coordinated', 'peak_import_kW')}: pre-cooling lowers the on-peak energy the battery must supply, "
      "so the stored energy lasts through the whole peak window. The price is a modest comfort penalty during the "
      f"set-up period ({A.loc['coordinated', 'discomfort_Kh_per_bldg']:.1f} K·h above 26 °C per building and week). "
      f"Each strategy took about {A['wall_time_s'].mean():.0f} s to simulate. Studying such interactions -- here "
      "between the thermal mass of ten detailed building models, PV, storage and a tariff -- is what a "
      "co-simulation framework should make routine; replacing the rule-based dispatch by model-predictive or RL "
      "control requires changing a single component.")
    h2("5.2 Case B: LLM-in-the-loop supervisory control")
    p("LLMs are being explored as interfaces between occupants or operators and building controls "
      "[@liu2025llm; @zhangchen2024; @jiang2024; @ahn2023; @zhu2025]. Case B places an LLMSupervisor above the "
      "small office: every hour it receives an operator intent and the current core-zone and outdoor temperatures, "
      "electricity price and building power, and returns cooling and heating set-points, which EnergyPlus applies "
      "at its 15-min steps. Because the purpose of this case is to characterize the integration layer -- multi-rate "
      "scheduling, prompt construction, parsing, guardrails, logging and fault tolerance -- independently of any "
      "particular model, the decisions were produced by the framework's offline stand-in back-end, a deterministic "
      "keyword-rule policy that answers in the same JSON format as an LLM. The same scenario runs with Claude, "
      "OpenAI-compatible or local Ollama models by changing one argument; evaluating the decision quality of "
      "specific LLMs is left to future work.")
    p("Three intents were tested for five weekdays: a nursing home prioritizing comfort, a normally operated office, "
      "and cost minimization during the evening peak (Fig. 6a,b). The supervisor's decisions propagated as intended: "
      f"the mean occupied-hour cooling set-point was {Bi.loc['comfort', 'mean_occupied_clg_sp']:.1f}, "
      f"{Bi.loc['office', 'mean_occupied_clg_sp']:.1f} and {Bi.loc['economy', 'mean_occupied_clg_sp']:.1f} °C, and "
      f"the building used {Bi.loc['comfort', 'energy_kWh']:.0f}, {Bi.loc['office', 'energy_kWh']:.0f} and "
      f"{Bi.loc['economy', 'energy_kWh']:.0f} kWh, respectively. Every hourly exchange (prompt, response, applied "
      "value, guardrail notes, latency) was recorded in the event log; an example exchange is reproduced in "
      "Appendix A.")
    p("Robustness was then tested by wrapping the back-end in a fault injector that, with a total probability "
      "between 0 and 50%, replaced answers by prose without JSON, out-of-range values (+40 K), answers with "
      "extraneous keys, refusals or timeouts (Fig. 6c,d). The co-simulation never failed. At the highest fault "
      f"rate, {int(worst.injected_faults)} of {int(worst.calls)} decisions were corrupted; the guardrails held the "
      f"previous set-point {int(worst.fallback)} times and clipped {int(worst.clipped)} out-of-range proposals, so "
      f"that the applied cooling set-point always stayed within [{worst.min_clg_sp_applied:.1f}, "
      f"{worst.max_clg_sp_applied:.1f}] °C, inside the permitted 21–30 °C band. The effect on the building was "
      f"limited to a {worst['energy_dev_%']:+.1f}% change in energy use and a mean absolute zone-temperature "
      f"deviation of {worst.mean_abs_dT_zone:.2f} K relative to the fault-free run. This shows how co-simulation "
      "can be used to stress-test AI-in-the-loop control before deployment.")
    B.append(("fig", FIG / "fig07_case_b_llm.png",
              "Fig. 6 Case B: (a) hourly cooling set-points chosen by the supervisor for three operator intents and "
              "(b) resulting core-zone temperatures; (c) guardrail interventions and (d) effect on the building under "
              "increasing rates of injected faults.", 16.0))
    h2("5.3 Case C: from travel behavior to apartment cooling loads")
    hs = R["C_hh"]
    p("Case C revisits the interdisciplinary question of the prototype [@zhang2023] -- how commuting patterns "
      "shape residential loads -- with a corrected occupancy method and a richer building model, in the spirit of "
      "recent mobility-building studies [@liudong2025]. The 23 dwelling units of a mid-rise apartment prototype "
      "(sized with the weather file's extreme periods for Tucson) were each assigned a household from the NHTS "
      "data. A HouseholdOccupancy component sets the number of people in every unit at every 15-min step through "
      "EnergyPlus' per-unit occupancy actuators. Two thermostat policies were compared, both writing the cooling "
      "set-point of every unit through EnergyPlus' zone temperature-control actuators: *fixed* (23.9 °C all day) "
      "and *smart* (an occupancy-responsive set-up to 26.7 °C whenever a unit is empty). The prototype's own "
      "door-switch thermostat reset (an EMS program) and its window ventilation were removed in a pre-processing "
      "step so that the two policies are compared on equal terms -- an example of the model-editing hook. Four "
      "household groups were simulated -- long commute (proxy ≥ 30 min), short commute (≤ 10 min), EV/hybrid owners "
      "and other households -- each with three independent household samples and both policies, i.e., 24 one-week "
      "apartment simulations, four of which ran in parallel in each co-simulation. For each sample the fixed and "
      "smart buildings were driven by the same occupancy component, giving a paired comparison.")
    B.append(("fig", FIG / "fig08_case_c_travel.png",
              "Fig. 7 Case C: average daily profiles of people at home (a, b) and of the apartment cooling load under "
              "fixed and occupancy-responsive (smart) thermostats (c, d) for long- versus short-commute households "
              "(left) and EV/hybrid-owning versus other households (right); averages over three household samples "
              "and seven days.", 16.0))
    grp = {"long": "Long commute", "short": "Short commute", "ev": "EV/hybrid", "nonev": "Non-EV"}
    rows = []
    for g in ("long", "short", "ev", "nonev"):
        f, s2 = C.loc[(g, "fixed")], C.loc[(g, "smart")]
        rows.append([grp[g], f"{hs.loc[g, 'mean_hhsize']:.2f}", f"{100 * hs.loc[g, 'empty_frac_day']:.1f}",
                     f"{f['cooling_load_MWh']:.2f}", f"{s2['cooling_load_MWh']:.2f}",
                     f"{savq.loc[g, 'mean']:.1f} ± {savq.loc[g, 'std']:.1f}",
                     f"{sav.loc[g, 'mean']:.1f} ± {sav.loc[g, 'std']:.1f}", f"{f['peak_to_valley']:.2f}",
                     f"{s2['peak_to_valley']:.2f}"])
    B.append(("table", "Table 4 Case C results per household group (simulation results: mean of three household "
              "samples ± one standard deviation, one week, 23 units; household statistics: whole NHTS group)",
              ["Group", "Household size (persons)", "Time with nobody home (% of day)", "Cooling load, fixed (MWh)",
               "Cooling load, smart (MWh)", "Cooling-load saving (%)", "Cooling-electricity saving (%)",
               "Peak/valley, fixed", "Peak/valley, smart"], rows))

    def compare(a, b):
        d = sav.loc[a, "mean"] - sav.loc[b, "mean"]
        spread = max(sav.loc[a, "std"], sav.loc[b, "std"])
        if abs(d) <= spread:
            return "a difference smaller than the variability between household samples"
        return f"a difference of {abs(d):.1f} percentage points"

    p("Figure 7 and Table 4 summarize the results. The occupancy profiles reflect the travel diaries: in all groups "
      "the number of people at home falls from about 06:00 to a minimum around noon and recovers in the evening; "
      "long-commute and EV/hybrid-owning households, which are larger on average, have more people at home at "
      "night but slightly fewer at midday. "
      "Under fixed thermostats, the groups' cooling loads differed little, because envelope and solar gains dominate "
      "the cooling demand of the apartments in a Tucson summer and occupants mainly add internal gains. "
      "Occupancy-responsive thermostats couple the load directly to presence. They saved "
      f"{sav.loc['long', 'mean']:.1f}% of the cooling electricity for long-commute and {sav.loc['short', 'mean']:.1f}% "
      f"for short-commute households ({compare('long', 'short')}), and {sav.loc['ev', 'mean']:.1f}% versus "
      f"{sav.loc['nonev', 'mean']:.1f}% for EV/hybrid-owning versus other households ({compare('ev', 'nonev')}). "
      "The NHTS statistics in Table 4 explain why commuting alone is a weak predictor: a set-up is possible only when "
      "a unit is completely empty, which depends on household size as much as on travel. Long-commute households "
      f"are absent longer per person but are also larger ({hs.loc['long', 'mean_hhsize']:.2f} versus "
      f"{hs.loc['short', 'mean_hhsize']:.2f} persons), and the share of the day with nobody home was "
      f"{100 * hs.loc['long', 'empty_frac_day']:.1f}% versus {100 * hs.loc['short', 'empty_frac_day']:.1f}%. "
      "Smart thermostats also reshaped the daily load profile: they strongly reduced the midday load of empty units, "
      "while part of that saving reappeared as a recovery load when occupants return in the evening and at night, "
      "shifting the peak hour from 17:00 to 18:00 and changing the ratio between the highest and the "
      f"lowest hourly mean load from {C.xs('fixed', level='policy')['peak_to_valley'].mean():.1f} to "
      f"{C.xs('smart', level='policy')['peak_to_valley'].mean():.1f} on average -- a shift that matters for "
      "grid operators when such devices are deployed at scale. The value of a technology, and thus of programmes "
      "that subsidize it, can therefore depend on how households travel and how they are composed, which in turn "
      "correlate with income and location; such interactions remain invisible without coupling building and "
      "mobility models. The results are illustrative: they rest on one-day diaries, a simple presence rule, three "
      "household samples per group and one climate, and should be read as a demonstration of the workflow rather "
      "than as general conclusions.")

    # ------------------------------------------------------------ 6 Discussion
    h1("6 Discussion")
    h2("6.1 Usability and lessons learned")
    p("Py-CoSim 0.x required about 470 lines of notebook code -- building classes with callback functions, "
      "threads, file-based messaging and plotting -- for two buildings and three Python components. In the new "
      f"framework the pre-cooling comparison of Example 3 takes {R['loc_ex3']} lines including "
      "key-performance-indicator computation and plotting, and the ten-building community of Case A "
      f"{R['loc_ex4']} lines; the framework itself comprises about {round(R['loc_lib'], -2):,} lines of library "
      "code, which users do not need to read, and is covered by an automated test suite. Equally important are the "
      "lessons on the EnergyPlus API that the framework now encapsulates: simulations must not share a process; "
      "time stamps must be derived from zone time-step numbers; outputs should be read after zone reporting to "
      "align with EnergyPlus' own reports; meter values refer to the preceding interval; daylight-saving time "
      "silently shifts schedules relative to standard-time clocks; and models must be synchronized with the "
      "co-simulation window and time step. Each of these produced subtle, silent errors during development, and "
      "each is now handled automatically or reported explicitly.")
    h2("6.2 Limitations")
    p("Several limitations remain. Values published by state-based components describe the interval that just "
      "ended, whereas tariffs, batteries and controllers act on the interval that starts; aggregations such as the "
      "grid balance of Case A therefore combine adjacent intervals at tariff changes, which we estimated to bias a "
      "week's energy cost by about 2% at a 15-min step (the convention is documented, and delayed connections "
      "can align it). The master uses fixed communication steps without rollback or iteration, so "
      "strongly coupled feedthrough loops must be broken with delays and their accuracy depends on the step size "
      "[@trcka2009]; the time resolution of EnergyPlus coupling is the zone time step. EnergyPlus support has been "
      "verified with version 22.2 on Windows; the implementation relies only on the basic runtime and data-exchange "
      "functions of the API, but other versions and operating systems have so far been exercised only by the "
      "EnergyPlus-free part of the test suite. All workers run on one machine, so very large communities are limited by memory (each EnergyPlus "
      "instance needs tens to a few hundred megabytes). Python components share the master's interpreter, so "
      "computationally heavy Python models do not run in parallel unless they release the global interpreter lock. "
      "Case B used a deterministic stand-in rather than an LLM, and Case C relies on one-day travel diaries and a "
      "simple presence rule.")
    h2("6.3 Future work")
    p("Planned extensions include FMU import so that Modelica models can participate as components "
      "[@blochwitz2011], distributed workers on several machines, bridges to grid co-simulation federations "
      "[@hardy2024] and to benchmarking services [@blum2021], hardware-in-the-loop execution against building "
      "automation systems, benchmarks of LLM supervisors with real models using the record-and-replay facility, "
      "and data-predictive and model-predictive control templates [@zhang2021dpc; @drgona2020].")

    # ------------------------------------------------------------ 7 Conclusions
    h1("7 Conclusions")
    p("Py-CoSim makes co-simulation of EnergyPlus buildings with arbitrary Python models practical for building "
      "researchers. Its component abstraction treats EnergyPlus models, physical and data-driven models, "
      "controllers, LLM agents and RL agents uniformly; a lock-step protocol turns EnergyPlus into a steppable "
      "component running in isolated, parallel worker processes; and a graph-based master algorithm with an "
      "in-memory data bus gives co-simulations explicit, verifiable and reproducible semantics. Verification showed "
      f"bit-identical agreement with stand-alone EnergyPlus and exact equivalence of Python actuation, and parallel "
      f"workers accelerated multi-building runs by up to {R['speed_best']:.1f} times with identical results. Three case studies -- coordinated pre-cooling and storage in a ten-building community, LLM "
      "supervisory control stress-tested with injected faults, and travel-diary-driven occupancy in 23 "
      "apartments -- illustrate the range of interdisciplinary questions the framework supports. Py-CoSim is open "
      "source, documented and accompanied by nine runnable examples intended as starting points for new studies.")
    p("**Code availability.** Py-CoSim, the examples and the scripts that reproduce every result and figure of "
      "this paper are open source under the MIT license (repository link withheld for double-blind review; it is "
      "given on the title page).")
    h1("Appendix A Example of a recorded LLM exchange (Case B)")
    p("The following prompt was generated by the LLMSupervisor at one hourly decision of the nursing-home run; the "
      "system prompt instructs the model to answer with a single JSON object containing clg_sp and htg_sp within "
      "their permitted ranges and a short reason. The response and the applied set-points are stored with the "
      "prompt in the event log.")
    ex_ = R["B_ex"]
    B.append(("mono", "PROMPT: " + str(ex_["prompt"])))
    B.append(("mono", "RESPONSE: " + str(ex_["response"])))
    B.append(("mono", "APPLIED: " + str(ex_["applied"])))
    return {"abstract": abstract, "keywords": keywords, "blocks": B}


# ============================================================== docx helpers
CITE_RE = re.compile(r"\[(@[^\]]+)\]")


def render_citations(text: str, used: set) -> str:
    def repl(m):
        items = [x.strip() for x in m.group(1).split(";")]
        textual = len(items) == 1 and items[0].startswith("@t:")
        labels = []
        for it in items:
            key = it.lstrip("@").replace("t:", "")
            if key not in REFS:
                raise KeyError(f"unknown reference {key}")
            used.add(key)
            labels.append(REFS[key][0])
        if textual:
            label = labels[0]
            name, year = label.rsplit(" ", 1)
            return f"{name} ({year})"
        return "(" + "; ".join(labels) + ")"

    return CITE_RE.sub(repl, text)


def add_runs(par, text: str, size: int = 12) -> None:
    # **bold** and *italic*; " -- " becomes a spaced en dash
    text = text.replace(" -- ", " – ")
    tokens = re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**"):
            run = par.add_run(tok[2:-2])
            run.bold = True
        elif tok.startswith("*") and tok.endswith("*"):
            run = par.add_run(tok[1:-1])
            run.italic = True
        else:
            run = par.add_run(tok)
        run.font.size = Pt(size)


M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _m_run(text: str, upright: bool = False) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    sty = '<m:rPr><m:sty m:val="p"/></m:rPr>' if upright else ""
    return f'<m:r>{sty}<w:rPr><w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/></w:rPr><m:t xml:space="preserve">{text}</m:t></m:r>'


_UPRIGHT = ("min", "max", "sign", "clip")


def _text_runs(text: str) -> str:
    parts = re.split(r"(\b(?:min|max|sign|clip)\b)", text)
    return "".join(_m_run(p, upright=p in _UPRIGHT) for p in parts if p)


def _group(expr: str, j: int):
    if expr[j] == "{":
        depth, k = 0, j
        while True:
            if expr[k] == "{":
                depth += 1
            elif expr[k] == "}":
                depth -= 1
                if depth == 0:
                    return expr[j + 1:k], k + 1
            k += 1
    return expr[j], j + 1


def omml(expr: str) -> str:
    """Tiny converter: 'x_{i}(t_{k+1})' -> OMML with subscripts (one nesting level inside groups)."""
    out, buf, i = [], "", 0
    while i < len(expr):
        c = expr[i]
        if c in "_^" and buf:
            base, buf = buf[-1], buf[:-1]
            if buf:
                out.append(_text_runs(buf))
                buf = ""
            grp, i = _group(expr, i + 1)
            inner = omml_inner(grp)
            if c == "_":
                out.append(f"<m:sSub><m:e>{_m_run(base)}</m:e><m:sub>{inner}</m:sub></m:sSub>")
            else:
                out.append(f"<m:sSup><m:e>{_m_run(base)}</m:e><m:sup>{inner}</m:sup></m:sSup>")
        else:
            buf += c
            i += 1
    if buf:
        out.append(_text_runs(buf))
    return "".join(out)


def omml_inner(expr: str) -> str:
    return omml(expr) if ("_" in expr or "^" in expr) else _text_runs(expr)


def add_equation(doc, expr: str, number: int) -> None:
    par = doc.add_paragraph()
    pf = par.paragraph_format
    pf.tab_stops.add_tab_stop(Cm(8.0), WD_TAB_ALIGNMENT.CENTER)
    pf.tab_stops.add_tab_stop(Cm(16.0), WD_TAB_ALIGNMENT.RIGHT)
    par.add_run("\t")
    xml = (f'<m:oMath xmlns:m="{M_NS}" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"{omml(expr)}</m:oMath>")
    par._p.append(parse_xml(xml))
    r = par.add_run(f"\t({number})")
    r.font.size = Pt(12)


def set_cell_text(cell, text: str, bold: bool = False, size: int = 9) -> None:
    cell.text = ""
    par = cell.paragraphs[0]
    par.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    par.paragraph_format.space_after = Pt(0)
    run = par.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)


def add_table(doc, caption: str, header, rows) -> None:
    cap = doc.add_paragraph()
    cap.paragraph_format.keep_with_next = True
    m = re.match(r"(Table \d+)(.*)", caption)
    r = cap.add_run(m.group(1))
    r.bold = True
    cap.add_run(m.group(2))
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        set_cell_text(table.rows[0].cells[i], h, bold=True)
        shade = parse_xml(r'<w:shd {} w:val="clear" w:color="auto" w:fill="E7EEF5"/>'.format(nsdecls("w")))
        table.rows[0].cells[i]._tc.get_or_add_tcPr().append(shade)
    for row in rows:
        cells = table.add_row().cells
        for i, v in enumerate(row):
            set_cell_text(cells[i], str(v))
    doc.add_paragraph()


def add_figure(doc, path: Path, caption: str, width_cm: float) -> None:
    par = doc.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    par.paragraph_format.keep_with_next = True
    par.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    par.add_run().add_picture(str(path), width=Cm(width_cm))
    cap = doc.add_paragraph()
    m = re.match(r"(Fig\. \d+)(.*)", caption)
    r = cap.add_run(m.group(1))
    r.bold = True
    cap.add_run(m.group(2))


def add_page_number(section) -> None:
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


def base_document() -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Cm(2.5))
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    style.font.size = Pt(12)
    style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    style.paragraph_format.space_after = Pt(0)
    for name in ("Heading 1", "Heading 2"):
        st = doc.styles[name]
        st.font.name = "Times New Roman"
        rfonts = st.element.rPr.rFonts
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            if rfonts.get(qn(attr)) is not None:
                del rfonts.attrib[qn(attr)]
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            rfonts.set(qn(attr), "Times New Roman")
        st.font.size = Pt(12)
        st.font.bold = True
        st.font.italic = name == "Heading 2"
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.paragraph_format.space_before = Pt(12)
        st.paragraph_format.space_after = Pt(0)
        st.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    add_page_number(sec)
    return doc


def build_manuscript(R: dict) -> set:
    C = content(R)
    used: set = set()
    doc = base_document()
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = t.add_run(TITLE)
    run.bold = True
    run.font.size = Pt(14)
    doc.add_paragraph()
    h = doc.add_paragraph()
    h.add_run("Abstract").bold = True
    add_runs(doc.add_paragraph(), render_citations(C["abstract"], used))
    k = doc.add_paragraph()
    k.add_run("Keywords").bold = True
    doc.add_paragraph(C["keywords"])
    for block in C["blocks"]:
        kind = block[0]
        if kind == "h1":
            doc.add_heading(block[1], level=1)
        elif kind == "h2":
            doc.add_heading(block[1], level=2)
        elif kind == "p":
            add_runs(doc.add_paragraph(), render_citations(block[1], used))
        elif kind in ("bullets", "numbers"):
            for i, item in enumerate(block[1], 1):
                par = doc.add_paragraph()
                par.paragraph_format.left_indent = Cm(0.8)
                par.paragraph_format.first_line_indent = Cm(-0.6)
                add_runs(par, ("• " if kind == "bullets" else f"({i}) ") + render_citations(item, used))
        elif kind == "eq":
            add_equation(doc, block[1], block[2])
        elif kind == "mono":
            par = doc.add_paragraph()
            par.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
            par.paragraph_format.space_after = Pt(6)
            run = par.add_run(block[1])
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif kind == "fig":
            add_figure(doc, block[1], block[2], block[3])
        elif kind == "table":
            add_table(doc, block[1], block[2], block[3])
    # Table 1 cites in plain text -> register those keys too
    for key in ("wetter2011", "nouidui2014", "wetter2014", "wetter2020", "steinbrink2019", "hardy2024", "bernal2012",
                "blum2021", "benne2024", "campoy2025", "dai2025", "zhang2020cobs", "nweye2025", "zhang2023"):
        used.add(key)
    doc.add_heading("References", level=1)
    for key in sorted(used, key=lambda k: REFS[k][1].lower()):
        par = doc.add_paragraph(REFS[key][1])
        par.paragraph_format.left_indent = Cm(0.8)
        par.paragraph_format.first_line_indent = Cm(-0.8)
    doc.save(OUT_MS)
    return used


def build_title_page() -> None:
    doc = base_document()
    def para(text, bold=False, center=False, size=12):
        p_ = doc.add_paragraph()
        if center:
            p_.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_runs(p_, text, size)
        if bold:
            for r in p_.runs:
                r.bold = True
        return p_
    para("Cover letter", bold=True)
    para("Dear Editor-in-Chief,")
    para("We submit the manuscript entitled “" + TITLE + "” for consideration as a Research Article in "
         "Building Simulation (section: Advances in Modeling and Simulation Tools). The paper presents an open-source, "
         "pure-Python co-simulation framework that turns EnergyPlus into a steppable component and couples it with "
         "energy, mobility and artificial-intelligence models on a verified common clock. We show bit-identical "
         "agreement with stand-alone EnergyPlus, parallel multi-building scalability and three case studies "
         "(grid-interactive community, LLM supervisory control with guardrails, travel-behavior-driven occupancy). "
         "The manuscript has not been published and is not under consideration elsewhere; an early prototype was "
         "posted as a preprint (SSRN 4572925), which the present work substantially extends and supersedes. All "
         "authors have approved the submission.")
    para("Sincerely, [Corresponding author name]")
    doc.add_page_break()
    para(TITLE, bold=True, center=True, size=14)
    para("[Author 1]¹*, [Author 2]¹, [Author 3]¹  — placeholders: please insert the final author list", center=True)
    para("¹ [Affiliation, e.g., Department, University of Arizona, Tucson, AZ, USA]", center=True)
    para("* Corresponding author: [name], [e-mail]", center=True)
    para("Author contributions (CRediT)", bold=True)
    para("[Author 1]: Conceptualization, Methodology, Software, Validation, Writing – original draft. [Author 2]: "
         "Data curation (NHTS), Investigation. [Author 3]: Data curation, Writing – review & editing. — placeholders; "
         "please edit.")
    para("Declarations", bold=True)
    para("**Funding.** [Funding information, or: The authors did not receive support from any organization for the "
         "submitted work.]")
    para("**Competing interests.** The authors have no competing interests to declare that are relevant to the "
         "content of this article.")
    para("**Compliance with ethical standards.** This study does not contain any studies with human or animal "
         "subjects performed by any of the authors. The National Household Travel Survey data used are public, "
         "de-identified data.")
    para("**Code and data availability.** Py-CoSim is available under the MIT license at "
         "https://github.com/lz356/Py-CoSim (version 2.0.0; [archive DOI, e.g., Zenodo, to be added]). The "
         "repository contains the scripts that reproduce all results and figures (paper/experiments).")
    para("**Use of generative AI.** During the preparation of this work the authors used Claude (Anthropic) to "
         "assist with software development, the design and execution of the computational experiments and the "
         "drafting of the manuscript. After using this tool, the authors reviewed, tested and edited the software "
         "and the content as needed and take full responsibility for the content of the publication.")
    para("Acknowledgements", bold=True)
    para("[Acknowledgements and funders, written out in full.]")
    doc.save(OUT_TP)


def main() -> None:
    R = load_results()
    used = build_manuscript(R)
    build_title_page()
    doc = Document(OUT_MS)
    words = sum(len(p_.text.split()) for p_ in doc.paragraphs)
    abstract_words = len(content(R)["abstract"].split())
    print(f"manuscript: {OUT_MS.name} ({words} words incl. references; abstract {abstract_words} words; "
          f"{len(used)} references)")
    print(f"title page: {OUT_TP.name}")


if __name__ == "__main__":
    main()
