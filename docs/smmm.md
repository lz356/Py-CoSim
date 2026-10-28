# Messages and time: SMMM

The heart of Py-CoSim is **SMMM**, the *Simulation Management and Messaging Module*. The
name comes from the original Py-CoSim paper, where SMMM was a data hub that passed CSV files
between tools. Today it has two halves:

* the **management** half is the master, `CoSimulation`: one clock, a barrier at every
  step, the execution order, deadlines;
* the **messaging** half is the data bus: it keeps every value a component publishes as a
  *message* and answers what the other components ask for.

This page explains the messaging features of SMMM 2.0. All of them are optional. A script
that connects ports with `connect()` and no `read=` works exactly as before; the one
exception is `GridMeter`, which now lines up its inputs in time by default (see
[GridMeter](component-library.md#gridmeter) and the [changelog](../CHANGELOG.md)).

Read [Concepts](concepts.md) first if *component*, *port* or *feedthrough* are new to you.
[Example 11](examples.md#11--messages-and-time) uses almost everything on this page in one
script that runs in about 15 seconds.

## Contents

* [Why messages instead of wiring software](#why-messages-instead-of-wiring-software)
* [The five kinds: what a time stamp means](#the-five-kinds-what-a-time-stamp-means)
* [The four read modes: what an input asks for](#the-four-read-modes-what-an-input-asks-for)
* [Alignment across step sizes](#alignment-across-step-sizes)
* [Patterns and topics](#patterns-and-topics)
* [Quality flags and bounds](#quality-flags-and-bounds)
* [Record and replay](#record-and-replay)
* [Provenance: trace and lineage](#provenance-trace-and-lineage)
* [Asynchronous components](#asynchronous-components)
* [EnergyPlus internal sub-steps](#energyplus-internal-sub-steps)
* [Limitations](#limitations)

## Why messages instead of wiring software

Many co-simulation tools connect *software*: tool A is wired to tool B through an interface
(an FMU, a socket, an adapter), and every pair of tools needs its own code to convert time
steps and units. A third tool means more adapters.

In Py-CoSim, components never call each other. Each component

1. **publishes** what it knows (a zone temperature, a price, a decision), and
2. **subscribes** to what it needs, without knowing who produced it.

A message is more than a number:

| Part | Example | Meaning |
|---|---|---|
| value | `1520.0` | the number (or text) |
| validity | `[12:00, 12:15)` | the time span the value describes |
| quality | `ok` | `ok`, `clipped`, `fallback`, `late` or `replayed` |
| origin | step 48 | the master step whose inputs produced the value |

Because every message says what its time stamp means, SMMM can hand it to a component that
runs at a different rate already converted ("aligned"). No conversion code is needed, and
new parts — a second building, a live price feed instead of a tariff model, an AI agent —
join without rewiring the others.

```text
  house  (15 min) ──┐                              ┌──► meter (1 h): mean power of its hour
  pv     (15 min) ──┼──►  SMMM: messages with   ───┼──► ai    (1 h): price in effect now
  tariff          ──┘     time meaning, quality,   └──► ...
                          origin
```

### Quick reference

| You want to … | Use |
|---|---|
| say what an output's values mean in time | `kind=` on the output: `instant`, `average`, `amount`, `hold`, `event` |
| get a value over your own step | `read=` on the input or the connection: `latest`, `at`, `past`, `ahead` |
| subscribe to every `P_elec` in the model | `sim.subscribe("meter.P_load", "*.P_elec", reduce="sum")` |
| subscribe without knowing the producer | `topic="grid/price"` on the output; `sim.subscribe("ctrl.price", "grid/price")` |
| catch nonsense values | `bounds=(low, high)` and `on_invalid=` on the output |
| repeat a run with random components exactly | `CoSimulation(..., record=["ai"])`, later `sim.replay("ai", run_folder)` |
| see which inputs led to a decision | `CoSimulation(..., trace=True)`, then `res.lineage("battery.P", when)` |
| let a slow component think while the simulation goes on | `component.make_async(delay="15min")` |
| run EnergyPlus at 1 min but exchange every 15 min | `EnergyPlusComponent(..., timestep=60, step_size="15min")` |

## The five kinds: what a time stamp means

Every output port can declare its `kind`. In the table, `h` is the step of the component
that publishes the value.

| `kind` | A value published at `t` means … | Building example | Read over a time window gives |
|---|---|---|---|
| `instant` | the state at the moment `t` (straight line between samples) | zone air temperature `RCBuilding.T_zone`, outdoor temperature `EPWWeather.T_out`, PV power `PVSystem.P_ac` | time-weighted mean |
| `average` | the mean of a rate over an interval | EnergyPlus `Electricity:Facility` in W: mean power over `[t-h, t)` | time-weighted mean |
| `amount` | the total over an interval | EnergyPlus meter in kWh: energy used during `[t-h, t)` | the total (split in proportion to time) |
| `hold` | a value valid from `t` until it is replaced | a set-point, a price, an on/off command | time-weighted mean |
| `event` | something that happened at `t` | a demand-response notice | the list of events in the window |

`average` and `amount` values also have an `interval`:

* `"past"` (the default): the value published at `t` describes `[t-h, t)`, the step that
  just ended. EnergyPlus and `RCBuilding` report like this.
* `"ahead"`: the value describes `[t, t+h)`, the step that starts. `Battery.P` is an example:
  at `t` the battery decides the power it will deliver over the coming step.

An output without a kind (`kind=None`) behaves like `hold`. That is how Py-CoSim 2.0
treated every value, and it is the right choice for set-points, prices and commands.

**How to declare it.** Use a dictionary instead of a plain initial value, call
`add_output(..., kind=...)` in `__init__`, or change an existing component with `set_port`:

```python
import pycosim as pc
from pycosim.components import Battery

class Chiller(pc.Component):
    direct_feedthrough = False
    inputs = {"T_chw_set": 7.0}
    outputs = {
        "T_chw": {"default": 7.0, "unit": "C", "kind": "instant"},                       # state at t
        "P_elec": {"default": 0.0, "unit": "W", "kind": "average", "interval": "past"},  # mean over [t-h, t)
        "E_elec": {"default": 0.0, "unit": "J", "kind": "amount"},                       # total over [t-h, t)
        "mode": {"default": "off", "kind": "hold"},                                      # until replaced
    }

@pc.component(outputs={"notice": {"default": None, "kind": "event"}})
def utility_dr(t):
    return "shed load" if (t.hour, t.minute) == (15, 30) else pc.HOLD   # publish only when it happens

battery = Battery("battery", capacity_kwh=13.5, max_charge_kw=5)
battery.set_port("P", kind="average", interval="ahead")   # what Battery already declares
print(battery.describe())                                 # shows the kinds next to the ports
```

**When to use it.** Declare a kind whenever others will add up, average or price a value
over time: power, energy, prices, set-points. All [library components](component-library.md)
and EnergyPlus outputs already declare theirs. A kind changes nothing for readers that use
the default `read="latest"`.

**Pitfalls.**

* A *running total* (e.g. `GridMeter.E_import`, kWh since the start) is **not** an
  `amount`: `amount` means "used during this interval". Leave counters untyped (or
  `instant`).
* A wrong `interval` shifts energy by one step. A component that applies a power over the
  step that *starts* (a battery, an EV charger written as a feedthrough component) publishes
  `average` with `interval="ahead"`; a simulator that reports the step that *ended* uses
  `"past"`.
* `res.df` still shows each value at the step it was published, held until the next one.
  The kind is stored in `res.meta["port_kinds"]`.

## The four read modes: what an input asks for

An input says what it needs relative to **its own** step `[t, t+h)`, where `h` is now the
step of the component that reads:

| `read` | You get | Typical use |
|---|---|---|
| `latest` (default) | the most recent message, whatever it means | everything that worked before; the fastest |
| `at` | the value in effect at `t` | a price or set-point announced in advance |
| `past` | the aggregate over `[t-h, t)`, the step that just ended | meters, cost accounting, hourly statistics of faster signals |
| `ahead` | the aggregate over `[t, t+h)`, the step that starts | schedules and plans published in advance |

"Aggregate" depends on the source's kind: the time-weighted mean for `instant`, `average`
and `hold` (and untyped) values, the total for `amount`, the list of values for `event`.

```text
                        one step of the READING component (length h)
                 ◄────────── past ──────────►◄────────── ahead ─────────►
   ──────────────|───────────────────────────|───────────────────────────|──────► time
                t-h                          t                          t+h
                                             ▲
                                   at: the value in effect at t
                                   latest: the newest message, whatever span it covers

   what some sources publish at t:
   EnergyPlus P_elec  (average, past)   ████████████████████████████  covers [t-15min, t)
   Battery.P          (average, ahead)                               ████████  covers [t, t+15min)
   tariff price       (hold)                                         ████████████▶  valid from t on
```

**How to set it.** On the connection, or once on the input port (the connection wins):

```python
from datetime import timedelta
import pycosim as pc
from pycosim.components import EPWWeather, RCBuilding

EPW = "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"
sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", save=False)
sim.add(EPWWeather("weather", EPW), RCBuilding("house", floor_area=150))
sim.connect("weather.T_out", "house.T_out")
sim.connect("weather.GHI", "house.GHI")

@pc.component(outputs=["P_latest", "P_past"], step_size="1h",
              inputs={"a": 0.0, "b": {"default": 0.0, "read": "past"}})   # read on the port ...
def hourly(t, a, b):
    return a, b

sim.add(hourly)
sim.connect("house.P_elec", "hourly.a")                 # latest: the last 15-min value only
sim.connect("house.P_elec", "hourly.b")                 # past (from the port): mean over the hour
res = sim.run()
print(res.df.loc["2023-07-03 13:00", ["hourly.P_latest", "hourly.P_past"]])
print(res["house.P_elec"]["2023-07-03 12:15":"2023-07-03 13:00"].mean())  # = hourly.P_past
```

`sim.connect(..., read="past")` sets it per connection, `add_input(name, default, read=...)`
in a class's `__init__`. Inside a component, `ctx.at(ref, when=None)` and
`ctx.window(ref, start, end=None)` give the same aligned answers for *any* port, connected or
not (e.g. `self.ctx.window("house.P_elec", t - timedelta(hours=1))`).

**Coverage.** Sometimes no message covers part of the window yet — for example the first
`past` read lies before the simulation start, or an `ahead` read asks for a building's power
over `[t, t+h)`, which the building has not simulated yet. SMMM then fills the gap by holding
the nearest known value and counts it in `res.alignment`:

| Column | Meaning |
|---|---|
| `input`, `read`, `sources` | which input, how it reads, from which outputs |
| `reads` | how often it was read |
| `extrapolated_reads` | reads in which messages did not cover the whole window |
| `min_coverage` | the smallest covered share of a window (1.0 = always fully covered) |

One extrapolated read (the very first step) is normal. Many of them mean the read mode does
not fit the source.

**A `past` read never needs the current value.** When the source's value only becomes valid
at `t` (`hold`, `event`, untyped, or `average`/`amount` with `interval="ahead"`), a `past`
read does not have to wait for the source to run in this step. Such a connection therefore
cannot create an [algebraic loop](concepts.md#algebraic-loops-and-how-to-break-them) — it is
a cleaner alternative to `delay=True`.

**Pitfalls.**

* `delay=True` only works with `read="latest"`; combining it with another read mode raises a
  `ConfigurationError` (a `past` read already looks at the previous step).
* `latest` ignores time meaning. A message published now but valid from 16:00 (see below) is
  seen by `latest` readers, and in `res.df`, immediately.
* Aligned reads cost a little more time than `latest`. Use them where time meaning matters
  (energy, cost, slower readers), not everywhere.

### Publishing a value for another time: `pc.Message`

A component can return `pc.Message(value, start=..., end=..., quality=...)` instead of a bare
value to say exactly which time the value is for — for example a price announced in advance:

```python
from datetime import timedelta
import pycosim as pc

@pc.component(outputs={"price": {"default": 0.10, "kind": "hold"}}, step_size="1h")
def day_ahead(t):
    if t.hour == 0:
        return pc.Message(0.50, start=t + timedelta(hours=16))   # announced at 00:00, valid from 16:00
    if t.hour == 1:
        return pc.Message(0.10, start=t + timedelta(hours=20))   # back to 0.10 from 21:00
    return pc.HOLD

sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", save=False)
sim.add(day_ahead, pc.FunctionComponent(lambda t, p=0.0: p, name="buyer", outputs="seen"))
sim.connect("day_ahead.price", "buyer.p", read="at")
res = sim.run()
for when in ("15:45", "16:00", "20:45", "21:00"):
    print(when, res["buyer.seen"][f"2023-07-03 {when}"])                    # 0.1, 0.5, 0.5, 0.1
```

`start` and `end` are datetimes (or seconds since the start); `end` only matters for
`average` and `amount` values. Only `at`, `past` and `ahead` readers respect `start`.

## Alignment across step sizes

The most common case: a slow reader needs a fast signal. Here an hourly grid meter reads the
15-minute electric power of an EnergyPlus office. With `GridMeter(aligned=True)` (the default)
the meter reads its power and price inputs with `read="past"`, so at 01:00 it gets the mean of
the four 15-minute values that describe 00:00–01:00, and the mean price of that hour. The
`aligned=False` meter reads the latest value, i.e. only the power of 00:45–01:00, and counts
it for the whole hour.

```python
import pycosim as pc
from pycosim.components import GridMeter, TimeOfUseTariff

EPW = "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"
office = pc.EnergyPlusComponent(
    "office", "examples/models/small_office.idf", EPW,
    meters={"P_elec": ("Electricity:Facility", "W")},       # average/past: mean W over [t-15min, t)
    daylight_saving=False)
aligned = GridMeter("aligned")                 # aligned=True is the default
naive = GridMeter("naive", aligned=False)      # Py-CoSim 2.0 behaviour
aligned.step_size = naive.step_size = "1h"     # both meters account once per hour

sim = pc.CoSimulation("2023-07-17", "2023-07-24", "15min", name="hourly_meter")
sim.add(office, TimeOfUseTariff("tariff"), aligned, naive)
sim.subscribe("aligned.P_load", "office.P_elec")    # read="past" (set by the meter)
sim.subscribe("aligned.price", "tariff.price")
sim.subscribe("naive.P_net", "office.P_elec")       # read="latest": one 15-min sample per hour
sim.subscribe("naive.price", "tariff.price")
res = sim.run()

t_end = "2023-07-23 23:00"                           # last whole hour
exact = res["office.P_elec"][:t_end].iloc[1:].sum() * 0.25 / 1000  # kWh on the 15-min grid
print(f"exact   {exact:8.2f} kWh")
for m in ("aligned", "naive"):
    print(f"{m:7s} {res[m + '.E_import'][t_end]:8.2f} kWh   cost ${res[m + '.cost'][t_end]:.2f}")
print(res.alignment)
```

Result for one July week in Tucson (EnergyPlus 22.2):

```text
exact    1821.91 kWh
aligned  1821.91 kWh   cost $247.09
naive    1817.49 kWh   cost $253.41
```

The aligned hourly meter reproduces the energy of the 15-minute data exactly. The naive one
is off by 0.2 % in energy and by 2.6 % in cost, because it prices each hour with a single
15-minute sample taken at the end of the hour. The same alignment works for any rates (1 min
to 1 h) and in both directions, and for every kind: `instant` PV power is averaged with the
trapezoid rule, `amount` energy is split in proportion to time, `hold` prices are
time-weighted.

**When to use it.** Whenever a component adds up, averages or prices a signal from a component
with a different step, or from a component with a different timing convention (an EnergyPlus
meter describes the step that ended, a battery the step that starts).

**Pitfall.** `GridMeter.P_net` keeps the old `latest` behaviour, while the meter's `price` is
now read `past`. That suits EnergyPlus meters and `RCBuilding.P_elec`, but power from a
feedthrough component that describes the coming step (`Battery.P`, an EV charger) is then
priced with the previous step's price. Connect consumption, generation and battery power to
`P_load`, `P_gen` and `P_bat` instead: SMMM aligns each of them by its own kind (see
[GridMeter](component-library.md#gridmeter)).

## Patterns and topics

**Patterns.** A source can be a glob pattern that is matched against every
`component.port` name:

```python
import pycosim as pc
from pycosim.components import GridMeter, RCBuilding

sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", save=False)
sim.add(GridMeter("meter"))
sim.subscribe("meter.P_load", "*.P_elec", reduce="sum")   # every P_elec, also ones added later
sim.add(RCBuilding("house1", 120), RCBuilding("house2", 200))
sim.initialize()
print(sim.describe())     # ... [house1.P_elec, house2.P_elec] -> meter.P_load
res = sim.run()
```

* `*` matches any text, `?` one character, `[ab]` one of the listed characters.
* `subscribe(input, source, ...)` is `connect(source, input, ...)` with the arguments in
  reading order; both accept patterns, topics, `read=`, `reduce=` and `transform=`.
* Patterns are resolved when the simulation is initialized, so components added after the
  `subscribe` call are included. The reading component's own outputs are never matched.
* A pattern that matches nothing raises a `ConfigurationError`; several matches need
  `reduce=`.

**Topics.** An output can also publish under a global name, a *topic*. Consumers subscribe to
the topic and do not need to know which component publishes it:

```python
import pycosim as pc
from pycosim.components import TimeOfUseTariff

sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", save=False)
tariff = sim.add(TimeOfUseTariff("tariff"))
tariff.set_port("price", topic="grid/price")          # or "topic": ... in an outputs dict

@pc.component(outputs="clg_sp")
def thermostat(t, price=0.10):
    return 26.0 if price > 0.2 else 24.0

sim.add(thermostat)
sim.subscribe("thermostat.price", "grid/price")
res = sim.run()
print(res.meta["topics"])                              # {'grid/price': 'tariff.price'}
```

To replace the tariff model by, say, a component that reads live prices from a web API, give
the new component an output with `topic="grid/price"` and remove the old one; the thermostat,
the meter and everything else that subscribed to the topic stay as they are. A topic can
have only one publisher.

**When to use them.** Patterns for "all buildings" or "all PV systems" in community studies;
topics for shared signals (price, grid frequency, a carbon signal, a demand-response notice)
whose producer you may want to swap.

**Pitfall.** A broad pattern can match more than you intend (`"*.P"` also matches
`battery.P`). Check what matched with `sim.initialize(); print(sim.describe())` — before
initialization `describe()` shows the pattern itself.

## Quality flags and bounds

Every message carries a quality flag:

| Flag | Set when |
|---|---|
| `ok` | the value was published as it was |
| `clipped` | the value was outside the output's `bounds` and was clipped to them |
| `fallback` | the value was invalid (outside the bounds, NaN, infinite or not a number) and was replaced |
| `late` | an [asynchronous](#asynchronous-components) answer missed its deadline; the previous value was kept |
| `replayed` | the value came from a [replay](#record-and-replay) |

Give an output `bounds=(low, high)` (either may be `None`) and choose what happens to an
invalid value with `on_invalid`:

| `on_invalid` | Out of bounds | NaN, infinite or not a number |
|---|---|---|
| `"clip"` (default) | clipped to the bounds, flagged `clipped` | last valid value, flagged `fallback` |
| `"last"` | last valid value, flagged `fallback` | last valid value, flagged `fallback` |
| `"default"` | the port's initial value, flagged `fallback` | the port's initial value, flagged `fallback` |
| `"error"` | the run stops with a `ComponentError` | the run stops |

```python
import pycosim as pc

@pc.component(outputs={"clg_sp": {"default": 24.0, "kind": "hold",
                                  "bounds": (21.0, 30.0), "on_invalid": "clip"}})
def ml_setpoint(t, ctx):
    r = ctx.rng.random()
    if r < 0.05:
        return float("nan")      # a broken model
    if r < 0.10:
        return 45.0              # an absurd answer
    return 23.0 + 3.0 * ctx.rng.random()

sim = pc.CoSimulation("2023-07-03", "2023-07-10", "15min", save=False)
sim.add(ml_setpoint)
res = sim.run()
print(res.quality)        # one row per port and flag (only ports with flagged messages)
```

**When to use it.** On every output of a component you do not fully trust: ML models, LLMs,
web APIs, measured data. It is a safety net at the level of SMMM; the
[`LLMSupervisor`](ai-and-rl.md#guardrails-why-the-building-stays-safe) has its own, more
detailed guardrails on top.

**Pitfalls.**

* `None` is not checked: it means "no value" (and releases an EnergyPlus actuator).
* The flag travels with the message, not with the input: a reading component receives the
  clipped number and does not see the flag. Look at `res.quality` (and, for recorded
  components, the `quality` column of `res.messages`) after each run.

## Record and replay

Physics models are deterministic; LLMs, sampled ML models and web services are not. To repeat
a run exactly, record the messages of the non-deterministic components and replay them:

```python
import random
import pycosim as pc
from pycosim.components import EPWWeather, RCBuilding

EPW = "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"

class Dice(pc.Component):
    """A stand-in for a non-deterministic AI (NOT an LLM): a random set-point every hour."""
    inputs = {"T": 25.0}
    outputs = {"sp": {"default": 24.0, "kind": "hold"}}
    def step(self, t, inputs):
        return {"sp": 23.0 + random.random() * 3.0}        # unseeded on purpose

def build(name, floor_area=100, **kw):
    sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", name=name, **kw)
    sim.add(EPWWeather("weather", EPW), RCBuilding("house", floor_area), Dice("ai", step_size="1h"))
    sim.connect("weather.T_out", "house.T_out")
    sim.connect("house.T_zone", "ai.T")
    sim.connect("ai.sp", "house.clg_sp")
    return sim

live = build("live", record=["ai"]).run()     # writes pycosim_runs/live/messages.jsonl
sim = build("replay")
sim.replay("ai", "pycosim_runs/live")         # a run folder, a messages.jsonl file or a Results object
again = sim.run()
print(again.df.equals(live.df))               # True: identical, bit for bit

sim = build("bigger_house", floor_area=200)   # counterfactual: same decisions, another building
sim.replay("ai", live)
cf = sim.run()
```

* `record=["ai"]` keeps every message of the listed components (`record=True`: all) in
  `res.messages` and, when results are saved, in `messages.jsonl`.
* `sim.replay(name, source)` (before `run()`) replaces the component by a replay that keeps
  its name and ports, ignores its inputs, and publishes the recorded messages at the recorded
  steps, flagged `replayed`. When every non-deterministic component is replayed, the run is
  repeated bit for bit — also if the original run had late answers or fallbacks.
* **Counterfactual replay** feeds the same decisions into a changed system (another building,
  tariff or battery). The AI's randomness is then held fixed, so differences come from the
  change alone.

Compared with `RecordingBackend`/`ReplayBackend` of the
[LLM supervisor](ai-and-rl.md#reproducibility-record-and-replay), which record the text of
each LLM call and need exactly the same prompts, SMMM replay records the *published
messages*. It works for any component and also when the prompts would differ (a
counterfactual building).

**Pitfalls.**

* A replayed component does not react: in a counterfactual it repeats what it decided for the
  original system, even if that no longer makes sense.
* Keep the simulation `start` and `step` and the component's name unchanged. Messages are
  replayed by master step number; Py-CoSim does **not** check that the clock is the same, so
  a different step silently shifts the decisions in time.

## Provenance: trace and lineage

With `trace=True`, SMMM also writes down which messages every component read at every step.
`res.lineage(ref, when, depth=4)` then follows a value back through the messages that
produced it:

```python
import pycosim as pc
from pycosim.components import EPWWeather, RCBuilding

EPW = "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"

@pc.component(outputs={"clg_sp": {"default": 24.0, "kind": "hold"}}, step_size="1h")
def supervisor(t, T_zone=24.0, T_out=30.0):
    return 23.0 if T_out > 38.0 and T_zone < 25.0 else 25.0     # pre-cool on very hot afternoons

sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", name="trace_demo", trace=True)
sim.add(EPWWeather("weather", EPW), RCBuilding("house", 150), supervisor)
sim.connect("weather.T_out", "house.T_out")
sim.connect("weather.T_out", "supervisor.T_out")
sim.connect("house.T_zone", "supervisor.T_zone")
sim.connect("supervisor.clg_sp", "house.clg_sp")
res = sim.run()
lin = res.lineage("house.P_elec", "2023-07-03 15:15", depth=2)
print(lin[["level", "message", "time", "value", "via_input", "source", "source_time", "source_value"]])
```

Each row is one link: the `message` (with its `time` and `value`) was produced by a component
that read `source` (published at `source_time` with `source_value`) through `via_input`. Level
0 is the value you asked about, level 1 what it was made from, and so on. Here the house's
power at 15:15 comes from the outdoor temperature at 15:00 and the set-point published at
15:00, which in turn came from the weather and the zone temperature at 15:00.

The trace is in `res.trace` (columns `step`, `consumer`, `input`, `source`, `first_index`,
`last_index`), is saved as `trace.csv`, and `pc.Results.load(folder).lineage(...)` works on
saved results.

**When to use it.** To audit AI decisions ("why did the battery discharge at 16:15?"), to
debug unexpected values, and to document a case study.

**Pitfalls.**

* Only inputs fed by connections are traced. Values a component reads itself with
  `ctx.latest`, `ctx.at`, `ctx.window` or `ctx.history` do not appear.
* `trace=True` also turns on `record=True` for all components. It keeps one row per input
  per step and every message, so switch it off for long production runs.

## Asynchronous components

An LLM or a web service may take seconds to answer. Normally the master waits for it at
every call. `make_async` lets the component think in the background while the simulation
goes on, and makes its answer take effect at a declared **simulation** time:

```text
   simulation time   16:00 ──────────────────── 16:15 ──────────────────── 16:30
                       │ inputs taken             │ answer published (phase 0)
                       │                          │ and used by everyone from here on
   wall clock          ├── the AI thinks 2.7 s ──►│
                       the master keeps simulating meanwhile; if the answer is not
                       ready when 16:15 is reached, the master waits for it
```

* The **decision delay** (`delay=`, simulation time) says when the answer applies: inputs
  taken at `t`, outputs published at `t + delay`. It is part of the model, like the time a
  real supervisor needs to act, and it never depends on the computer.
* The **latency** (wall-clock seconds the call really takes) only changes how long the run
  takes, not its results. Several asynchronous components (one per building, say) think at
  the same time instead of one after the other.
* An optional **deadline** (`deadline=`, wall-clock seconds) limits the wait. If an answer is
  not ready in time, the previous outputs are kept and flagged `late` (`on_deadline="fallback"`,
  the default), or the run stops (`on_deadline="error"`).

```python
import random
import time
import pycosim as pc
from pycosim.components import EPWWeather, RCBuilding

EPW = "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"

class SlowAdvisor(pc.Component):
    """A stand-in for a remote AI service (NOT an LLM): it sleeps, then answers."""
    inputs = {"T_zone": 24.0}
    outputs = {"clg_sp": {"default": 24.0, "kind": "hold", "bounds": (21.0, 30.0)}}
    def step(self, t, inputs):
        time.sleep(0.05 + 0.05 * random.random())              # "network + thinking"
        return {"clg_sp": 25.0 if inputs.T_zone < 24.5 else 23.5}

sim = pc.CoSimulation("2023-07-03", "2023-07-05", "15min", save=False)
sim.add(EPWWeather("weather", EPW), RCBuilding("house", 150))
sim.add(SlowAdvisor("ai", step_size="1h").make_async(delay="15min", deadline=5.0))
sim.connect("weather.T_out", "house.T_out")
sim.connect("house.T_zone", "ai.T_zone")
sim.connect("ai.clg_sp", "house.clg_sp")
res = sim.run()
print(res.meta["asynchronous"])   # {'ai': {'delay_steps': 1, 'deadline_s': 5.0, 'delivered': 48, 'late': 0}}
```

Rules and details:

* Only feedthrough components (with `step()`) can be asynchronous, and not an
  `ExternalAgent`. Built-in ones work too, e.g. `LLMSupervisor(...).make_async(delay="15min")`.
* `delay` must be a whole multiple of the **master** step (default: one master step). It may
  be shorter than the component's own step: an hourly AI with `delay="15min"` takes its inputs
  at 16:00 and its answer applies from 16:15 to 17:15.
* Inside `step()`, `self.ctx.now`, `ctx.latest`, `ctx.at`, `ctx.window` and `ctx.history` show
  the simulation as it was when the inputs were taken, not as it is while the call runs.
* Each asynchronous component runs in one background thread, so its own calls do not overlap
  (except after a missed deadline: the abandoned call may still be running while the next one
  starts). Do not share mutable objects with other components.
* Because its outputs only appear in a later step, an asynchronous component can never be part
  of an algebraic loop.
* Missed deadlines are logged in `res.events` (`kind == "late"`) and counted in
  `res.meta["asynchronous"]`.

**When to use it.** For slow components whose answers act on the future anyway: LLM
supervisors, remote forecasts, optimisation services, other people's APIs.

**Pitfalls.**

* An asynchronous component always answers `delay` later. Do not make a fast, deterministic
  controller asynchronous — it would just react later.
* With a `deadline`, *which* answers are late depends on the computer and the network, so the
  results do too. Record the component (`record=[...]`) to be able to repeat such a run
  exactly.

## EnergyPlus internal sub-steps

EnergyPlus can run with a finer internal time step than the exchange step of the
co-simulation. Give the EnergyPlus time steps per hour with `timestep=` and the exchange step
with `step_size=`:

```python
import pycosim as pc

EPW = "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"
office = pc.EnergyPlusComponent(
    "office", "examples/models/small_office.idf", EPW,
    sensors={"T_core": ("Zone Mean Air Temperature", "Core_ZN"),                    # average: 15-min mean
             "T_core_end": ("Zone Mean Air Temperature", "Core_ZN", "instant")},    # last 1-min value
    meters={"P_elec": ("Electricity:Facility", "W"),      # mean of the fifteen 1-min powers
            "E_elec": ("Electricity:Facility", "kWh")},   # sum of the fifteen 1-min energies
    actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")},
    timestep=60, step_size="15min",                       # EnergyPlus at 1 min, exchange every 15 min
    daylight_saving=False)

sim = pc.CoSimulation("2023-07-17", "2023-07-19", "15min", name="substeps")
sim.add(office)
sim.add(pc.FunctionComponent(lambda t: 24.0 if 7 <= t.hour < 19 else 29.0, name="schedule", outputs="clg_sp"))
sim.connect("schedule.clg_sp", "office.clg_sp")
res = sim.run()
print(res.meta["component_metadata"]["office"]["exchange_every_timesteps"])   # 15
```

Between two exchanges EnergyPlus runs on its own: actuator values are held, and outputs are
aggregated by their kind — rates (`average`) are averaged, energies (`amount`) are summed,
anything else (`instant`, `hold`) reports its last value. The exchange step must be a whole
multiple of the EnergyPlus step, and the master step must divide the exchange step.

EnergyPlus outputs are typed automatically:

| EnergyPlus output | `kind` | The value published at `t` |
|---|---|---|
| sensors (output variables) | `average` (`amount` if the unit is J), `interval="past"` | mean over `[t-h, t)` |
| meters in W or kW | `average`, `interval="past"` | mean power over `[t-h, t)` |
| meters in J, Wh, kWh or MJ | `amount`, `interval="past"` | energy used during `[t-h, t)` |
| internal variables | `hold` | the static value |

A sensor's kind can be overridden with a third element, `(variable, key, kind)`, or with
`{"variable": ..., "key": ..., "kind": ...}`.

**When to use it.** When an HVAC system or its controls need a short time step to behave
realistically, but the rest of the co-simulation (controllers, LLMs, the grid) only needs to
exchange data every 15 minutes or every hour.

**Pitfalls.**

* With sub-steps a sensor such as `Zone Mean Air Temperature` becomes the mean over the
  exchange step, not the temperature at its end. Declare it `"instant"` (as `T_core_end`
  above) if a controller needs the latest value.
* Sub-steps reduce the number of exchanges, not EnergyPlus' own work: a 1-minute EnergyPlus
  run takes about as long with a 15-minute exchange as with a 1-minute one.
* The internal step can change EnergyPlus' results noticeably (HVAC cycling, controls), so
  compare a finer and a coarser step on your own model before you choose.

## Limitations

* **YAML scenarios** can set `record:` and `trace:` in `simulation:`, patterns and topics as
  connection sources, and kinds, bounds, topics and read modes on the ports of *function*
  components (`outputs: {price: {default: 0.1, kind: hold, topic: grid/price}}`,
  `inputs: {P: {default: 0.0, read: past}}`). They cannot set `read:` on a connection, make a
  component asynchronous, or replay a component — use a short Python script for that (see
  [Scenarios and CLI](scenarios-and-cli.md#yaml-scenarios)).
* **Replay** requires the same clock (start and master step); this is not checked.
* **Inputs do not see quality flags**; they receive plain values.
* `res.df` shows values at the step they were *published*. To see validity intervals and
  flags, record the component and look at `res.messages` (columns `ref`, `index`, `step`,
  `time`, `value`, `start`, `end`, `quality`, `origin_step`).
