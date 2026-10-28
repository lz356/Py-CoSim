# Scenarios, the command line, observers and results

## YAML scenarios

A co-simulation can be described entirely in a YAML file and run without writing a Python
script. This is handy for parameter studies, for sharing set-ups with colleagues and for
keeping a record of exactly what was simulated.

```yaml
# scenario.yaml
simulation:
  name: precooling_demo
  start: 2023-07-17
  end: 2023-07-24
  step: 15min
  output_dir: output
  seed: 1

components:
  weather:
    type: EPWWeather
    path: ../weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw
  house:
    type: RCBuilding
    floor_area: 180
  tariff:
    type: TimeOfUseTariff
    peak_hours: [16, 21]
    peak_price: 0.30
    offpeak_price: 0.08
  pv:
    type: PVSystem
    capacity_kw: 6
  thermostat:
    function: my_rules:price_aware_thermostat     # a plain Python function
    outputs: [clg_sp]
  meter:
    type: GridMeter

connections:
  - weather.T_out -> house.T_out
  - weather.GHI -> house.GHI
  - tariff.price -> thermostat.price
  - house.T_zone -> thermostat.T_zone
  - thermostat.clg_sp -> house.clg_sp
  - {from: [house.P_elec, pv.P_ac], to: meter.P_net, reduce: my_rules:net_power}
  - tariff.price -> meter.price
```

Run it:

```bash
pycosim run scenario.yaml --plot
```

This is [example 08](examples.md#08--yaml-scenario) (with `my_rules.py` next to the YAML file).

### Schema

**`simulation:`** — keyword arguments of `CoSimulation`:

| Key | Default | Meaning |
|---|---|---|
| `start`, `end` | from the EnergyPlus model | simulation window, `end` exclusive (`2023-07-17`, `"2023-07-17 06:00"`) |
| `step` | from the EnergyPlus model | master step (`15min`, `1h`, `900`) |
| `name` | `cosim` | run name = name of the results folder |
| `output_dir` | `pycosim_runs` | results root; a relative path is relative to the YAML file |
| `seed` | `0` | random seed (`null` draws one and records it in `meta.json`) |
| `save` | `true` | write results to disk |
| `progress` | `false` | progress bar (the CLI shows one unless `--quiet`) |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

**`components:`** — a mapping `name: settings`. Each entry is one of:

* **a class**: `type: <ClassName>` plus the constructor's keyword arguments. `<ClassName>` is
  any class of `pycosim.components` (`EPWWeather`, `RCBuilding`, `PVSystem`, `Battery`,
  `HouseholdOccupancy`, …), `EnergyPlusComponent` (alias `EnergyPlus`), or your own class as
  `"module:ClassName"` (also `"package.module.ClassName"`, e.g. `pycosim.gym:ExternalAgent`).
* **a function**: `function: module:function` with optional `inputs`, `outputs` (default
  `[out]`) and `step_size`. No other keys are allowed.

Lists in YAML (e.g. `[16, 21]`) are passed where the Python API expects tuples. Nested
mappings become dicts, e.g. for an EnergyPlus building:

```yaml
  office:
    type: EnergyPlusComponent
    model: ../models/small_office.idf
    weather: ../weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw
    daylight_saving: false
    sensors: {T_zone: [Zone Mean Air Temperature, Core_ZN]}
    meters: {P_elec: [Electricity:Facility, W]}
    actuators: {clg_sp: [Schedule:Compact, Schedule Value, CLGSETP_SCH]}
```

**`connections:`** — a list; each item is either

* a string `"source.port -> target.port"`, or
* a mapping with `from` (one port or a list), `to`, and optionally `delay: true`,
  `reduce` (`sum`, `mean`, `max`, `min`, `list`, or the import path of your own function,
  `module:function` or `module.function`) and `transform` (an import path in the same form).

**Paths.** Relative values of the keys `path`, `model`, `weather` and `data` are resolved
against the YAML file's folder, and that folder is added to Python's import path, so
`my_rules.py` next to the YAML file can be imported as `my_rules`. You can therefore run a
scenario from any working directory.

**What YAML cannot express** (use a short Python script instead, see below): observers
(live plots, CSV streams), Python objects as arguments (LLM backends, `preprocess`
functions, pandas objects), lambdas, and `priority` of function components.

### Loading a scenario from Python

```python
from pycosim.config import load_scenario
from pycosim.observers import LivePlot

sim = load_scenario("examples/08_yaml_scenario/scenario.yaml", name="variant_b", seed=7)
sim["house"].default_clg = 25.0               # tweak a component before running
sim.add_observer(LivePlot([["house.T_zone", "thermostat.clg_sp"], "meter.cost"]))
res = sim.run(progress=True)
```

Keyword arguments of `load_scenario` override the `simulation:` section. A dictionary with
the same structure works too: `build_simulation(spec_dict, base_dir=".")`.

## The `pycosim` command line

| Command | What it does |
|---|---|
| `pycosim version` | print the installed version |
| `pycosim doctor` | check Python packages and find EnergyPlus installations ([Installation](installation.md#5-check-the-installation-pycosim-doctor)) |
| `pycosim handles MODEL -w EPW [options]` | list the sensors, meters and actuators a model offers ([EnergyPlus](energyplus.md#finding-the-right-names-pycosim-handles)) |
| `pycosim run SCENARIO.yaml [options]` | run a YAML scenario |

`pycosim handles` options: `--kind {actuator,variable,meter,internal_variable}`,
`--grep TEXT`, `--csv FILE`, `--max-rows N` (default 500), `--energyplus-dir DIR`.

`pycosim run` options:

| Option | Effect |
|---|---|
| `--name NAME` | override `simulation.name` (results go to a different folder) |
| `--plot` | show a quick plot of (up to six) numeric outputs at the end |
| `--quiet` | no progress bar |

It prints `results.summary()` and, when results are saved (`save: true`, the default), the
results folder. If you run `pycosim` and the command
is not found, activate the environment where Py-CoSim is installed, or use
`python -m pycosim.cli ...`.

## Observers: watching a run

Observers are notified after every master step without taking part in the simulation.
Add them before `run()`:

```python
from pycosim.observers import ProgressBar, LivePlot, CSVStreamRecorder, CallbackObserver
sim.add_observer(LivePlot([["office.T_core", "ctrl.clg_sp"], "office.P_elec"]))
```

### ProgressBar

```python
ProgressBar(stream=None, width=30, min_interval=0.5)
```

A text bar with simulated time, elapsed time and ETA (on standard error). `sim.run(progress=True)`
adds one for you.

### LivePlot — online visualisation

```python
LivePlot(panels, every=24, window=None, title="Py-CoSim live view")
```

* `panels`: one entry per sub-plot; each entry is a port or a list of ports drawn together,
  e.g. `[["office.T_core_zn", "schedule.clg_sp", "office.T_out"], "office.P_facility"]`.
* `every`: redraw every `every` master steps (drawing is the slow part; with 15-minute steps,
  `every=4` redraws once per simulated hour).
* `window`: show only the last `window` steps (a scrolling plot), e.g. `4 * 24 * 3` for three days.

In a script a plot window opens and updates while the simulation runs. To keep it open at
the end:

```python
import matplotlib.pyplot as plt
res = sim.run()
plt.ioff(); plt.show()
```

In **Jupyter**, install `ipympl` (`pip install ipympl`) and run `%matplotlib widget` in the
notebook before creating the simulation; the figure then updates inside the notebook.
Live plotting costs time — use it to watch and debug, and switch it off for production runs.

### CSVStreamRecorder

```python
CSVStreamRecorder(path, refs=None, every=1)
```

Appends the latest values of `refs` (default: all output ports) to a CSV file while the
simulation runs (flushed every 100 writes). Useful for very long runs: you can watch the file
from another program, and the data survive if the process is killed.

### CallbackObserver and your own observers

```python
def report(sim, k):
    print(sim.now, round(sim.bus.latest("house.T_zone"), 2))

sim.add_observer(CallbackObserver(report, every=96))     # once per simulated day at 15 min
```

For more control subclass `pycosim.observers.Observer` and override any of
`on_start(sim)`, `on_step(sim, k)` and `on_end(sim, results)` (`on_end` is also called after
a failure, with partial results or `None`).

## Results

`sim.run()` returns a `pycosim.Results` object.

| Member | Content |
|---|---|
| `res.df` | pandas DataFrame indexed by time (`time`), one column per output port `"comp.port"`; slower components' values are held between their updates |
| `res["comp.port"]`, `"comp.port" in res` | column access and membership |
| `res.events` | event log DataFrame: `time`, `source`, `kind`, `message` + extra columns |
| `res.timing` | per component: `calls`, `total_s`, `mean_ms` |
| `res.meta` | dict: `name`, `start`, `end`, `step`, `step_s`, `n_steps`, `completed_steps`, `seed`, `wall_time_s`, `setup_time_s`, `components`, `connections`, `pycosim_version`, `python`, `platform`, and `component_metadata` (e.g. EnergyPlus version and warnings, LLM statistics) |
| `res.units` | `{"comp.port": unit}` |
| `res.ok`, `res.error` | `True` / `None` unless the run failed (then `error` holds the error report) |
| `res.wall_time` | wall-clock seconds |
| `res.summary()` | short text summary (also what `print(res)` shows) |

Methods:

```python
office = res.component("office")          # outputs of one component, columns = port names
hourly = res.resample("1h")               # numeric columns, hourly means
daily_max = res.resample("1D", how="max")
fig = res.plot(["office.T_core", "ctrl.clg_sp"], "office.P_elec",
               title="Office", resample="1h")   # one panel per argument; returns the figure
fig.savefig("office.png", dpi=150)
res.save("my_results/")                    # write the files below to any folder
res2 = pc.Results.load("pycosim_runs/office_demo")        # read them back later (import pycosim as pc)
```

`res.plot()` without arguments plots up to six numeric columns. Typical post-processing:

```python
h = res.meta["step_s"] / 3600                                 # step in hours
energy_kwh = res["office.P_elec"].sum() * h / 1000            # P in W -> kWh
cost = (res["office.P_elec"] / 1000 * res["tariff.price"]).sum() * h
peak_kw = res["office.P_elec"].max() / 1000
```

### Files on disk

With `save=True` (default), `<output_dir>/<name>/` contains:

| File | Content |
|---|---|
| `timeseries.csv` | `res.df` |
| `events.csv` | `res.events` |
| `timing.csv` | `res.timing` |
| `meta.json` | `res.meta` plus the units of all ports |
| `error_report.txt` | only if the run failed |
| `<component>/` | files written by components (e.g. EnergyPlus' `eplus/` folder) |

Running again with the same `name` overwrites the files — give each variant its own `name`.
`Results.load` restores `df`, `events`, `timing`, `meta`, `units` and `error`, so plots of
loaded results show units as before.

With `save=False` nothing is written and the run folder is not created up front; only
components that need a working folder create their own sub-folder on demand (e.g.
EnergyPlus' `<output_dir>/<name>/<component>/eplus/`), and an `error_report.txt` is still
written if the run fails.

### Logging

`CoSimulation(..., log_level="INFO")` sets the level of the `pycosim` logger: `"WARNING"`
hides the start/finish messages, `"DEBUG"` shows more detail, `None` leaves Python's logging
configuration untouched.
