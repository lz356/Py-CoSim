# EnergyPlus in the loop

`EnergyPlusComponent` turns an EnergyPlus model (`.idf` or `.epJSON`) into a co-simulation
component. EnergyPlus runs through its official Python API, one time step at a time, in
lock-step with the master clock. You never write callback functions or look up handles
yourself: you list what you want to read and write, and Py-CoSim does the rest.

## A first example

```python
import pycosim as pc
from pycosim.components import ThermostatSchedule

office = pc.EnergyPlusComponent(
    "office",
    model="examples/models/small_office.idf",
    weather="examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw",
    sensors={
        "T_core": ("Zone Mean Air Temperature", "Core_ZN"),
        "T_out": ("Site Outdoor Air Drybulb Temperature", "Environment"),
    },
    meters={"P_elec": ("Electricity:Facility", "W")},
    actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")},
    daylight_saving=False,          # Arizona has no DST; keeps schedules in standard time
)

sim = pc.CoSimulation(start="2023-07-17", end="2023-07-24", step="15min", name="office_demo")
sim.add(office, ThermostatSchedule("schedule", occupied_hours=(7, 19)))
sim.connect("schedule.clg_sp", "office.clg_sp")
res = sim.run(progress=True)
res.plot(["office.T_core", "schedule.clg_sp"], "office.P_elec")
```

Sensors, meters and internal variables become **outputs** of the component; actuators
become **inputs**. EnergyPlus is a non-feedthrough component (see
[Concepts](concepts.md#feedthrough-and-non-feedthrough-components)), so a controller can read
its outputs and drive its actuators without any special care.

## Parameters

```python
pc.EnergyPlusComponent(name, model, weather, *, sensors=None, meters=None, actuators=None,
                       internal_variables=None, timestep=None, step_size=None, sync_run_period=True,
                       sizing="model", daylight_saving=None, preprocess=None,
                       executor="process", energyplus_dir=None, verbose=False,
                       timeout=600.0, extra_args=None)
```

| Parameter | Default | Meaning |
|---|---|---|
| `name` | — | component name |
| `model` | — | path to an `.idf` / `.epJSON` file, or an `EnergyPlusModel` (it is copied) |
| `weather` | — | path to an EPW file |
| `sensors` | `None` | `{output: (variable_name, key)}` — EnergyPlus output variables |
| `meters` | `None` | `{output: meter_name}` or `{output: (meter_name, unit)}` |
| `actuators` | `None` | `{input: (component_type, control_type, key)}` — EMS actuators |
| `internal_variables` | `None` | `{output: (variable_name, key)}` — static values such as floor area |
| `timestep` | `None` | EnergyPlus time steps per hour (its *internal* step); by default derived from the exchange step |
| `step_size` | `None` | exchange step with the co-simulation (default: the master step, or `3600 / timestep` s); may be a multiple of the EnergyPlus step ([sub-steps](#internal-sub-steps-timestep-and-step_size)) |
| `sync_run_period` | `True` | rewrite the model's RunPeriod to match the co-simulation window |
| `sizing` | `"model"` | `"weather_file"` sizes HVAC from the EPW file's extreme periods |
| `daylight_saving` | `None` | `False` removes DST from the model; `None` keeps it (and logs a note) |
| `preprocess` | `None` | function `preprocess(model)` to edit the model before it runs |
| `executor` | `"process"` | `"process"` (own worker process) or `"thread"` (in this process) |
| `energyplus_dir` | `None` | EnergyPlus installation folder (auto-detected otherwise) |
| `verbose` | `False` | write EnergyPlus' console output to the worker log |
| `timeout` | `600.0` | seconds to wait for EnergyPlus at any single exchange |
| `extra_args` | `None` | extra EnergyPlus command-line arguments, e.g. `["-r"]` (ReadVarsESO) |

## Sensors, meters, internal variables and actuators

### Sensors (output variables)

```python
sensors={
    "T_core": ("Zone Mean Air Temperature", "Core_ZN"),                   # zone variable
    "T_out":  ("Site Outdoor Air Drybulb Temperature", "Environment"),     # site variable
    "Q_cool": ("Zone Air System Sensible Cooling Rate", "Perimeter_ZN_1"),
}
```

* `variable_name` is the EnergyPlus output variable name, `key` the zone/object name, or
  `"Environment"` for site variables. Names are case-insensitive but must otherwise match.
* A plain string means key `"Environment"`: `"T_out": "Site Outdoor Air Drybulb Temperature"`.
* A dict also works: `{"variable": "Zone Mean Air Temperature", "key": "Core_ZN"}`.
* You do **not** need `Output:Variable` objects in the model; Py-CoSim requests the variables.
* Units are guessed from the name for plots (`Temperature` → C, `Rate`/`Power` → W, …).
* Every sensor is published as `kind="average"` with `interval="past"` (`"amount"` if its
  unit is J): EnergyPlus reports time-step values as means (or sums) over the time step that
  just ended. Give a different [kind](tide.md#the-five-kinds-what-a-time-stamp-means) as a
  third element, `("Zone Mean Air Temperature", "Core_ZN", "instant")`, or as
  `{"variable": ..., "key": ..., "kind": "instant"}`. This matters for aligned reads and
  [sub-steps](#internal-sub-steps-timestep-and-step_size); `latest` readers get the same
  value either way.

### Meters

Meters (e.g. `Electricity:Facility`, `Electricity:HVAC`, `Cooling:Electricity`,
`Cooling:EnergyTransfer:Zone:<zone>`) report energy per time step. Choose the unit:

| Unit | Value published at `t` | Kind |
|---|---|---|
| `"J"` (default), `"Wh"`, `"kWh"`, `"MJ"` | energy used during the interval `[t-dt, t)` | `amount`, `interval="past"` |
| `"W"`, `"kW"` | **average power** over the interval `[t-dt, t)` | `average`, `interval="past"` |

```python
meters={
    "E_elec":   "Electricity:Facility",                 # J per time step
    "P_elec":   ("Electricity:Facility", "W"),          # average W
    "P_hvac":   ("Electricity:HVAC", "kW"),             # average kW
    "E_cool":   {"meter": "Cooling:Electricity", "unit": "kWh"},
}
```

Any other unit (in any of the three forms) raises a `ConfigurationError` when the component
is created.

### Internal variables

Static model data, read once at the start and published every step (as `kind="hold"`):

```python
internal_variables={"A_core": ("Zone Floor Area", "Core_ZN")}       # m2
```

### Actuators

```python
actuators={
    "clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH"),      # override a schedule
    "htg_sp": ("Schedule:Compact", "Schedule Value", "HTGSETP_SCH"),
    "sp_GN1": ("Zone Temperature Control", "Cooling Setpoint", "G N1 Apartment"),  # one zone
    "n_GN1":  ("People", "Number of People", "OCCUP_GN1"),                # occupant count
}
```

* The three strings are `(component_type, control_type, key)` exactly as EnergyPlus lists
  them (a dict `{"component_type": ..., "control_type": ..., "key": ...}` also works).
* For a schedule actuator, `component_type` must match the schedule's object type in the
  model (`Schedule:Compact`, `Schedule:Year`, `Schedule:Constant`, …).
* An input name cannot also be used as a sensor/meter name.

### Releasing an actuator with `None`

An actuator input that is `None` (or NaN) is **released**: EnergyPlus' own schedule or
controller takes over again for that time step. Unconnected actuators are `None` by default,
so they are released for the whole run. A controller can hand control back temporarily:

```python
@pc.component(outputs="clg_sp")
def override_peak_only(t):
    return 26.5 if 16 <= t.hour < 21 else None     # outside the peak: model's own schedule
```

## Finding the right names: `pycosim handles`

Exact EnergyPlus names are the most common stumbling block. Py-CoSim runs your model for
one day and lists everything it exposes:

```bash
pycosim handles examples/models/small_office.idf -w examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw --kind actuator --grep setp
```

Options: `--kind {actuator,variable,meter,internal_variable}`, `--grep TEXT`
(case-insensitive filter), `--csv FILE` (save the full table), `--max-rows N`,
`--energyplus-dir DIR`. Each row shows a `spec` you can paste directly into
`sensors=`, `meters=` or `actuators=`.

The same from Python, as a pandas DataFrame with columns `kind`, `name`, `control`, `key`,
`unit`, `spec`:

```python
from pycosim.energyplus import list_available_handles

df = list_available_handles("examples/models/small_office.idf",
                            "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw")
df[(df.kind == "actuator") & df.key.str.contains("SETP")]
df[(df.kind == "variable") & (df.key == "Core_ZN")]
```

`kind` is one of `actuator`, `variable`, `meter`, `internal_variable` (plus
`plugin_global`/`plugin_trend` for models with Python plugins).

## Clock, run period and time step

Py-CoSim makes EnergyPlus follow the master clock:

* **Time step.** The model's `Timestep` is set from the component's step: a 15-minute master
  step gives 4 time steps per hour. EnergyPlus allows 1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30
  or 60 steps per hour, so the step must be 60, 30, 20, 15, 12, 10, 6, 5, 4, 3, 2 or 1
  minutes. Note that this **overrides** the model's own time step (a 1-hour master step
  makes EnergyPlus run hourly).
* **`timestep=`** fixes the EnergyPlus time step independently of the master, e.g.
  `timestep=4` with a 5-minute master step: EnergyPlus then exchanges data every 15 minutes
  (a multi-rate component), while other components run every 5 minutes. The exchange step
  must be a multiple of the master step.
* **`timestep=` with `step_size=`** lets EnergyPlus run several internal steps per exchange,
  e.g. `timestep=60, step_size="15min"` (see [below](#internal-sub-steps-timestep-and-step_size)).
* **`sync_run_period=True`** (default) replaces all RunPeriods with one covering the
  co-simulation window (first day to last day, inclusive) **with the calendar year written
  explicitly**, so weekdays and holidays in EnergyPlus follow the same calendar as `t` in
  Python. If `start` is not at midnight, EnergyPlus starts at midnight and Py-CoSim skips
  forward to `start`. If the window ends before the end of its last day, EnergyPlus is
  stopped when the master reaches `end`.
* **`sync_run_period=False`** keeps the model's RunPeriod. The clocks must then agree, or you
  get a `SynchronizationError`. Give the RunPeriod a begin year so weekdays match.
* **Clock taken from the model.** If you create `CoSimulation()` without `start`, `end` or
  `step`, they are taken from the first EnergyPlus component: the first RunPeriod (from its
  begin day to the end of its end day) and the model's time steps per hour (or `timestep=`):

  ```python
  sim = pc.CoSimulation(name="full_year")    # window and step from the model
  sim.add(pc.EnergyPlusComponent("bldg", "model.idf", "weather.epw", sensors={...}))
  ```

Sizing periods and warm-up days run before the co-simulation starts and are not exchanged.

### Internal sub-steps: `timestep` and `step_size`

```python
office = pc.EnergyPlusComponent(
    "office", "examples/models/small_office.idf",
    "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw",
    sensors={"T_core": ("Zone Mean Air Temperature", "Core_ZN")},
    meters={"P_elec": ("Electricity:Facility", "W")},
    actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")},
    timestep=60, step_size="15min",          # EnergyPlus at 1 min, exchange every 15 min
    daylight_saving=False)
```

EnergyPlus then runs fifteen 1-minute steps between two exchanges. Actuator values are held
over all of them, and each output is aggregated by its kind: `average` values (sensors, W/kW
meters) are averaged, `amount` values (J/kWh meters) summed, anything else reports its last
value. `res.meta["component_metadata"]["office"]["exchange_every_timesteps"]` shows the ratio
(15 here). Rules: the exchange step must be a whole multiple of `3600 / timestep` seconds, and
a multiple of the master step.

Use it when the HVAC system or its controls need a short time step but the rest of the
co-simulation does not. Two things to keep in mind: a sensor such as `Zone Mean Air
Temperature` is then the *mean* over 15 minutes (declare it `"instant"` to get the last
1-minute value), and the run is not much faster than a 1-minute exchange, because EnergyPlus
still computes every minute. More in [Messages and time](tide.md#energyplus-internal-sub-steps).

## Sizing from the weather file: `sizing="weather_file"`

Prototype models are sized with design days for their original climate. To co-simulate a
model in another climate consistently, use

```python
pc.EnergyPlusComponent(..., sizing="weather_file")
```

This replaces the design days by the summer and winter extreme periods of the EPW file
(`SizingPeriod:WeatherFileConditionType`) and turns off the separate sizing-period
simulation. Example 06 uses it for a Denver apartment prototype simulated in Tucson.

## Daylight saving time: `daylight_saving`

The co-simulation clock is local standard time. If the model has a
`RunPeriodControl:DaylightSavingTime` object, EnergyPlus shifts its schedules by one hour in
summer, so the model's schedules and your Python controllers disagree by an hour.

| Value | Effect |
|---|---|
| `None` (default) | model unchanged; an INFO log message tells you if DST is defined |
| `False` | DST removed: schedules, weather and the Python clock all in standard time (**recommended**) |
| `True` | model unchanged (use only if you deliberately keep DST and account for the shift) |

See [Concepts](concepts.md#time-convention) for the background.

## Editing the model: `preprocess` and `EnergyPlusModel`

`EnergyPlusModel` holds a model as an epJSON dictionary. IDF files are converted with
EnergyPlus' own converter (`energyplus --convert-only`) and cached, so every field has its
official name.

```python
from pycosim import EnergyPlusModel

model = EnergyPlusModel.load("examples/models/small_office.idf")
model.set_timesteps_per_hour(4)
model.set_run_period("2023-07-17", "2023-07-23")      # inclusive days, explicit year
model.use_weather_file_sizing()                       # size HVAC for the EPW climate
model.remove_daylight_saving()
model.scale_floor_area(1.08)                          # +8 % floor area (x/y stretch)
model.add_output_variable("Zone Mean Air Temperature", key="*", frequency="Timestep")
model.add_output_meter("Electricity:Facility", frequency="Hourly")
model.enable_sqlite()                                 # eplusout.sql
model.save("office_variant.epJSON")
```

| Member | Purpose |
|---|---|
| `EnergyPlusModel.load(path)` | read `.idf` (needs EnergyPlus for conversion) or `.epJSON` |
| `save(path)` | write epJSON (EnergyPlus runs epJSON directly) |
| `copy()` | independent deep copy |
| `data` | the raw epJSON dict `{class: {object_name: {field: value}}}` — edit freely |
| `classes()`, `objects(cls)`, `names(cls)`, `get(cls, name)` | browse objects |
| `add(cls, name, **fields)`, `remove(cls, name=None)`, `set_field(cls, name, field, value)` | edit objects |
| `zone_names`, `version`, `timesteps_per_hour`, `has_daylight_saving` | information |
| `set_timesteps_per_hour(n)`, `get_run_period()`, `set_run_period(first, last)` | time settings |
| `use_weather_file_sizing(summer="SummerExtreme", winter="WinterExtreme")` | sizing from the EPW |
| `remove_daylight_saving()` | delete DST |
| `set_simulation_control(**fields)` | e.g. `run_simulation_for_sizing_periods="No"` |
| `add_output_variable(variable, key="*", frequency="Timestep")`, `add_output_meter(meter, frequency="Timestep")`, `enable_sqlite()` | extra EnergyPlus output files |
| `scale_floor_area(factor)` | stretch the plan by √factor in x and y (detailed geometry only) |

Pass a model object directly (`EnergyPlusComponent("bldg", model, epw)`), or edit the
component's model just before it runs with `preprocess`:

```python
from functools import partial

def scale(factor, model):
    model.scale_floor_area(factor)

for i, f in enumerate([0.92, 1.0, 1.07]):          # three office variants
    sim.add(pc.EnergyPlusComponent(
        f"bldg{i:02d}", "examples/models/small_office.idf",
        "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw",
        meters={"P_elec": ("Electricity:Facility", "W")},
        preprocess=partial(scale, f),               # partial(...) fixes the first argument
    ))
```

`preprocess(model)` is called inside `setup`, **after** Py-CoSim has set the time step, run
period, sizing and DST. Do not change `Timestep` or `RunPeriod` there, or the clocks will no
longer match. Field names follow the epJSON schema (lower case with underscores, e.g.
`"begin_month"`); `model.get("RunPeriod")` shows an example object.

## Executors: where EnergyPlus runs

| `executor=` | How it works | Use it for |
|---|---|---|
| `"process"` (default) | each EnergyPlus building runs in its **own worker process** (`python -m pycosim.energyplus.worker`) | everything, and always for more than one building |
| `"thread"` | EnergyPlus runs in a background thread of your Python process | a single, one-off building run when you want minimal start-up overhead |

Why processes? **EnergyPlus is not thread-safe.** Its library keeps global state, so two
EnergyPlus simulations in threads of one Python process interfere with each other and
typically deadlock (this was the main reliability problem of Py-CoSim 0.x). Separate
processes give:

* **isolation** — each building has its own EnergyPlus; a crashing model cannot take the
  master or other buildings down;
* **parallelism** — all buildings size their HVAC and warm up at the same time before the
  first step, and simulate their interval simultaneously during phase 3 of each master step,
  on as many CPU cores as you have;
* **no `if __name__ == "__main__":` guard needed**, and it works from Jupyter notebooks.

**Limits of `executor="thread"`.** EnergyPlus (tested with 22.2) can run only *once* per
Python process: a second simulation in the same process — even after the first one has
finished, and even with a fresh EnergyPlus state — stalls at start-up. Py-CoSim therefore
uses the thread executor only for the first EnergyPlus run in a process. Any later
`executor="thread"` component (a second building in the same co-simulation, or a new run in
the same script or Jupyter kernel) automatically switches to `executor="process"` and logs a
warning (`EnergyPlus already ran in this Python process and cannot be started in-process
again; using executor='process' instead.`). The executor actually used is recorded in
`res.meta["component_metadata"]`.

## Where the files go

For a run named `name` in `output_dir`, each EnergyPlus component gets a folder:

```text
<output_dir>/<name>/
├── timeseries.csv  events.csv  timing.csv  meta.json     (results of the whole run)
└── <component>/
    ├── in.epJSON          the exact model that was simulated (after all edits)
    ├── worker.log         console of the worker process (process executor)
    └── eplus/             EnergyPlus' own output: eplusout.err, .eso, .sql, eplustbl.htm, ...
```

`res.meta["component_metadata"]["<component>"]` records the EnergyPlus version, time steps
per hour, number of warnings and severe errors, exit code, executor, model and weather
paths, and the worker's wall time.

## Troubleshooting

### `HandleNotFoundError`

Raised at start-up when a sensor, meter, internal variable or actuator does not exist in
the model. The message lists every missing handle with close matches:

```text
office: EnergyPlus could not find the following handles:
  - actuator 'clg_sp': ('Schedule:Compact', 'Schedule Value', 'CLGSETP_SCHED')
      did you mean: <up to three similar entries from EnergyPlus' own list>
Variable/key names are case-insensitive but must otherwise match EnergyPlus exactly.
List everything available with:  pycosim handles <model> -w <weather.epw>
```

The suggestions are copied from EnergyPlus' catalogue, so they show the exact spelling to
use. The exception's `.missing` attribute lists the missing handles. Fix the name, or run
`pycosim handles` to see all options. Note that meters only exist if
the model contains equipment of that kind (no `Gas:Facility` in an all-electric model).

### `SynchronizationError`

EnergyPlus' clock and the master clock disagree, e.g.

```text
office: EnergyPlus is at 07-18 00:00 but the master clock is at 07-17 00:15. Check the
RunPeriod/time step or use sync_run_period=True.
office: EnergyPlus reached the end of its RunPeriod at ... before the master clock ...
```

Use `sync_run_period=True` (the default), or make the model's RunPeriod and Timestep cover
the co-simulation window exactly. Do not change the Timestep or RunPeriod in `preprocess`.

### Version mismatch between the IDF and your EnergyPlus

Every IDF file is written for one EnergyPlus version (its `Version` object). When it
differs from the installation that Py-CoSim uses, loading the model fails with a
`ModelError` such as

```text
ModelError: Model small_office.idf is EnergyPlus version 22.2 but the installation is
24.1.0. Update the model with EnergyPlus' IDFVersionUpdater (in
C:\EnergyPlusV24-1-0\PreProcess\IDFVersionUpdater) or install EnergyPlus 22.2.
```

(or with `EnergyPlus could not convert ... to epJSON` followed by EnergyPlus' version
message, if the converter itself rejects the file). Either convert the model with
**IDFVersionUpdater** (a GUI that ships with EnergyPlus and transitions a file one version
at a time), or install the matching EnergyPlus version and select it with the
`ENERGYPLUS_DIR` environment variable or `energyplus_dir=` (see
[Installation](installation.md#how-py-cosim-finds-energyplus)).

### EnergyPlus errors during the run

`EnergyPlusRuntimeError` (from `pycosim.energyplus.component`) is raised when EnergyPlus
stops abnormally (fatal error in the model, worker crash, no answer within `timeout`). The
message contains the end of `eplusout.err` and of `worker.log`. Look at:

1. `<component>/eplus/eplusout.err` — EnergyPlus' own warnings and severe/fatal errors;
2. `<component>/worker.log` — Python errors in the worker process (start with
   `verbose=True` to also capture EnergyPlus' console output there);
3. `<output_dir>/<name>/error_report.txt` — what the master was doing at the time.

A run that finishes but reports severe errors logs a warning
(`EnergyPlus reported N severe errors (see .../eplusout.err)`); `meta.json` contains the
counts.

### It is slow to start

Each run converts IDF files (cached after the first time), sizes the HVAC system and runs
warm-up days before the first co-simulation step — typically a few seconds per building.
Sizing and warm-up of all buildings run in parallel (each `EnergyPlusComponent` launches its
worker in `setup()` and the master waits for all of them in `ready()`), so with enough CPU
cores ten buildings start about as fast as one. The model conversion and edits in `setup()`
are done one building at a time, so save a converted model with `EnergyPlusModel.save(...)`
and use the `.epJSON` file when you start many copies.

More in the [FAQ](faq.md).
