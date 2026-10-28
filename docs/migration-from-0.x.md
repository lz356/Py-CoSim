# Migrating from Py-CoSim 0.x

Py-CoSim 2 is a rewrite of the original notebook-based Py-CoSim (`PyCoSim.ipynb`). The
research ideas are the same — EnergyPlus buildings co-simulated with tariffs, controllers,
occupancy models and AI — but the machinery underneath is new. The old code is preserved in
the git tag `v0.1-legacy`:

```bash
git show v0.1-legacy:PyCoSim.ipynb > PyCoSim_legacy.ipynb
```

## Concept map

| Py-CoSim 0.x | Py-CoSim 2 |
|---|---|
| one class per building (`function_n_data`, `function_n_data_2`, …), nearly identical copies | one `EnergyPlusComponent` per building, configured with dictionaries — no subclassing |
| `callback_function(state)` looking up handles with `get_variable_handle` / `get_actuator_handle` | `sensors=`, `meters=`, `actuators=` dictionaries; handles are looked up and **validated** at start-up |
| `if -1 in [...]: print("***Invalid handles"); sys.exit(1)` | `HandleNotFoundError` listing every wrong name with "did you mean" suggestions; `pycosim handles` lists all valid names |
| `init_plot()` / `update_line()` inside each building class | the `LivePlot` observer (any port of any component), `Results.plot()` afterwards |
| `func1()`, `func2()` creating `api`/`state` as **global** variables | nothing to write: each `EnergyPlusComponent` runs EnergyPlus in its own worker process |
| `func3()` (TOU prices from a CSV, in an endless loop) | `TimeOfUseTariff`, or `TimeSeriesSource` for any CSV |
| `func4()` (control: rules, a tkinter dialog, ChatGPT) | a function component (`@pc.component`) or a `Component` subclass; `LLMSupervisor` for LLMs |
| `func5()` (occupancy from CSVs / a PyTorch MLP) | `HouseholdOccupancy` (NHTS diaries), `OccupancyMLP`, or your own component wrapping the PyTorch model |
| `Thread(target=func1).start()` … for every function | `sim.run()` |
| SMMM: one CSV file per component per time step in `dynamic_simulation_result/` | the in-memory data bus; `sim.connect("a.out", "b.in")` |
| waiting until `len(glob(...)) == 2` files exist; time step = number of files | the master clock: every component gets the same `t` in a fixed, documented order |
| `api.exchange.hour/minutes/current_time`, patching hour 24 and minute 60, `year = 2009` hard-coded | `t` is a real `datetime` on the master clock; the year comes from `start`; EnergyPlus' clock is checked against it every step |
| `sys.path.insert(0, 'C:/EnergyPlusV22-2-0/')` | automatic discovery, `ENERGYPLUS_DIR`, or `energyplus_dir=` |
| `np.random.seed(123)` | `CoSimulation(seed=...)` and a per-component `ctx.rng` |
| `openai.api_key = "sk-..."` in the notebook | backends read keys from environment variables |
| regex `extract_number()` on the LLM's text | JSON answers validated, clipped, rate-limited, with fallbacks (`LLMSupervisor`) |
| `result_folder` per building, temp CSVs deleted at start | `<output_dir>/<run name>/<component>/eplus/`, results in `timeseries.csv` etc. |
| `%matplotlib notebook` | `%matplotlib widget` (with `ipympl`) |

## Before and after

### Before (0.x, abbreviated)

```python
sys.path.insert(0, 'C:/EnergyPlusV22-2-0/')
from pyenergyplus.api import EnergyPlusAPI

class function_n_data:
    def callback_function(self, state_argument):
        if not self.got_handles:
            if not api.exchange.api_data_fully_ready(state_argument):
                return
            self.zone_temp_handle = api.exchange.get_variable_handle(
                state_argument, "Zone Mean Air Temperature", "Perimeter_ZN_1")
            self.handle_clgsetp = api.exchange.get_actuator_handle(
                state_argument, "Schedule:Compact", "Schedule Value", "CLGSETP_SCH")
            # ... more handles ...
            if -1 in [self.zone_temp_handle, self.handle_clgsetp]:
                print("***Invalid handles, check spelling and sensor/actuator availability")
                sys.exit(1)
            self.got_handles = True
        if api.exchange.warmup_flag(state_argument):
            return
        self.count += 1
        zone_temp = api.exchange.get_variable_value(state_argument, self.zone_temp_handle)
        hour = api.exchange.hour(state)            # note: global `state`, not state_argument
        minute = api.exchange.minutes(state)
        year = 2009                                # "Year is bogus" -> hard-coded
        pd.DataFrame([zone_temp]).T.to_csv(f'dynamic_simulation_result/{self.building_id}_{self.count:03}.csv')
        while len(glob.glob(working_path + f'/dynamic_simulation_result/bldg*{self.count:03}.csv')) != 2:
            time.sleep(0.5)                        # wait for the other building
        while True:
            try:
                clg_spt = pd.read_csv(working_path + f'/dynamic_simulation_result/control_{self.count:03}.csv')['clg_spt'][0]
                break
            except:
                time.sleep(1)                      # file not there yet (or half written)
        api.exchange.set_actuator_value(state_argument, self.handle_clgsetp, clg_spt)

def func1():
    global api, state
    api = EnergyPlusAPI()
    state = api.state_manager.new_state()
    g = function_n_data('bldg_1', None, "...PHOENIX.idf", "...Tucson.epw", "results/...")
    api.runtime.callback_begin_zone_timestep_after_init_heat_balance(state, g.callback_function)
    api.runtime.run_energyplus(state, ['-w', g.weather_file, '-d', g.result_folder, g.filename_to_run])

def func4():                                       # the controller, polling the folder forever
    while True:
        try:
            n = max(int(x[-7:-4]) for x in os.listdir('dynamic_simulation_result') if 'bldg_1' in x)
            # ... read bldg_1_n.csv, bldg_2_n.csv, TOU_n.csv, compute set-points ...
            pd.DataFrame([26, 22], index=['clg_spt', 'htg_spt']).T.to_csv(f'dynamic_simulation_result/control_{n:03}.csv')
        except:
            time.sleep(0.5)

Thread(target=func1).start(); Thread(target=func2).start()
Thread(target=func3).start(); Thread(target=func4).start(); Thread(target=func5).start()
```

### After (Py-CoSim 2)

```python
import pycosim as pc
from pycosim.components import TimeSeriesSource
from pycosim.observers import LivePlot

def office(name, weather):
    return pc.EnergyPlusComponent(
        name, "RefBldgSmallOfficeNew2004_v1.4_7.2_2B_USA_AZ_PHOENIX.idf", weather,
        sensors={"T_out": ("Site Outdoor Air Drybulb Temperature", "Environment"),
                 "T_zone": ("Zone Mean Air Temperature", "Perimeter_ZN_1"),
                 "Q_cool": ("Zone Air System Sensible Cooling Rate", "Perimeter_ZN_1")},
        actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH"),
                   "htg_sp": ("Schedule:Compact", "Schedule Value", "HTGSETP_SCH"),
                   "occ": ("Schedule:Compact", "Schedule Value", "BLDG_OCC_SCH")},
    )

@pc.component(outputs=["clg_sp", "htg_sp"])
def controller(t, T_1=None, T_2=None, price=0.1):
    return 26.0, 22.0                     # the rule of func4; use t, T_1, T_2, price as needed

sim = pc.CoSimulation(start="2023-07-17", end="2023-07-24", step="15min", name="two_buildings")
sim.add(office("bldg_1", "USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"),
        office("bldg_2", "USA_CA_San.Francisco.Intl.AP.724940_TMY3.epw"),
        TimeSeriesSource("tou", "tou_prices.csv"),        # columns: time, price
        TimeSeriesSource("occupancy", "occupancy.csv"),   # columns: time, bldg_1, bldg_2
        controller)
sim.connect("bldg_1.T_zone", "controller.T_1")
sim.connect("bldg_2.T_zone", "controller.T_2")
sim.connect("tou.price", "controller.price")
for b in ("bldg_1", "bldg_2"):
    sim.connect("controller.clg_sp", f"{b}.clg_sp")
    sim.connect("controller.htg_sp", f"{b}.htg_sp")
    sim.connect(f"occupancy.{b}", f"{b}.occ")
sim.add_observer(LivePlot([["bldg_1.T_zone", "controller.clg_sp"], "bldg_1.Q_cool"], every=4))

res = sim.run(progress=True)
res.df.to_csv("two_buildings.csv")       # every exchanged value, one table
```

Notes on the translation:

* The time-of-use and occupancy CSVs of 0.x were indexed by time-step number. For
  `TimeSeriesSource`, give them a first column with timestamps (e.g. `2023-07-17 00:00`).
  The built-in `TimeOfUseTariff` or `HouseholdOccupancy` may replace them altogether.
* A PyTorch occupancy model becomes a class component that loads the model in `setup()` and
  predicts in `step()` (see [Writing components](writing-components.md#wrapping-an-ml-model)).
* An LLM controller becomes an `LLMSupervisor` (see [AI and RL](ai-and-rl.md)).
* Two buildings with different weather files are no problem: each `EnergyPlusComponent` has
  its own `weather`.
* The legacy example models were EnergyPlus 22.2; the same models (cleaned) are in
  `examples/models/`.

## What the rewrite fixes

| Problem in 0.x | Why it mattered | How Py-CoSim 2 solves it |
|---|---|---|
| **Concurrent EnergyPlus states in threads** (`Thread(target=func1)`, `Thread(target=func2)`) | EnergyPlus is not thread-safe; two simulations in one process share global state and could deadlock or corrupt each other. Python's GIL also limited parallelism, because every callback runs Python code | each building runs in its own worker process: isolated, truly parallel, and a crash in one model does not hang the others |
| **Race conditions on CSV files** | readers could open a file before it was completely written; bare `except: time.sleep()` retried silently; files left over from a previous run (or a crashed run) were picked up; the folder was polled in endless loops | values move through an in-memory data bus in a fixed order; nothing is read before it is written |
| **Unreliable timestamps** | `minutes`/`current_time` follow EnergyPlus' HVAC sub-steps; hour 24 and minute 60 had to be patched; the year was hard-coded to 2009; building 2's callback read the clock of building 1 (global `state`) | timestamps are built from month, day, hour and zone time-step number; every EnergyPlus step is checked against the master clock (`SynchronizationError` otherwise); the run period gets the real calendar year |
| **No synchronisation guarantees** | the time step was inferred by counting files; the controller computed set-points for "the latest file number", occupancy was written for "number + 1"; nothing guaranteed that step *k*'s control used step *k*'s measurements | a master clock and the phases of each master step (deliver → output → step → update) define exactly which value every component sees at every time (see [Concepts](concepts.md#one-master-step-deliver--output--step--update)) |
| **No error reports** | exceptions in threads were swallowed by `except:` or died silently; the notebook kept running (the TOU/control/occupancy loops never end) | a failing component stops the run with `ComponentError` (component, phase, simulation time), `error_report.txt` with the inputs at that moment, and partial results |
| **Hard-coded handles without validation** | a typo produced handle `-1` and `sys.exit(1)` from inside an EnergyPlus callback, with no hint what was wrong | names are validated before the first step; `HandleNotFoundError` lists all wrong names with suggestions; `pycosim handles` lists all valid ones |
| **An API key in the source code** | a (commented-out) OpenAI key is stored in the notebook and therefore in the git history | keys come only from environment variables; **if you used that key, revoke it** in your OpenAI account — removing it from the notebook does not remove it from the history at tag `v0.1-legacy` |
| hard-coded number of buildings (`!= 2`), one class copy per building | scaling to a community meant editing code in many places | buildings are added in a loop; example 04 runs any number |
| global random seed | changing one component changed the random numbers of all others | per-component, name-seeded `ctx.rng` |
| results scattered over per-step CSV files | post-processing required re-assembling hundreds of files | one `Results` object: `df`, `events`, `timing`, `meta`, saved as four files |

Also new in version 2: multi-rate components, algebraic-loop detection, an RC building
model for quick prototyping, PV/battery/tariff/grid components, NHTS-based occupancy with a
documented method, LLM guardrails with record/replay, a Gymnasium interface for RL, YAML
scenarios and the `pycosim` command line.

## Step-by-step migration checklist

1. Install Py-CoSim 2 and check EnergyPlus with `pycosim doctor` ([Installation](installation.md)).
   Remove `sys.path.insert(...)` lines.
2. For each building class, create an `EnergyPlusComponent`: copy the variable names into
   `sensors=` and the actuator triples into `actuators=`. Check them with
   `pycosim handles <model> -w <epw>`.
3. Turn each `funcN()` that is not a building into a component: a decorated function if it is
   a rule, a class if it keeps state or loads a model.
4. Replace every CSV exchange by a `sim.connect(...)`.
5. Replace plotting code by `LivePlot` (during the run) or `Results.plot()` (afterwards).
6. Decide on daylight saving time (`daylight_saving=False` is recommended — see
   [Concepts](concepts.md#time-convention)) and set a `seed`.
7. Run a short period (`end` one or two days after `start`) and compare with your old results.
   Expect small differences where 0.x mixed up time steps.
