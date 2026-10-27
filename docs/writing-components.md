# Writing your own components

Anything you can compute in Python can become a component: a control rule, a measured data
set, a regression model, a neural network, a grid model, a survey-based behaviour model.
There are two ways to write one:

| | Function component | Class component |
|---|---|---|
| How | decorate a function with `@pycosim.component` | subclass `pycosim.Component` |
| State between steps | no (stateless) | yes (attributes on `self`) |
| Feedthrough | always | either |
| Best for | rules, formulas, simple controllers, unit conversions | models with memory, ML models, anything that needs `setup`/`finalize` |

Read [Concepts](concepts.md) first if the words *feedthrough* or *port* are new to you.

## Function components

```python
import pycosim as pc

@pc.component(outputs="clg_sp")
def thermostat(t, price=0.10, T_zone=24.0):
    """Cooling set-point [C]."""
    if not 7 <= t.hour < 22:
        return 27.5
    return 26.0 if price > 0.2 else 24.0

sim.add(thermostat)                                  # component name = function name
sim.connect("tariff.price", "thermostat.price")
sim.connect("thermostat.clg_sp", "house.clg_sp")
```

How the function is read:

* **The first parameter always receives the current time** (a `datetime`), whatever you
  call it, so always write it first (`t`). If the first parameter has a default value, as in
  `def f(price=0.1)`, Py-CoSim warns you (`UserWarning: The first parameter 'price' of f()
  receives the simulation time ...`) — write `def f(t, price=0.1)` instead.
* **Every other parameter becomes an input port.** Its default value becomes the input's
  default (used while the input is not connected). Parameters without a default get `None`.
* A parameter named **`ctx`** is not an input; it receives the component's
  [context](#the-context-ctx) (random generator, logging, history, …).
* `outputs=` names the output ports. Without it the single output is called `out`.

Decorator options: `@pc.component(name=..., inputs=..., outputs=..., step_size=..., priority=...)`.
`name` overrides the function name; `inputs` declares the inputs explicitly (they must still be
accepted by the function, or the function must take `**inputs`).

### Returning values

| Your function returns | Meaning |
|---|---|
| a single value | the value of the one output |
| a tuple or list `(a, b)` | values of several outputs, in the order of `outputs=[...]` |
| a dict `{"a": 1.0, "b": 2.0}` | values by name (you may return only some of them) |
| `None` | publish nothing new this step: the outputs keep their previous values |

```python
@pc.component(outputs=["clg_sp", "htg_sp"])
def setpoints(t, occupied=1):
    return (24.0, 21.0) if occupied else (29.4, 15.6)
```

### Using the context in a function

```python
@pc.component(outputs="noisy_T")
def sensor_noise(t, T=20.0, ctx=None):
    return T + ctx.rng.normal(0.0, 0.2)      # reproducible noise (same seed -> same run)
```

### Several copies of one function: `.copy(name)`

A decorated function is one component. To use the same logic for several buildings, make
independent copies:

```python
for b in ["bldg00", "bldg01", "bldg02"]:
    ctrl = thermostat.copy(f"ctrl_{b}")
    sim.add(ctrl)
    sim.connect(f"{b}.T_core_zn", f"ctrl_{b}.T_zone")
    sim.connect(f"ctrl_{b}.clg_sp", f"{b}.clg_sp")
```

`copy(name, **overrides)` also accepts attributes to change, e.g.
`thermostat.copy("slow_thermostat", step_size="1h")`.

You can also wrap a lambda or an existing function without the decorator:

```python
sim.add(pc.FunctionComponent(lambda t, P_bldgs=0.0, P_pv=0.0: P_bldgs - P_pv,
                             name="net_load", outputs="P_net"))
```

## Class components

Subclass `pycosim.Component` when the component must remember something between steps,
load a file or model once, or report its state.

### A feedthrough class component

```python
import pycosim as pc

class DemandLimiter(pc.Component):
    """Raise the cooling set-point while the 15-min average demand exceeds a limit."""

    inputs = {"P_elec": 0.0}                 # name -> default
    outputs = {"clg_sp": 24.0}               # name -> initial value

    def __init__(self, name, limit_kw=40.0, base_sp=24.0, max_offset=3.0):
        super().__init__(name)               # always call this first
        self.limit_w = limit_kw * 1000
        self.base_sp, self.max_offset = base_sp, max_offset

    def setup(self, ctx):
        # Called once before the first step: initialise state here.
        self.offset = 0.0

    def step(self, t, inputs):
        if inputs.P_elec > self.limit_w:
            self.offset = min(self.offset + 0.5, self.max_offset)
        else:
            self.offset = max(self.offset - 0.5, 0.0)
        return {"clg_sp": self.base_sp + self.offset}
```

`inputs` behaves like a dictionary that also allows attribute access: `inputs.P_elec` and
`inputs["P_elec"]` are the same. A typo raises an error that lists the available inputs.

### Declaring ports

As class attributes (fixed ports):

```python
inputs = {"T_out": 25.0, "GHI": 0.0}                       # name -> default
inputs = ["T_out", "GHI"]                                   # names only (default None)
inputs = {"T_out": {"default": 25.0, "unit": "C", "description": "outdoor air"}}
outputs = {"P": 0.0, "soc": None}                           # name -> initial value
```

Or in `__init__` when the ports depend on arguments (after `super().__init__(name)`):

```python
class ZoneAverager(pc.Component):
    def __init__(self, name, zones):
        super().__init__(name)
        for z in zones:
            self.add_input(f"T_{z}", default=None, unit="C")
        self.add_output("T_mean", initial=None, unit="C")

    def step(self, t, inputs):
        values = [v for v in inputs.values() if v is not None]
        return sum(values) / len(values) if values else None
```

Units are free text; they appear in `describe()`, in plot legends and in `res.units`.

### A non-feedthrough (state-based) class component

Physical models whose outputs depend only on their state implement `output` and `update`
instead of `step`, and set `direct_feedthrough = False`:

```python
class WaterTank(pc.Component):
    """Hot-water tank: well-mixed, heated by an electric element."""

    direct_feedthrough = False
    inputs = {"P_heater": 0.0, "draw_lps": 0.0, "T_mains": 15.0}   # W, litre/s, C
    outputs = {"T_tank": 55.0}

    def __init__(self, name, volume_l=200.0, UA=2.0, T_amb=20.0):
        super().__init__(name)
        self.C = volume_l * 4186.0        # J/K
        self.UA, self.T_amb = UA, T_amb
        self.T = 55.0

    def output(self, t):
        # Report the state at time t. Do not change the state here.
        return {"T_tank": self.T}

    def update(self, t, inputs):
        # Advance the state from t to t + step using the inputs at t.
        dt = self.ctx.step_size
        q = (inputs.P_heater
             - inputs.draw_lps * 4186.0 * (self.T - inputs.T_mains)
             - self.UA * (self.T - self.T_amb))
        self.T += q * dt / self.C
```

Because its outputs at `t` do not depend on its inputs at `t`, a heater controller can read
`T_tank` and drive `P_heater` without creating an algebraic loop.

### Lifecycle methods

| Method | When it is called | Use it for |
|---|---|---|
| `__init__(...)` | when you create the component | parameters, ports. `self.ctx` is still `None` here |
| `setup(ctx)` | once, before the first step | loading files/models, initial state, anything that needs the clock or `ctx` |
| `ready()` *(optional)* | once, after **all** components ran `setup` | waiting for something slow that `setup` started (see below) |
| `step(t, inputs)` | every own step (feedthrough) | compute and return outputs |
| `output(t)` | every own step, phase 1 (non-feedthrough) | return the outputs of the current state |
| `update(t, inputs)` | every own step, phase 3 (non-feedthrough) | advance the state over `[t, t + step)` |
| `finalize()` | once after the last step | close files, release resources |
| `abort()` | instead of `finalize()` when the run fails | emergency stop; by default it simply calls `finalize()` |
| `run_metadata()` *(optional)* | when results are built | return a dict stored in `res.meta["component_metadata"][name]` |

The order is always: `setup` for every component → `ready` for every component → the steps
(`output`/`step`/`update`) → `finalize` (or `abort` after a failure).

**Why `ready()`?** It lets slow start-ups run in parallel. If `setup` only *starts*
something time-consuming (launches a process, opens a connection, starts loading a big
model in a thread) and `ready` *waits* for it, then all components start at the same time
instead of one after the other. `EnergyPlusComponent` uses exactly this: `setup` launches the
EnergyPlus worker, `ready` waits until EnergyPlus has finished sizing — so the sizing of many
buildings runs in parallel. Most components do not need `ready` (the default does nothing).

```python
import subprocess

class ExternalModel(pc.Component):          # sketch: my_model_server and wait_until_port_open
    outputs = {"y": None}                   # stand for your own program and check

    def setup(self, ctx):
        self.proc = subprocess.Popen(["my_model_server", "--port", "5555"])   # returns at once

    def ready(self):
        wait_until_port_open(5555, timeout=60)      # blocks; other components started meanwhile

    def step(self, t, inputs):
        ...

    def finalize(self):
        self.proc.terminate()
```

An exception in `ready` is reported like one in `setup` (`ComponentError ... during 'ready'`).

Two class attributes control scheduling: `step_size` (see below) and `priority` (tie-breaker
between independent feedthrough components; lower runs first, default 0).

If you omit `name` when creating a class component, it is derived from the class name
(`EVCharger()` → `"ev_charger"`). Built-in components require a name.

## The context (`ctx`)

Before `setup` is called, the master gives each component a context, available as the
`ctx` argument of `setup` and as `self.ctx` afterwards:

| Member | Meaning |
|---|---|
| `ctx.rng` | NumPy random generator, seeded from the simulation seed and the component name — **use it for all randomness** |
| `ctx.log(message, kind="info", **data)` | write to the Python log *and* the run's event log (`res.events`); extra keywords become columns |
| `ctx.latest("comp.port", default=None)` | latest value of any output port on the data bus |
| `ctx.history("comp.port", n=None)` | published values of any output port as a pandas Series (last `n`) |
| `ctx.output_dir` | a folder reserved for this component's files: `<output_dir>/<run>/<component>/` (created on first use) |
| `ctx.step_size` | this component's own step in seconds |
| `ctx.dt` | the master step in seconds |
| `ctx.start`, `ctx.end`, `ctx.n_steps` | simulation window (end exclusive) and number of master steps |
| `ctx.now`, `ctx.step_index` | current master time and step index |
| `ctx.logger` | a standard `logging.Logger` named `pycosim.<component>` |

Example (from [example 09](examples.md#09--writing-your-own-component-ev-charger)):

```python
def setup(self, ctx):
    rng = ctx.rng
    self.arrive = ctx.start.replace(hour=0) + pd.Timedelta(hours=rng.uniform(16.5, 19.5))

def step(self, t, inputs):
    ...
    self.ctx.log(f"EV arrives, SOC {self.soc:.2f}", kind="ev", soc=self.soc)
```

`ctx.log` accepts `level=` (a `logging` level, default `INFO`). Messages at `DEBUG` level go to
the event log without being printed.

## Step sizes

By default a component runs at every master step. To run slower:

```python
class HourlyForecast(pc.Component):
    step_size = "1h"                      # class default
    ...

forecast = HourlyForecast("fc", step_size="30min")   # Component's constructor accepts it

@pc.component(outputs="u", step_size="1h")           # function components
def rule(t): ...

weather = EPWWeather("weather", epw)                  # built-in components:
weather.step_size = "1h"                              # set the attribute after creating it
```

Rules:

* the step must be a whole multiple of the master step (else a `ConfigurationError`);
* the component runs at `start`, `start + step_size`, …; its outputs are held in between;
* inside the component, integrate over `self.ctx.step_size`, not over the master step.

Your own subclass only accepts `step_size=` in its constructor if you pass it on:
`super().__init__(name, step_size=step_size)`.

## Wrapping an ML model

Load the model once in `setup` (or `__init__`), predict in `step`:

```python
import joblib

class LoadForecaster(pc.Component):
    inputs = {"T_out": None}
    outputs = {"P_forecast": None}
    step_size = "1h"

    def __init__(self, name, model_path):
        super().__init__(name)
        self.model_path = model_path

    def setup(self, ctx):
        self.model = joblib.load(self.model_path)           # e.g. a scikit-learn regressor

    def step(self, t, inputs):
        hist = self.ctx.history("weather.T_out", n=24)        # lagged features from the bus
        if inputs.T_out is None or len(hist) < 24:
            return None                                       # not enough data yet: keep previous
        X = [[t.hour, t.weekday(), inputs.T_out, hist.mean()]]
        return float(self.model.predict(X)[0])
```

The same pattern works for PyTorch (`torch.load` + `model.eval()` in `setup`, `with
torch.no_grad():` in `step`) or any other library.

## Common mistakes and the messages they produce

| Mistake | What you see |
|---|---|
| Adding a plain function without the decorator | `ConfigurationError: <function plain at 0x...> is not a Component. Wrap functions with @pycosim.component or subclass pycosim.Component.` |
| Forgetting the time parameter: `def f(price=0.1)` | `UserWarning: The first parameter 'price' of f() receives the simulation time, but it has a default value as if it were an input. Write e.g. def f(t, price=...) ...` |
| Typo in a port name when connecting | `ConfigurationError: Connection thermostat.clg_sp -> house.clg_setpoint: 'house' has no input 'clg_setpoint'. Inputs: ['T_out', 'GHI', 'Q_int', 'clg_sp', 'htg_sp']` |
| Connecting a second source to an input | `ConfigurationError: Input house.clg_sp is already connected. Connect several sources at once with a list and reduce=...` |
| A list of sources without `reduce` | `ConfigurationError: 2 sources connected to grid.P_net: pass reduce='sum' (or 'mean', 'max', 'min', 'list', a callable)` |
| Two feedthrough components feeding each other | `AlgebraicLoopError: Algebraic loop detected between direct-feedthrough components: f -> g -> f. ...` → use `delay=True` |
| A step size that is not a multiple of the master step | `ConfigurationError: Component 'c' has step size 1200s which is not a positive integer multiple of the master step 900s` |
| Returning an undeclared output name | `ConfigurationError: Component 'h' returned unknown outputs ['z']; declared outputs are ['y']` |
| Returning one value for several outputs | `ConfigurationError: Component 'g' returned a single value but declares 2 outputs; return a dict {output: value} or a tuple in declaration order` |
| Returning a tuple/list of the wrong length | `ConfigurationError: Component 'g' returned a sequence of length 3 but declares 2 outputs ['a', 'b']` |
| A dot or space in a component name | `ConfigurationError: Invalid component name 'my.comp': use letters, digits, '_' or '-', ...` |
| Two components with the same name | `ConfigurationError: A component named 'ctrl' already exists; names must be unique` → use `.copy(name)` |
| An exception inside your code | `ComponentError: Component 'boom' failed during 'step' at 2023-07-03 00:00:00: ZeroDivisionError: float division by zero` + `error_report.txt` with the inputs at that moment |
| Using `self.rng` / `self.ctx` in `__init__` | `RuntimeError: rng is only available after the component was added to a simulation and set up` (or `None`) → move the code to `setup` |
| Using `np.random` / `random` directly | no error, but runs are not reproducible → use `self.ctx.rng` |
| Reusing a component object in a second simulation | no error, but its internal state (e.g. a battery's SOC) carries over → build fresh components for every run (write a `build()` function) |
| Not calling `super().__init__(name)` in `__init__` | the name falls back to the class name in snake case; always call it |
| Missing `start`/`end` without an EnergyPlus component | `ConfigurationError: Simulation start/end unknown: pass start=... and end=... to CoSimulation` |

When a component fails, open `error_report.txt` in the run folder first: it shows the
simulation time, the phase and the exact input values the component received.
