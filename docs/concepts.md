# Concepts

This page explains the handful of ideas behind every Py-CoSim co-simulation. Read it once;
everything else in the documentation builds on it.

## Components and ports

A **component** is one model or piece of logic: an EnergyPlus building, a weather file
reader, a battery, a controller, an ML model, an LLM. Each component has named **ports**:

* **inputs** — values it needs from others (e.g. `clg_sp`, the cooling set-point),
* **outputs** — values it produces for others (e.g. `T_zone`, `P_elec`).

A port is addressed as `"component.port"`, e.g. `"office.T_zone"`. Component names must
start with a letter or `_` and contain only letters, digits, `_` or `-` (no dots or spaces,
because the dot separates component and port).

```python
sim.connect("office.T_zone", "controller.T_zone")   # output -> input
```

Each input has a **default** that is used while it is not connected (or before its source
has produced a value). Each output can have an **initial value** that others see before the
component has run for the first time.

`sim.describe()` prints all components with their ports, the connections and the execution
order — use it whenever you are unsure what is connected to what.

## The master and the time grid

`CoSimulation` is the **master**: it owns the clock and calls the components.

```python
sim = pc.CoSimulation(start="2023-07-17", end="2023-07-24", step="15min")
```

* The clock advances on a fixed grid `t_k = start + k * step`, k = 0, 1, 2, …
* `end` is **exclusive**: the example above has 7 × 96 = 672 steps, the last one at
  2023-07-23 23:45.
* `step` accepts strings (`"15min"`, `"1h"`, `"30s"`, `"1 day"`, ISO `"PT15M"`), numbers in
  seconds (`900`) or a `timedelta`.
* If the window is not a whole number of steps, it is truncated (with a warning).
* With an EnergyPlus component you may leave out `start`, `end` and `step`; they are then
  taken from the model's RunPeriod and Timestep (see [EnergyPlus](energyplus.md#clock-run-period-and-time-step)).

Every component receives the current time `t` as a normal Python `datetime`.

## Feedthrough and non-feedthrough components

Components come in two kinds. The difference decides the order in which they run.

| | **Feedthrough** (default) | **Non-feedthrough** (state-based) |
|---|---|---|
| Outputs at `t` depend on … | the inputs at the same `t` | only the internal state at `t` |
| You implement | `step(t, inputs)` → outputs | `output(t)` → outputs, and `update(t, inputs)` |
| Typical examples | controllers, tariffs, data sources, PV, ML/LLM models | EnergyPlus, the RC building, any model integrated over time |
| Attribute | `direct_feedthrough = True` | `direct_feedthrough = False` |

**Why EnergyPlus is non-feedthrough.** EnergyPlus cannot tell you the zone temperature at
the end of the next interval before it has simulated that interval, and a set-point you
send now can only influence the future. At time `t`, EnergyPlus already *knows* its state
(the result of the interval that just ended) and *needs* the actuator values for the
interval that starts now. That is exactly the non-feedthrough pattern: report the state
(`output`), then receive inputs and advance (`update`). The RC building model
(`RCBuilding`) follows the same pattern so you can swap one for the other.

A useful consequence: a loop *building → controller → building* never needs special
treatment, because the building's outputs are available before the controller runs.

## One master step: output → step → update

At each grid time `t_k` the master runs three phases:

```text
             one master step k, at time t_k
 ┌───────────────────────────────────────────────────────────────────────┐
 │ 1. OUTPUT   state-based components publish their state y(t_k)         │
 │             office (EnergyPlus), house (RC) ...           ──► data bus │
 │                                                                       │
 │ 2. STEP     feedthrough components run in data-flow order             │
 │             weather → tariff → controller → meter                     │
 │             each reads its inputs from the bus and        ──► data bus │
 │             publishes its outputs for t_k                             │
 │                                                                       │
 │ 3. UPDATE   state-based components read their inputs u(t_k) ◄── bus   │
 │             and advance over [t_k, t_k + dt)                          │
 │             (all EnergyPlus workers simulate in parallel here)        │
 │                                                                       │
 │    observers (progress bar, live plot, CSV stream) are notified       │
 └───────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼  k = k + 1
```

The order in phase 2 is computed automatically from the connections (a *topological
order*): if `controller` reads `tariff.price`, the tariff runs first. Components that do not
depend on each other run in the order given by their `priority` (lower first; default 0)
and then in the order they were added. `sim.describe()` shows the result, e.g.
`execution: output[house] -> step[weather, tariff, thermostat] -> update[house]`.

Each EnergyPlus building lives in its own worker process. In phase 3 the master hands every
building its actuator values and moves on; the buildings then simulate their interval at
the same time, and phase 1 of the next step collects their results. This is where the
speed-up for multi-building studies comes from. Start-up is parallel as well: before the
first step, all components run `setup()` (EnergyPlus workers are launched and start sizing),
and only then does the master wait for each of them to be `ready()`.

## Multi-rate components and zero-order hold

Not everything needs to run every master step. A component can have its own **step size**,
which must be a whole multiple of the master step:

```python
llm = LLMSupervisor(..., step_size="1h")      # decides once per hour
weather = EPWWeather("weather", epw)
weather.step_size = "1h"                      # built-in components: set the attribute
```

Between its updates, a component's outputs are **held constant** (a *zero-order hold*):
with a 15-minute master step, an hourly LLM decision is seen by the building in all four
15-minute steps of that hour. A component only runs at master steps `k` where
`k % multiple == 0`, i.e. at `start`, `start + step_size`, …

Inside a component, `ctx.step_size` is its own step in seconds (use it for integration),
and `ctx.dt` is the master step.

## Connections

```python
sim.connect(source, target, *, delay=False, transform=None, reduce=None)
```

* `source`: `"comp.port"`, `comp["port"]`, a `(comp, "port")` pair, or a **list** of those
  (fan-in).
* `target`: one input port. Each input can be connected only once.

### Fan-in with `reduce`

Several outputs can feed one input if you say how to combine them:

```python
loads = [f"bldg{i:02d}.P_facility" for i in range(10)]
sim.connect(loads, "grid.P_net", reduce="sum")
```

`reduce` can be `"sum"`, `"mean"`, `"max"`, `"min"` (all ignore missing `None` values),
`"list"` (passes the list of values in source order) or any function that takes the list
and returns one value, e.g. `reduce=lambda xs: xs[0] - xs[1]`.

### `transform`

A function applied to the value on its way (after `reduce`): unit conversions, scaling,
simple logic. It is skipped when the value is `None`.

```python
sim.connect("office.P_facility", "agent.P_elec", transform=lambda w: w / 1000)  # W -> kW
sim.connect("occ.occ_00", "apt.sp_GN1", transform=lambda n: 23.9 if n > 0.05 else 26.7)
```

### `delay=True`

The target reads the value the source had at the **end of the previous master step**
instead of the current one. At the first step it reads the source's initial value (or the
input default if there is none).

```python
sim.connect("battery.soc", "dispatch.soc", delay=True)
```

`connect_matching("a", "b")` connects every output of `a` to the same-named input of `b`
(optionally only the names in `ports=[...]`).

## Algebraic loops and how to break them

If feedthrough components form a cycle — A needs B's output *at the same time*, and B needs
A's — there is no order in which they can run. This is an **algebraic loop**, and Py-CoSim
refuses to start:

```text
AlgebraicLoopError: Algebraic loop detected between direct-feedthrough components:
dispatch -> battery -> dispatch.
Break the loop by passing delay=True to one of the connections in this cycle ...
```

Two ways out:

1. **`delay=True`** on one connection of the cycle. Example 04 does this: the dispatch
   controller uses the battery's state of charge from the previous step, which is also what
   a real battery management system would report.
2. Make one of the components **non-feedthrough** if its outputs really only depend on its
   state (that is why building → controller → building loops never cause this error).

## The data bus

All values pass through an in-memory **data bus** (it replaces the CSV files of Py-CoSim
0.x). The bus stores every published value with its time. Besides normal connections,
a component can read any port through its context:

```python
self.ctx.latest("office.T_zone")          # most recent value of any output port
self.ctx.history("weather.T_out", n=4)    # last 4 published values as a pandas Series
```

`history` only contains the times at which the producer actually published (its own rate).
Use `latest`/`history` for monitoring and for lagged features (e.g. the last 24 h of
temperature for an ML model). For values your component *depends on*, use a connection:
only connections tell the master about the dependency, so only they guarantee that the
producer has already run in the current step.

## The event log

Besides numbers, components can write **events** — text messages with optional data — via
`ctx.log(...)`:

```python
self.ctx.log("EV departs", kind="ev", soc=self.soc)
```

Events are collected in `res.events` (columns `time`, `source`, `kind`, `message`, plus one
column per extra keyword) and saved to `events.csv`. The LLM supervisor writes every prompt,
raw answer and applied decision there (`kind == "llm"`). The last 20 events are also included
in the error report if a run fails.

## Results

`sim.run()` returns a `Results` object:

| Attribute | Content |
|---|---|
| `res.df` | DataFrame, one column per **output** port (`"comp.port"`), one row per master step; values of slower components are held between their updates |
| `res.events` | the event log |
| `res.timing` | wall-clock time and number of calls per component |
| `res.meta` | start/end, step, seed, wall time, versions, connections, per-component metadata |
| `res["office.T_zone"]` | shortcut for `res.df["office.T_zone"]` |

Results are saved to `<output_dir>/<name>/` (`timeseries.csv`, `events.csv`, `timing.csv`,
`meta.json`). Note that **inputs are not recorded** — to see what a building received, look at
the output of the component that sent it. More in [Scenarios and CLI](scenarios-and-cli.md#results).

## Reproducibility

```python
sim = pc.CoSimulation(..., seed=42)
```

Every component gets its own random number generator `ctx.rng` (a NumPy `Generator`),
seeded from the simulation `seed` *and the component's name*. Consequences:

* the same seed gives exactly the same run;
* random streams are independent between components, and adding or removing another
  component does not change them;
* renaming a component changes its stream.

Use `self.ctx.rng` (never `np.random.seed` or the global `random` module) for anything
random. The default seed is `0`. With `seed=None`, Py-CoSim draws a random seed, stores it in
`sim.seed` and in the results' `meta["seed"]`, so even a "random" run can be repeated
afterwards by passing that number. EnergyPlus itself is deterministic. LLM answers are not —
use [record/replay](ai-and-rl.md#reproducibility-record-and-replay) for those. `meta.json`
stores the seed and the Python, Py-CoSim and EnergyPlus versions of every run.

## Time convention

The co-simulation clock uses **local standard time without daylight saving time (DST)**.
Times are "naive" Python datetimes (no time zone attached). This matches how EnergyPlus
counts time internally and how EPW weather files are written.

**Why the `daylight_saving` option matters.** If an EnergyPlus model contains a
`RunPeriodControl:DaylightSavingTime` object, EnergyPlus shifts the *schedules* by one hour
during summer: a schedule that says "occupied 8:00–18:00" then acts from 7:00 to 17:00
standard time. Your Python controllers, however, see `t.hour` in standard time. Without care
the model's own schedules and your controller are one hour apart in summer. Options:

```python
pc.EnergyPlusComponent(..., daylight_saving=False)   # remove DST: everything in standard time
pc.EnergyPlusComponent(..., daylight_saving=None)    # default: keep the model as it is
                                                     # (a log message warns if DST is defined)
```

`daylight_saving=False` is recommended unless you deliberately study DST. If you compare with
measured data that uses clock time (with DST), shift the measurements, not the model.

## Timing of EnergyPlus values

Precisely which value does an EnergyPlus output carry at master time `t`, and when does an
actuator value take effect?

```text
              interval [t-dt, t)          interval [t, t+dt)
         |----------------------------|----------------------------|---> time
       t-dt                           t                          t+dt

 published at t : the value EnergyPlus reports with time stamp t
                  (= the end of the interval [t-dt, t) that just finished;
                   meters in W/kW: average power over that interval)
 actuators set at t : applied during [t, t+dt)
                      -> their effect appears in the values published at t+dt
```

* A sensor value published at `t` is **identical to the value EnergyPlus writes with time
  stamp `t`** in its own output files (verified in `paper/experiments/verification.py`), so
  you can compare Py-CoSim results and EnergyPlus CSV/SQL output row by row.
* `EPWWeather.T_out` at `t` equals the outdoor temperature EnergyPlus reports at `t`.
* A controller at time `t` sees the zone temperature at `t` and its decision acts on the next
  interval. In `res.df`, a set-point change in row `t` therefore shows its effect on
  temperature and power in row `t + dt`. This is causal, not a delay bug
  (see the [FAQ](faq.md#values-look-one-step-late)).
* At the very first step (`t = start`), EnergyPlus reports the state at the end of its
  warm-up period.

The RC building follows the same convention: its outputs at `t` are the state at `t` and the
average HVAC power over the previous step.

## Errors

When something goes wrong during a run, Py-CoSim raises a `ComponentError` that names the
component, the phase (`setup`, `output`, `step`, `update`, …) and the simulation time, and
writes `error_report.txt` (inputs of the failing component, traceback, last events,
connections) to the run folder. Partial results up to the failure are saved too and are
available as `sim.partial_results`. All framework errors derive from
`pycosim.errors.PyCoSimError`:

| Error | Meaning |
|---|---|
| `ConfigurationError` | the set-up is inconsistent (unknown port, bad step size, bad name, …) |
| `AlgebraicLoopError` | a cycle of same-time dependencies (see above) |
| `ComponentError` | an exception inside a component (the original is in `.original`) |
| `SynchronizationError` | a component's clock disagrees with the master (e.g. EnergyPlus run period) |
| `HandleNotFoundError` | EnergyPlus could not find a sensor/meter/actuator name |

The EnergyPlus-specific errors `EnergyPlusNotFoundError` (no installation found), `ModelError`
(a model cannot be read or edited) and `EnergyPlusRuntimeError` (EnergyPlus stopped
abnormally) derive from `PyCoSimError` too, so `except PyCoSimError:` catches every
framework problem.

## Running step by step

`sim.run()` is all you normally need. For interactive work you can also advance manually:

```python
sim.initialize()            # optional: run() and step() call it if needed
while sim.step():           # returns False after the last step has been executed
    if sim.now.hour == 12:
        print(sim.now, sim.bus.latest("house.T_zone"))
res = sim.finalize()        # finalize components and build the Results
```

`sim.now` is the time of the step just executed, `sim.k` its index and `sim.done` tells
whether the end has been reached. Do not call `step()` again after it returned `False`.
