---
title: Exchanging messages instead of wiring software: time-aware co-simulation of building energy models with data-driven and AI components
author: Author One | 1 | corresponding
author: Author Two | 1
author: Author Three | 2
affiliation: 1 | Department, University, Street address, City, State Postal code, Country
affiliation: 2 | Department, Institution, Street address, City, Postal code, Country
email: corresponding.author@university.edu
keywords: co-simulation; EnergyPlus; time semantics; large language models; reproducibility; grid-interactive buildings
bibliography: references.ris
endnote-style: Numbered
---

# Abstract {-}

@@ABSTRACT@@

# Introduction {#sec:intro}

Buildings are no longer simulated alone. Studies of grid-interactive efficient buildings couple a building energy model with photovoltaic (PV) generation, batteries, electric vehicles, tariffs and grid signals [@neukomm2019; @hong2025]. Occupant models, machine-learning (ML) predictors and, increasingly, large language models (LLMs) take part in the control loop [@yan2015; @wang2020; @ahn2023; @zhangchen2024; @liu2025llm]. No single program models all of this. Co-simulation, the coupled execution of separate models that exchange data at run time, is therefore the standard way to study such systems [@gomes2018; @hong2018].

Most co-simulation infrastructure is organized around connecting software. The Functional Mock-up Interface (FMI) packages a model behind a standard programming interface [@blochwitz2011; @junghanns2021]. The Building Controls Virtual Test Bed (BCVTB) and EnergyPlus's External Interface link EnergyPlus to other programs through sockets [@wetter2011; @nouidui2014]. Spawn of EnergyPlus couples Modelica with EnergyPlus [@wetter2020]. Gym-style environments such as Sinergym, COBS and BOPTEST wrap a building model for reinforcement learning [@jimenez2021; @zhang2020cobs; @blum2021]. Message-based frameworks such as HELICS and mosaik let simulators publish and subscribe to values through a broker or scheduler [@palmintier2017; @hardy2024; @steinbrink2019; @ofenloch2022]. In all of these tools, an exchanged value is a number stamped with a time instant. What that number means in time is left to the person who wires the models together.

This matters more than it seems, for two reasons. The first is time meaning. EnergyPlus reports a zone temperature or an electric power for a time step as an average over the step that has just ended, and an energy as a sum over it [@doe2024rdd]. A tariff or a set-point, by contrast, is valid from its time stamp until it is replaced. A battery command applies to the step that starts. When components with different step sizes exchange such values as bare numbers, the receiving component must know which interval each value belongs to and convert it. If it does not, energy is attributed to the wrong price interval, slow consumers sample fast signals, and fast consumers re-use stale values. These are the classical alignment and extrapolation errors of explicit (non-iterative) coupling [@kubler2000; @gomes2018], and they are usually fixed by hand for each connection [@tripakis2015; @gomes2019semantic].

The second reason is that AI components behave differently from physics models. A physics model is deterministic, fast, always returns a valid number, and needs only its current inputs. A hosted LLM may answer the same prompt differently on each call, even at temperature zero [@ouyang2025]. It may time out, refuse, or return text that is not a valid control signal. One call can take seconds while a building time step takes milliseconds [@neubauer2026]. An ML predictor needs a history of features rather than a single value. Reinforcement-learning results are notoriously hard to reproduce [@henderson2018]. A co-simulation that contains such components needs rules for reproducing a run, for rejecting invalid values, and for deciding when a slow decision takes effect. Recent studies of LLM-based building control call the model synchronously inside the loop or offline, and a 2026 review lists bounded latency, physics guardrails and reproducibility as open needs [@neubauer2026].

Py-CoSim was introduced as a Python co-simulation infrastructure that couples EnergyPlus through its Python application programming interface (API) with non-building components such as weather data, ML models and an LLM [@zhang2023]. Its Simulation Management and Messaging Module (SMMM) acted as a data hub: components published their results to it and read what they needed, without knowing each other. That first version exchanged data through one CSV file per component and time step and attached no meaning to the time stamps. The present paper keeps the idea of a message hub and develops it into a time-aware co-simulation method. Instead of asking how to connect program A to program B, every component publishes messages that state what their time stamps mean, and SMMM aligns them to what each receiving component needs.

The contributions are:

1. A message model in which every exchanged value carries its time meaning (an instantaneous sample, an interval average, an interval amount, a value held until replaced, or an event) and its validity interval, and in which every input declares the window it needs relative to its own step. SMMM reconstructs each signal from its messages and aligns it automatically: time-weighted means, proportional splitting of amounts, and sample-and-hold. To our knowledge, none of the general-purpose or building co-simulation tools we reviewed attaches interval semantics to exchanged values or uses them to align data between components with different step sizes. The vocabulary itself is borrowed from data conventions (@sec:related).
2. Message handling for AI components, built on established principles: per-message quality flags with bounds and fallbacks, record and replay of the messages of any component (including counterfactual replay into a modified system), a provenance trace, and deadline-aware asynchronous execution in which a slow call runs in wall-clock time while its answer takes effect at a declared simulation time.
3. An open-source implementation for EnergyPlus and Python components (Py-CoSim 2.1), in which EnergyPlus can also run a finer internal time step than the exchange step. It is evaluated through verification against standalone EnergyPlus, coupling-error studies on synthetic signals and on an EnergyPlus case, reproducibility and latency experiments with stand-ins for LLM services, an overhead analysis, and a head-to-head comparison with HELICS.

@sec:related reviews related work. @sec:method describes SMMM 2.0. @sec:impl summarizes the implementation. @sec:eval reports the evaluation. @sec:discussion discusses limitations and @sec:conclusions concludes.

# Related work {#sec:related}

## Co-simulation standards and master algorithms

FMI defines how a simulation unit is packaged, initialized and stepped [@blochwitz2011]. FMI 3.0 adds clocks, an event mode for co-simulation, early return from a step and an intermediate-update mode in which the importer can inspect outputs during a step [@junghanns2021; @gomes2021clocks; @fmi3_2022]. Discrete inputs keep their values between communication points, and outputs are values at the unit's current time. The System Structure and Parameterization (SSP) standard describes how units are connected, including unit conversion and linear transformations on connections [@ssp2_2024]. Neither standard says whether an output is an average or a total over the communication step.

How the units are stepped is the job of a master algorithm. Kübler and Schiehlen compared Jacobi and Gauss–Seidel coupling and the role of direct feedthrough [@kubler2000]. Broman et al. stated the conditions under which a master composing FMUs gives determinate results [@broman2013]. Gomes et al. surveyed the field, including how signals are reconstructed between communication points by holding or extrapolating them [@gomes2018]. Several methods reduce the physical coupling error of explicit co-simulation. The nearly energy-preserving coupling element (NEPCE) feeds back the energy residual left by extrapolation [@benedikt2013nepce; @benedikt2013], and power bonds monitor that residual to estimate the error and adapt the step size [@sadjina2017]. These methods correct the physics of the coupling after the fact. The problem addressed here is related but different: which interval a reported value belongs to, so that it is aggregated correctly in the first place. The closest co-simulation concept is semantic adaptation, in which an adapter written for each connection corrects mismatches between the formalisms of two units [@tripakis2015; @gomes2019semantic]. Simulink inserts rate-transition blocks automatically, but only with hold and delay semantics [@mathworks_rt]. In the tagged-signal model, a signal is a set of time-tagged events [@lee1998]. The `average` and `amount` messages of this paper can be read as events tagged with an interval instead of an instant.

## Message-based co-simulation

Exchanging data through a broker rather than direct connections is well established. The High Level Architecture (HLA) lets federates publish and subscribe to object attributes through a run-time infrastructure. Its time management delivers time-stamp-ordered messages, and each federate declares a lookahead, a promise not to send messages earlier than its current time plus that amount [@ieee1516_2010; @ieee1516_1_2010; @fujimoto2000]. Conservative and optimistic synchronization protocols are the classical ways to run such federations in parallel while keeping results deterministic [@chandy1979; @jefferson1985; @fujimoto1990]. In the energy domain, HELICS distinguishes persistent values from one-time messages, reduces several publications into one input (sum, maximum, average across sources), offers recorder and player applications, and runs federates in many languages and on many machines [@palmintier2017; @hardy2024; @helics_docs]. mosaik 3 combines time-stepped and event-based simulators, distinguishes persistent measurements from events, and supports time-shifted and weak connections [@ofenloch2022; @mosaik_docs]. Publish/subscribe middleware such as DDS and MQTT, with topic wildcards, has been used for power-system co-simulation [@omg_dds2015; @oasis_mqtt2019; @wen2024; @paduani2023], and VOLTTRON uses a topic tree for building devices [@katipamula2016].

These frameworks provide hold and event semantics and reductions across sources. We found none in which a value states that it is the mean or the total over an interval, and none that time-averages or integrates a signal over the receiver's step. In HELICS, an input receives the newest value at its granted time; in mosaik, a receiver reads the value valid at the start of its step [@helics_docs; @mosaik_docs]. Averaging or integrating must be written into the simulators.

## Building co-simulation tools

BCVTB and the EnergyPlus External Interface exchange data at a fixed synchronization step equal to the EnergyPlus zone time step [@wetter2011; @doe_extif]. EnergyPlus FMU export likewise requires the EnergyPlus time steps per hour to match the FMU sampling time [@nouidui2014; @lbnl_fmuexport]. MLE+ coupled EnergyPlus with MATLAB [@bernal2012]. Spawn of EnergyPlus couples Modelica HVAC and controls with EnergyPlus envelope models [@wetter2020]. Since EnergyPlus 9.3, a Python API allows EnergyPlus to be driven from callbacks [@doe2022], which Sinergym, COBS, Energym and BuildingGym use for control research [@jimenez2021; @campoy2025; @zhang2020cobs; @scharnhorst2021; @dai2025]. BOPTEST exposes test cases through a web API with a configurable control step [@blum2021], CityLearn targets district demand response [@vazquez2019; @nweye2025], and Alfalfa runs building models as a service that controllers read and write at run time [@benne2024]. These tools step synchronously or follow the wall clock. We found no time-meaning metadata, no record/replay of components and no asynchronous handling of slow AI calls in them.

## AI components in building simulation and control

LLMs have been used to choose HVAC set-points [@ahn2023; @song2023; @zhu2025], to explain and support ML-based control [@zhangchen2024], in office-in-the-loop experiments with multimodal models [@sawada2024; @sawada2025], with retrieval-augmented control [@ko2025; @liu2026colllm], as simulated occupants [@sasaki2025], and to generate or revise EnergyPlus models [@jiang2024; @zhang2024bem; @zhang2025agentic; @li2025mcp; @dieng2026]. Reviews summarize opportunities and risks [@liu2025llm; @neubauer2026]. These studies call the model synchronously or offline. Infrastructure for deterministic timing of slow calls, for replaying LLM decisions, or for flagging invalid outputs inside a co-simulation is missing.

## Ideas borrowed from data standards and real-time systems

The time meanings used in this paper exist in data conventions. The Climate and Forecast (CF) conventions state through `cell_methods` whether a value is a point, a mean or a sum over its time cell, with explicit bounds [@cf2023]. WaterML 2.0 gives every time series an interpolation type such as average, total or constant in the preceding or succeeding interval [@ogc_waterml2014]. EnergyPlus classifies its output variables as averaged or summed when reporting at coarser frequencies [@doe2024rdd]. Industrial communication standards attach a quality to every value: OPC UA status codes include last usable value and substitute value [@opcua_part8], and IEC 61850 marks values as good, invalid or questionable [@iec61850_7_3]. Provenance models such as W3C PROV describe which activity produced which entity from which inputs [@moreau2013prov; @ruscheinski2017]. Deterministic record and replay is a standard debugging technique [@chen2015replay], and has recently been applied to LLM agents [@feng2025agentrr]. Finally, real-time systems separate when a task's outputs take effect from how long it actually runs. Giotto fixes this logical execution time [@henzinger2003], PTIDES relates model time to physical time [@zhao2007], and Lingua Franca runs a deadline handler when a reaction is late [@lohstroh2021]. Decision delays are also modeled explicitly in reinforcement learning [@katsikopoulos2003; @ramstedt2019]. SMMM 2.0 brings these ideas into building co-simulation. @tbl:features compares it with representative tools.

@@TABLE_FEATURES@@

# SMMM 2.0: co-simulation through time-aware messages {#sec:method}

## Overview

A Py-CoSim co-simulation consists of components and SMMM (@fig:concept). A component is any model, data source, controller or service with named input and output ports: an EnergyPlus building, a PV array, a tariff, an ML predictor, an LLM supervisor or a reinforcement-learning agent. Components never call each other. At each of its steps, a component publishes messages on its output ports and reads the messages it subscribed to. SMMM has two halves, as in the original design [@zhang2023]. The *messaging* half stores every message with its time meaning and answers time-aware queries. The *management* half keeps one simulation clock, decides in which order components run within a step, delivers the answers of slow components, and writes logs, error reports and results. Subscriptions are made by port name, by topic, or by pattern (for example, every `P_elec` output), so a new building or a new data source joins without rewiring the others.

![Two ways to organize a co-simulation. (a) Connecting software: each pair of tools needs its own interface and time conversion. (b) Py-CoSim: every component publishes messages that state what their time stamps mean to SMMM, which aligns them to each subscriber's step, flags their quality, records them for replay and decides when slow answers take effect.](../figures/fig01_smmm_concept.png){#fig:concept width=16cm}

## Messages and their time meaning

The master clock advances on a grid $t_k = t_0 + k\,\Delta t$. A component $c$ runs every $n_c$ master steps, so its own step is $h_c = n_c\,\Delta t$. A message published on a port is

$$ m = \left( v,\; a,\; b,\; q,\; k_o \right) $$ {#eq:message}

where $v$ is the value, $[a, b)$ is its validity interval ($b$ may be open), $q$ is a quality flag and $k_o$ is the step whose inputs produced it. The *kind* of the output port says how $v$ relates to $[a, b)$. Five kinds cover the signals met in building co-simulation (@tbl:kinds).

Table: Time meanings of messages. A message published at $t$ by a component with step $h$ gets the validity shown unless the component states it explicitly. {#tbl:kinds}

| Kind | Meaning of $v$ | Default validity | Building examples |
|:---|:---|:---|:---|
| `instant` | sample of a continuous quantity at $a$ | $[t, t]$ | outdoor temperature, battery state of charge |
| `average` | mean of a rate over $[a, b)$ | $[t-h, t)$ (past) or $[t, t+h)$ (ahead) | EnergyPlus power and temperature reports, battery power |
| `amount` | total over $[a, b)$ | as for `average` | EnergyPlus energy meters in J or kWh |
| `hold` | valid from $a$ until replaced | $[t, \infty)$ | set-points, prices, commands, AI decisions |
| `event` | something that happened at $a$ | $[t, t]$ | demand-response notice, fault alarm |
Note: Untyped ports behave as `hold`, which reproduces the behavior of Py-CoSim 2.0. A component can also publish messages with explicit validity, for example a price announced for a future hour.

From the messages of a port, SMMM reconstructs a signal $\hat{x}(\tau)$. For `hold` signals, $\hat{x}(\tau)$ is the value of the message with the latest start $a_i \le \tau$. For `average` signals, $\hat{x}(\tau) = v_i$ on $[a_i, b_i)$. For `amount` signals the reconstructed rate is $\hat{r}(\tau) = v_i / (b_i - a_i)$ on $[a_i, b_i)$. For `instant` signals, $\hat{x}$ interpolates linearly between samples. Where no message covers a time yet, the last known value or rate is held, and the share of a query window covered by messages is reported as its *coverage*.

## Read modes and alignment

Each input port, or each subscription, declares what the component needs relative to its own step $[t_k, t_k + h_c)$: the most recent message (`latest`, the default and the behavior of Py-CoSim 2.0), the value in effect at $t_k$ (`at`), or an aggregate over the window that has just ended, $W^{-}_k = [t_k - h_c, t_k)$ (`past`), or the one that starts, $W^{+}_k = [t_k, t_k + h_c)$ (`ahead`). For a window $W$ SMMM returns

$$ \bar{x}(W) = \frac{1}{|W|} \int_{W} \hat{x}(\tau)\, \mathrm{d}\tau $$ {#eq:mean}

for `instant`, `average` and `hold` signals, the total

$$ X(W) = \int_{W} \hat{r}(\tau)\, \mathrm{d}\tau $$ {#eq:amount}

for `amount` signals, and the list of events in $W$ for `event` signals. Because the reconstructed signals are piecewise constant or piecewise linear, the integrals are exact sums over the messages that overlap $W$. Several sources feeding one input are aligned first and then reduced (summed, averaged, and so on).

@fig:timeline shows why this matters. An EnergyPlus building with a 15-min step reports at 10:15 the mean power of 10:00–10:15. A tariff publishes at 10:15 the price valid from 10:15. A meter that multiplies the latest power by the latest price charges the energy of 10:00–10:15 at the price of 10:15–10:30. With `read="past"`, the same meter receives the mean power and the mean price of 10:00–10:15, whatever the step sizes of the building and the tariff are. An hourly meter receives the mean of four 15-min reports. A 15-min consumer of an hourly producer receives the last complete hour, held, and the alignment report records the missing coverage.

![Time meanings and read modes. Messages of an `average` (past) power signal, a `hold` price signal and an `instant` temperature signal, and the windows a consumer with step $h$ reads at $t_k$ with `past`, `at` and `ahead`.](../figures/fig02_time_meaning.png){#fig:timeline width=16cm}

## Management: clock, phases and dependencies

Within master step $k$, SMMM runs four phases. In phase 0 it delivers the answers of asynchronous components that are due and publishes the messages of replayed components. In phase 1, components whose outputs depend only on their state (non-feedthrough components such as EnergyPlus) report their outputs at $t_k$. In phase 2, feedthrough components (controllers, tariffs, ML models) run in an order computed from their dependencies. In phase 3, non-feedthrough components advance to $t_{k+1}$. EnergyPlus instances advance in parallel worker processes in this phase. Components with longer steps run only when $k$ is a multiple of $n_c$.

The phase-2 order is a topological sort of the feedthrough components [@kahn1962]. A link from producer $p$ to consumer $c$ requires $p$ to run first only if $c$ needs what $p$ publishes in the current step. Time meanings refine this rule. A `past` read of a `hold`, `event` or `average`-ahead signal never needs the message published at $t_k$, because that message is only valid from $t_k$ on. Such links impose no ordering, which removes artificial algebraic loops in the same way as lookahead in HLA and time-shifted connections in mosaik [@fujimoto2000; @ofenloch2022]. Remaining cycles are reported with their path.

## Message handling for AI components

*Quality.* An output port may declare bounds. A value outside them is clipped or replaced by the last valid value or a default, and a non-finite value is replaced. The message keeps a quality flag (`ok`, `clipped`, `fallback`, `late` or `replayed`), following status-code practice in OPC UA and IEC 61850 [@opcua_part8; @iec61850_7_3], and results include a count per port. The LLM supervisor of Py-CoSim additionally parses and validates the model's JSON answer, limits the rate of change and falls back to the previous decision; port bounds are a second, generic line of defense that also applies to ML models, data APIs or user code.

*Record and replay.* Any component's messages can be journaled with their values, validity intervals and originating steps. In a later run, the component can be replaced by a replay component that publishes exactly the recorded messages at the recorded steps and ignores its inputs. When all non-deterministic components are replayed, every other component receives the same messages, so the run is repeated bit for bit. Replaying recorded AI decisions into a modified system, for example a retrofitted building, gives a counterfactual in which the AI's randomness is held fixed. Record/replay is an established technique, and HELICS offers a recorder and player [@chen2015replay; @helics_docs]. Here it is automatic, per component, and aimed at AI components.

*Provenance.* Optionally, SMMM records which messages each component read at each step. A recorded message can then be traced back through the chain that produced it, for example from a grid import through an EnergyPlus output and an LLM decision to the temperature and price it was based on. This is a lightweight, message-level form of a PROV graph [@moreau2013prov].

*Deadline-aware asynchronous components.* A slow feedthrough component, such as an LLM supervisor or a web service, can be declared asynchronous with a decision delay $d$ (a multiple of $\Delta t$) and optionally a wall-clock deadline $D$. When it is due at $t_k$, SMMM gathers its inputs, hands the call to a background thread and keeps simulating. Its outputs are published in phase 0 of the step at $t_k + d$, with validity starting there, however long the call took. If the call has not returned $D$ seconds after it started, the previous outputs are kept and flagged `late` (or the run stops, if so configured). The simulated decision delay is a model parameter, the counterpart of HLA lookahead, and results do not depend on the computer's speed or the service's latency as long as no deadline is missed. This applies the logical-execution-time principle of Giotto and Lingua Franca to AI components [@henzinger2003; @lohstroh2021]. Calls of several components overlap with each other and with EnergyPlus. An asynchronous component that reads other ports through its context sees them as they were at $t_k$.

## EnergyPlus adapter

EnergyPlus owns its time loop. The adapter turns it into a steppable component by blocking inside the callback at the beginning of each zone time step until the master sends the actuator values. Outputs are read in the callback at the end of the zone time step, so the value published at $t_k$ equals the value EnergyPlus reports with time stamp $t_k$. Time stamps are built from the date, hour and zone time-step number because the API's current-time functions follow the variable HVAC sub-steps. Each EnergyPlus instance runs in its own worker process, because EnergyPlus states are not thread-safe and a finished simulation cannot be restarted in the same process. Outputs are typed: variables and power meters are `average` and energy meters are `amount`, both over the step that has just ended, as EnergyPlus reports them [@doe2024rdd].

The exchange step may be a multiple of the EnergyPlus zone time step. EnergyPlus then runs the intermediate steps on its own, the adapter re-applies the last actuator values in each of them, and the outputs are aggregated by kind: rates are averaged, energies summed and other values taken from the last step. This decouples the numerical step of the building model from the communication step of the co-simulation, which EnergyPlus's External Interface and FMU export do not allow [@doe_extif; @lbnl_fmuexport].

# Implementation {#sec:impl}

Py-CoSim 2.1 is a Python package (Python 3.9 or later) released under the MIT license. Its core depends only on NumPy and pandas [@harris2020]. Components subclass a base class or wrap a plain function with a decorator; ports are declared as dictionaries, for example `{"P": {"kind": "average", "interval": "ahead"}}`. The package provides components for EnergyPlus buildings, EPW weather, PV (a PVWatts-type model [@dobos2014]), batteries, tariffs, grid metering, controllers, a two-node resistance–capacitance building, occupancy from the 2017 National Household Travel Survey [@fhwa2017], LLM supervisors with interchangeable back ends, an adapter for co-simulation FMUs, and a Gymnasium environment for reinforcement learning [@towers2025]. Library components declare the time meaning of their ports: the grid meter, for instance, reads power and price over the step that has just ended. Scenarios can also be written in YAML and run from the command line. The repository contains 11 example co-simulations and a test suite of @@N_TESTS@@ tests, run in continuous integration on three operating systems.

@@IMPL_REST@@

# Evaluation {#sec:eval}

@@EVAL@@

# Discussion {#sec:discussion}

@@DISCUSSION@@

# Conclusions {#sec:conclusions}

@@CONCLUSIONS@@

# Acknowledgements {-}

@@ACK@@

# References {-}
