# FAQ and troubleshooting

## Installation and EnergyPlus

### EnergyPlus not found

`pycosim doctor` says `EnergyPlus: none found`, or you get `EnergyPlusNotFoundError: Could not
find an EnergyPlus installation`.

1. Is EnergyPlus **9.4 or newer** installed? Older versions have no Python API.
2. Is it in a standard place (`C:\EnergyPlusV22-2-0`, `/Applications/EnergyPlus-22-2-0`,
   `/usr/local/EnergyPlus-22-2-0`)? If not, set the `ENERGYPLUS_DIR` environment variable to
   the folder that contains `energyplus(.exe)` and `Energy+.idd`, and open a new terminal.
3. Or pass the folder directly: `EnergyPlusComponent(..., energyplus_dir="D:/Tools/EnergyPlusV22-2-0")`.
4. `doctor` finds it but says `no Python API` or `pyenergyplus import FAILED`: reinstall
   EnergyPlus, and on macOS check that EnergyPlus and Python have the same CPU architecture
   ([Installation](installation.md#macos)).

### Py-CoSim uses the wrong EnergyPlus version

With several versions installed and no environment variable set, the newest one with a
Python API is used. To pin a version, set `ENERGYPLUS_DIR` (or `PYCOSIM_ENERGYPLUS_DIR`) to
its folder — this applies to scripts, notebooks and `pycosim handles` alike — or pass
`energyplus_dir=` to an individual `EnergyPlusComponent` (`--energyplus-dir` for
`pycosim handles`), which takes priority over everything else
([details](installation.md#how-py-cosim-finds-energyplus)).

### "Model ... is EnergyPlus version 22.2 but the installation is 24.1"

The IDF file and your EnergyPlus differ. Either upgrade the file with **IDFVersionUpdater**
(in `<EnergyPlus folder>/PreProcess/IDFVersionUpdater`), or install the matching EnergyPlus
version and select it with `ENERGYPLUS_DIR` or `energyplus_dir=`. The example models are
EnergyPlus 22.2.

## Actuators and control

### My actuator does nothing

Work through this list:

1. **Is the input connected and not `None`?** An unconnected actuator, or one that receives
   `None`/NaN, is *released* and EnergyPlus' own schedule applies. Inputs are not recorded in
   the results, so check the output of the component that drives it
   (e.g. `res["ctrl.clg_sp"]`) and `sim.describe()` for the connection.
2. **Is it the right actuator?** For a schedule, `component_type` must be the schedule's
   object type in the model (`Schedule:Compact`, `Schedule:Year`, `Schedule:Constant`, …),
   and the schedule must actually be the one the thermostat uses. Look at the model's
   `ThermostatSetpoint:DualSetpoint` (or similar) object to see which schedules are used.
   List the options with `pycosim handles <model> -w <epw> --kind actuator --grep <name>`.
3. **Schedule vs. zone actuator.** Overriding a set-point *schedule* changes every zone that
   uses that schedule. To control one zone, use
   `("Zone Temperature Control", "Cooling Setpoint", "<zone name>")` (or `"Heating
   Setpoint"`), as example 06 does.
4. **Is the HVAC system on?** If its availability schedule switches the system off (nights,
   weekends), set-point changes have no effect then.
5. **Units and plausibility.** Set-points are in °C. A heating set-point above the cooling
   set-point, or values outside what the equipment can reach, are not followed.
6. **Daylight saving time.** If the effect seems to be shifted by one hour in summer, set
   `daylight_saving=False` ([Concepts](concepts.md#time-convention)).
7. **Timing.** The effect of a value sent at `t` appears at `t + dt` (next question).

### Values look one step late

That is the expected, physically correct timing. At master time `t` an EnergyPlus output
carries the value EnergyPlus reports with time stamp `t` — the result of the interval
`[t-dt, t)` that just ended. An actuator value set at `t` acts on the next interval `[t, t+dt)`.
So in `res.df`, a set-point change in row `t` shows up in temperature and power in row
`t + dt`. Meters in W/kW are average powers over the interval *ending* at `t`. The values are
identical to EnergyPlus' own output files row by row. Details and a diagram:
[Concepts](concepts.md#timing-of-energyplus-values).

If you see a delay of a whole *extra* step, check for `delay=True` on a connection, a
`read="past"` connection (it reads the step that just ended, on purpose), an asynchronous
component (its answers apply `delay` later), or a component that reads another one through
`ctx.latest(...)` instead of a connection (then it may run first and see the previous value).

### My costs changed after updating Py-CoSim

`GridMeter` now reads its `price` (and its new `P_load`, `P_gen`, `P_bat` inputs) as the mean
over the step that just ended (`aligned=True`), so energy is priced with the tariff of its
own interval. For an EnergyPlus building connected to `P_net` the cost becomes *more*
accurate (in our tests it moved by about 1–2 % for a week at 15-minute steps). If you added
power from a battery or another feedthrough component to `P_net`, connect it to `P_bat` or
`P_load` instead ([GridMeter](component-library.md#gridmeter)); `GridMeter(name,
aligned=False)` restores the old behaviour exactly. If a connection to the meter's `price`
has `delay=True`, you now get a `ConfigurationError`: remove `delay=True`.

### A battery/controller loop gives `AlgebraicLoopError`

Two feedthrough components depend on each other at the same time step. Add `delay=True` to
one connection of the loop, typically the measurement (e.g. `battery.soc → dispatch.soc`), or
read a set-point/command that is valid from `t` on with `read="past"`.
See [Concepts](concepts.md#algebraic-loops-and-how-to-break-them).

## Running and performance

### How many buildings can I run?

Each EnergyPlus building is one worker process. All buildings advance in parallel within
each master step, so with up to as many buildings as CPU cores the wall time is close to that
of a single building plus a small overhead; beyond that, buildings share the cores and the
time grows roughly linearly. Each worker needs memory — typically tens to a few hundred MB
depending on the model size (watch Task Manager / `top` during a short test run). In practice
the limit is your RAM and cores: tens of buildings on a laptop, more on a workstation or
server. Example 06 runs 4 apartment buildings (92 dwellings) in one co-simulation; example 04
runs 10 offices by default. Start-up is parallel too: all EnergyPlus workers are launched
first and size their HVAC systems and run their warm-up days at the same time; only the
quick model preparation (IDF conversion, cached after the first run, and model edits) is
done one building after the other.

### The run is slow

* `res.timing` (and `timing.csv`) shows where the time goes, per component.
* Live plots are slow: increase `every`, or remove the observer.
* An LLM backend waits for every answer; use a longer `step_size`, make the supervisor
  asynchronous (`make_async(delay="15min")`, so the simulation goes on while it thinks and
  several supervisors think at once), or record once and replay
  ([Messages and time](smmm.md#asynchronous-components)).
* `trace=True` and `record=True` keep every message; switch them off for long production runs.
* EnergyPlus models with many zones/HVAC objects or a small time step are slow on their own;
  a 15-minute step is usually a good compromise.
* Python components are called at every master step; use `step_size` for slow logic.

### The run hangs

A single exchange with EnergyPlus waits up to `timeout` (default 600 s) before failing.
Look at `<output_dir>/<name>/<component>/worker.log` and `eplus/eplusout.err`. Press Ctrl+C to
stop; Py-CoSim then terminates the worker processes and writes the partial results.

### Why did `executor="thread"` switch to a worker process?

EnergyPlus (tested with 22.2) cannot run a second simulation in the same Python process —
it stalls at start-up, even after the first run has finished. Py-CoSim therefore runs only
the first EnergyPlus simulation of a process in-thread and moves later ones to worker
processes automatically, with a warning. Results are the same; only start-up overhead
differs. See [Executors](energyplus.md#executors-where-energyplus-runs).

### Where are my results?

In `<output_dir>/<name>/` — by default `pycosim_runs/<name>/` relative to the folder you
started Python in (`sim.output_path` tells you). Running again with the same `name`
overwrites them. With `save=False` no results are written; components that need a working
folder (e.g. EnergyPlus, for its `eplus/` files) still create their own sub-folder there.

### Can I run without EnergyPlus?

Yes. `RCBuilding` is a fast 2R2C single-zone model with the same interface and timing, and
PV, batteries, tariffs, controllers, occupancy, LLM and RL tools need no EnergyPlus.

### Can each building have its own weather file, model or time step?

Weather and model: yes, they are per-component parameters. Time step: every EnergyPlus
component follows the master step by default; with `timestep=` a building can exchange less
often (a multiple of the master step), and with `timestep=` plus `step_size=` it can run a
finer internal step than it exchanges, e.g. `timestep=60, step_size="15min"`
([sub-steps](energyplus.md#internal-sub-steps-timestep-and-step_size)). Components that read
a building with another step get correctly averaged values with `read="past"`
([alignment](smmm.md#alignment-across-step-sizes)).

## Jupyter notebooks

Py-CoSim works in Jupyter, including EnergyPlus (workers are separate processes, no
`if __name__ == "__main__":` needed).

* **Live plots:** `pip install ipympl`, then `%matplotlib widget` at the top of the notebook.
  (`%matplotlib notebook` from 0.x does not work in JupyterLab.)
* **Build a fresh simulation in each run.** Components keep internal state (a battery's SOC,
  a controller's integral), so create them inside a `build()` function and call it for every
  run rather than re-running `sim.run()` on the same objects.
* The progress bar works (`sim.run(progress=True)`); `res` displays its summary when it is the
  last expression of a cell.
* Prefer the default `executor="process"`. EnergyPlus can run only once inside one Python
  process, so with `executor="thread"` only the first EnergyPlus run in a kernel actually
  runs in-thread; every later one automatically switches to a worker process and logs a
  warning (`... cannot be started in-process again; using executor='process' instead`).
  Restarting the kernel resets this.

## Data, time and units

### Which time zone and daylight saving time does Py-CoSim use?

Local standard time without DST, like EPW files and EnergyPlus' internal clock. Timestamps are
naive `datetime`s. Use `daylight_saving=False` on EnergyPlus components unless you study DST
on purpose. See [Concepts](concepts.md#time-convention).

### Are inputs recorded in the results?

Not as time series, only outputs. To see what a component received, look at the output of
its source (with the default `read="latest"` the values are identical), or record it yourself
with `ctx.log` or an extra output. With `CoSimulation(..., trace=True)`, `res.trace` lists
which messages every input read at every step, and `res.lineage(...)` follows a value back
through them ([provenance](smmm.md#provenance-trace-and-lineage)).

### Is my data sent anywhere?

Only if you use a cloud LLM backend (`AnthropicBackend`, `OpenAIBackend` with a cloud
`base_url`): the prompt — your intent text and the observations you selected — is sent to that
provider. `OllamaBackend`, `OfflineStandInBackend` and everything else run locally.

### How do I make runs reproducible?

Set `seed=` on `CoSimulation` (or `seed=None` for a random seed — it is recorded in
`meta.json`, so you can repeat the run later), use `self.ctx.rng` for randomness in your
components, and keep the `meta.json` of each run (versions, seed, connections). For
components you cannot seed — LLMs, web services, other people's models — record their
messages (`CoSimulation(..., record=["llm"])`) and replay them (`sim.replay("llm",
run_folder)`): the replay is identical bit for bit
([Record and replay](smmm.md#record-and-replay)). `RecordingBackend` additionally keeps the
raw LLM answers. See [Concepts](concepts.md#reproducibility).

## Extending Py-CoSim

### Can I use Modelica models or FMUs?

Yes, as co-simulation FMUs (FMI 2.0 or 3.0, e.g. exported from Modelica/Dymola/OpenModelica or
Simulink). FMUs are optional: `FMUComponent` needs the `fmpy` package (`pip install fmpy`, or
`pip install -e ".[fmu]"`) and behaves like any other state-based component:

```python
from pycosim.components import FMUComponent

zone = FMUComponent("zone", "ThermalZone.fmu",
                    inputs={"T_out": "TOut", "Q_hvac": "QHVAC"},  # port -> FMU variable
                    outputs={"T_zone": "TZon"},
                    parameters={"UA": 165.0},
                    kinds={"T_zone": "instant"})                  # the default for Real outputs
sim.add(zone)
sim.connect("weather.T_out", "zone.T_out")
```

By default every input and output variable of the FMU becomes a port; the FMU clock starts at
0 at the co-simulation start and advances by the component's step. Model-exchange-only FMUs
are not supported (export with the co-simulation interface). See the `FMUComponent` docstring
and [example 10](../examples/10_fmu_component/run.py), which builds a demo FMU with pythonfmu
and couples it with EPW weather and Python controllers.

### Can I use my own optimiser or MPC?

Yes — any Python code can be a component. Read forecasts with `ctx.history(...)` or from
connected inputs, solve your problem in `step()`, and return the first control move. Give it
a `step_size` if it re-optimises less often than the master step.

### How do I add a new built-in component or report a bug?

Read [Writing components](writing-components.md), look at `src/pycosim/components/` for
examples, run `pytest`, and open an issue or pull request at
<https://github.com/lz356/Py-CoSim>.

## How to cite

Please cite the software and the paper. The repository's `CITATION.cff` holds the details
(GitHub shows a "Cite this repository" button):

```bibtex
@software{zhang_pycosim_2026,
  author  = {Zhang, Liang},
  title   = {Py-CoSim: Python co-simulation framework for buildings},
  version = {2.0.0},
  year    = {2026},
  url     = {https://github.com/lz356/Py-CoSim},
  license = {MIT}
}

@article{zhang_pycosim_2023,
  author  = {Zhang, Liang and Haroon, Saquib Mohammed and Ryan, Alyssa},
  title   = {Py-CoSim: Python-based building energy co-simulation infrastructure},
  journal = {SSRN Electronic Journal},
  year    = {2023},
  note    = {SSRN 4572925 (preprint)}
}
```

If you use the occupancy data, also cite the 2017 National Household Travel Survey (U.S.
Federal Highway Administration).
