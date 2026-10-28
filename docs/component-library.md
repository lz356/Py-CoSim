# Component library

Ready-made components in `pycosim.components`. Import them with

```python
from pycosim.components import EPWWeather, RCBuilding, PVSystem, Battery  # ...
```

All of them take a `name` as first argument. Units: temperatures in °C, power in W, energy in
kWh unless stated otherwise, prices in $/kWh. "In" = input port (default in brackets), "Out" =
output port. Built-in components do not take a `step_size=` argument; set
`comp.step_size = "1h"` after creating one to make it run slower (see
[Writing components](writing-components.md#step-sizes)).

**Time meaning of the outputs.** Every library output declares what its value means in time
(its [kind](smmm.md#the-five-kinds-what-a-time-stamp-means)), so SMMM can align it for
readers with another step size or timing convention (`read="past"` etc.):

| Kind | Library outputs |
|---|---|
| `instant` — the state at `t` | `EPWWeather` (all fields, solar angles), `RCBuilding.T_zone`/`T_mass`, `PVSystem.P_ac`/`POA` |
| `average`, `interval="past"` — mean over the step that ended, `[t-h, t)` | `RCBuilding.Q_cool`/`Q_heat`/`P_hvac`/`P_elec`, `GridMeter.P_import`/`P_export`, EnergyPlus sensors and W/kW meters |
| `average`, `interval="ahead"` — mean over the step that starts, `[t, t+h)` | `Battery.P`, `HouseholdOccupancy` outputs (15-minute diary slots) |
| `hold` — valid from `t` until replaced | `TimeOfUseTariff.price`/`is_peak`, `Constant`, `DailyProfile` (default), the controllers' set-points and `P_set`, `LLMSupervisor` set-points, `ExternalAgent` actions |
| untyped (behaves like `hold`) | `Battery.soc`/`energy`, `GridMeter.E_import`/`E_export`/`cost`/`peak` (running totals), `LLMSupervisor.valid`/`latency`/`reason`, `TimeSeriesSource` (unless you pass `kind=`) |

`describe()` shows the kind next to each port, e.g. `P_elec [W] (average/past)`. To change
one, use `comp.set_port("port", kind=..., interval=...)`.

| Group | Components |
|---|---|
| [Weather and data](#weather-and-data) | `EPWWeather`, `TimeSeriesSource`, `DailyProfile`, `Constant` |
| [Building without EnergyPlus](#rcbuilding) | `RCBuilding` |
| [Energy resources and grid](#energy-resources-and-grid) | `PVSystem`, `Battery`, `TimeOfUseTariff`, `GridMeter` |
| [Controls](#controls) | `ThermostatSchedule`, `PrecoolingController`, `PIController`, `PeakShavingController` |
| [People and travel](#people-and-travel-householdoccupancy) | `HouseholdOccupancy`, `select_households`, `OccupancyMLP` |
| [AI decision making](#ai-decision-making-llmsupervisor) | `LLMSupervisor` and its backends |

EnergyPlus buildings are covered in [EnergyPlus](energyplus.md); the RL agent placeholder
`ExternalAgent` in [AI and RL](ai-and-rl.md#reinforcement-learning).

---

## Weather and data

### EPWWeather

Publishes weather from an EnergyPlus weather (EPW) file on the co-simulation clock.

```python
EPWWeather(name, path, fields=None, interpolate=True, solar=True)
```

| Parameter | Default | Meaning |
|---|---|---|
| `path` | — | EPW file |
| `fields` | `["T_out", "RH", "GHI", "DNI", "DHI", "wind_speed"]` | which variables to publish (see below) |
| `interpolate` | `True` | linear interpolation between hourly records (as EnergyPlus does); `False` = hold the record of the current hour |
| `solar` | `True` | also publish `solar_zenith` and `solar_azimuth` |

Available fields: `T_out` [C], `T_dew` [C], `RH` [%], `pressure` [Pa], `IR_horizontal`,
`GHI`, `DNI`, `DHI` [W/m2], `wind_direction` [deg], `wind_speed` [m/s], `sky_cover`
[tenths], `precipitation` [mm]. Out: the chosen fields, plus `solar_zenith`, `solar_azimuth`
[deg, azimuth clockwise from north] if `solar=True` — all `instant`.

The value at `t` equals what EnergyPlus reports at `t` (EPW record *h* describes the hour
ending at *h*:00). The EPW year is ignored: data are mapped by month, day and hour onto any
simulation year (Feb 29 uses Feb 28). Helpers: `read_epw(path)` returns `(DataFrame,
location)`; `solar_position(t, latitude, longitude, timezone)` returns `(zenith, azimuth)`.

```python
weather = sim.add(EPWWeather("weather", "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"))
sim.connect("weather.T_out", "house.T_out")
```

### TimeSeriesSource

Plays back measured or pre-computed data: each column becomes an output port.

```python
TimeSeriesSource(name, data, method="previous", repeat_yearly=False, columns=None, units=None,
                 kind=None, interval=None)
```

| Parameter | Default | Meaning |
|---|---|---|
| `data` | — | CSV path (first column = timestamps), pandas `Series` or `DataFrame` with a datetime index |
| `method` | `"previous"` | value between samples: `"previous"` (step-wise), `"linear"` or `"nearest"` |
| `repeat_yearly` | `False` | map the simulation time onto the data's year (typical-year data) |
| `columns` | `None` | use only these columns |
| `units` | `None` | `{column: unit}` for plots |
| `kind`, `interval` | `None` | time meaning of every published value, e.g. `kind="instant"` for measured temperatures, `kind="hold"` for prices ([kinds](smmm.md#the-five-kinds-what-a-time-stamp-means)) |

Before the first sample the first value is used, after the last sample the last value.

```python
prices = TimeSeriesSource("rtp", "data/real_time_prices.csv", method="previous",
                          units={"price": "$/kWh"})
sim.connect("rtp.price", "controller.price")
```

### DailyProfile

A value that follows a 24-hour profile, optionally with a different weekend profile.

```python
DailyProfile(name, profile, weekend=None, output="value", unit=None, kind="hold")
```

`profile` is either 24 hourly values or a dict of change points `{hour: value}` (the last
value wraps around midnight). Out: `<output>` (default `value`).

```python
sp = DailyProfile("clg_schedule", {0: 29.0, 7: 24.0, 19: 29.0}, weekend=[29.0] * 24,
                  output="clg_sp", unit="C")
```

### Constant

Outputs constant values, one port per keyword.

```python
limits = Constant("limits", P_max=50e3, T_max=26.0)     # outputs limits.P_max, limits.T_max
```

---

## RCBuilding

A light-weight 2R2C single-zone building (indoor air node + building mass node) with ideal
heating and cooling. It has the same timing behaviour as `EnergyPlusComponent`
(non-feedthrough), so you can prototype with it in seconds and switch to EnergyPlus by
replacing one component.

```python
RCBuilding(name, floor_area=100.0, *, H_ae=None, H_me=None, H_am=None, C_air=None, C_mass=None,
           A_sol=None, f_conv=0.5, cool_capacity=None, heat_capacity=None, cop_cool=3.0,
           cop_heat=3.0, base_load=None, default_setpoints=(24.0, 20.0), T_init=24.0, substep=60.0)
```

| Parameter | Default (A = floor area, m²) | Meaning |
|---|---|---|
| `floor_area` | 100 | scales all defaults below |
| `H_ae` | 0.6·A W/K | air ↔ outdoor (windows, infiltration) |
| `H_me` | 0.5·A W/K | mass ↔ outdoor (opaque envelope) |
| `H_am` | 22.75·A W/K | air ↔ mass (ISO 13790: 9.1 W/m²K × 2.5·A) |
| `C_air`, `C_mass` | 1.0e4·A, 1.65e5·A J/K | heat capacities (medium-weight building) |
| `A_sol` | 0.04·A m² | effective solar aperture: solar gain = `A_sol × GHI` |
| `f_conv` | 0.5 | share of gains going to the air node (rest to the mass) |
| `cool_capacity`, `heat_capacity` | 120·A, 100·A W | ideal HVAC capacity (thermal) |
| `cop_cool`, `cop_heat` | 3.0, 3.0 | electric efficiency |
| `base_load` | 8·A W | non-HVAC electricity (added to `P_elec`; not a heat gain — use `Q_int` for gains) |
| `default_setpoints` | (24.0, 20.0) | (cooling, heating) used when `clg_sp`/`htg_sp` are `None` |
| `T_init` | 24.0 | initial air and mass temperature |
| `substep` | 60 s | internal integration step (backward Euler) |

In: `T_out` [25.0], `GHI` [0.0], `Q_int` [0.0, W internal gains], `clg_sp` [None], `htg_sp`
[None]. Out: `T_zone`, `T_mass` [C], `Q_cool`, `Q_heat` [W thermal], `P_hvac`, `P_elec` [W
electric; `P_elec = P_hvac + base_load`]. Outputs at `t` are the state at `t` (temperatures:
`instant`) and the average powers over the previous step (`average`, `interval="past"`), like
EnergyPlus. The ideal HVAC keeps the air temperature at the end of
each step between the set-points, within capacity.

```python
house = sim.add(RCBuilding("house", floor_area=150))
sim.connect("weather.T_out", "house.T_out")
sim.connect("weather.GHI", "house.GHI")
sim.connect("thermostat.clg_sp", "house.clg_sp")
```

---

## Energy resources and grid

### PVSystem

A PVWatts-style PV array: isotropic-sky transposition to the plane of array, NOCT cell
temperature, linear temperature coefficient, fixed losses, inverter efficiency and AC
clipping.

```python
PVSystem(name, capacity_kw, tilt=20.0, azimuth=180.0, losses=0.14, temp_coeff=-0.0037,
         noct=45.0, inverter_eff=0.96, dc_ac_ratio=1.2, albedo=0.2)
```

| Parameter | Default | Meaning |
|---|---|---|
| `capacity_kw` | — | DC nameplate capacity |
| `tilt`, `azimuth` | 20°, 180° | panel tilt and azimuth (180° = south) |
| `losses` | 0.14 | system losses (soiling, wiring, …) |
| `temp_coeff` | −0.0037 1/K | power temperature coefficient |
| `noct` | 45 °C | nominal operating cell temperature |
| `inverter_eff` | 0.96 | inverter efficiency |
| `dc_ac_ratio` | 1.2 | AC limit = `capacity_kw / dc_ac_ratio` |
| `albedo` | 0.2 | ground reflectance |

In: `GHI`, `DNI`, `DHI` [0.0], `T_out` [25.0], `solar_zenith` [90.0], `solar_azimuth`
[180.0] — all provided by `EPWWeather`. Out: `P_ac` [W], `POA` [W/m2], both `instant`.

```python
pv = sim.add(PVSystem("pv", capacity_kw=6))
sim.connect_matching("weather", "pv",
                     ports=["GHI", "DNI", "DHI", "T_out", "solar_zenith", "solar_azimuth"])
```

### Battery

Battery storage with power limits, SOC limits and charge/discharge losses.

```python
Battery(name, capacity_kwh, max_charge_kw, max_discharge_kw=None, eff_charge=0.95,
        eff_discharge=0.95, soc_init=0.5, soc_min=0.1, soc_max=0.95)
```

`max_discharge_kw` defaults to `max_charge_kw`. In: `P_set` [0.0, W; **positive = charge**,
negative = discharge]. Out: `P` [W, the power actually exchanged after limits, `average` over
the step that *starts*, `interval="ahead"`], `soc` [-] and `energy` [kWh stored], both at the
end of that step.

The battery is a feedthrough component: at `t` it applies `P_set` over `[t, t + step)` and
reports the resulting SOC. A dispatch controller that reads `soc` and writes `P_set`
therefore forms a loop — connect `soc` with `delay=True`:

```python
sim.add(Battery("battery", capacity_kwh=500, max_charge_kw=125))
sim.connect("dispatch.P_set", "battery.P_set")
sim.connect("battery.soc", "dispatch.soc", delay=True)
sim.connect("battery.P", "grid.P_bat")    # GridMeter: aligned with the building's power
```

Because `P` describes the coming step while EnergyPlus and `RCBuilding` powers describe the
step that ended, connect it to the meter's `P_bat` input (read `past`, so SMMM lines the two
up) rather than adding it to `P_net`.

### TimeOfUseTariff

```python
TimeOfUseTariff(name, peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.10,
                shoulder_hours=None, shoulder_price=None, weekdays_only=True, months=None)
```

| Parameter | Default | Meaning |
|---|---|---|
| `peak_hours` | (16, 21) | on-peak window, start inclusive, end exclusive (local standard time) |
| `peak_price`, `offpeak_price` | 0.30, 0.10 | $/kWh |
| `shoulder_hours`, `shoulder_price` | `None` | optional mid-peak window and price |
| `weekdays_only` | `True` | weekends are off-peak all day |
| `months` | `None` | months in which the peak and shoulder windows apply, e.g. `range(5, 11)`; other months are off-peak |

Out: `price` [$/kWh], `is_peak` [0/1], both `hold` (valid from `t` until the next value).
To let consumers subscribe without naming the tariff, give the price a topic:
`tariff.set_port("price", topic="grid/price")` ([topics](smmm.md#patterns-and-topics)).

### GridMeter

The point of connection to the grid: splits import/export and integrates energy, cost and
peak demand.

```python
GridMeter(name, demand_window="15min", aligned=True)
```

At each of its steps the meter accounts for the interval that just ended, `[t-h, t)`, with
the net import `P_net + P_load - P_gen + P_bat`.

| In | Default | Read with `aligned=True` | Meaning |
|---|---|---|---|
| `P_load` | 0.0 W | `past` | consumption (connect several with `reduce="sum"` or a pattern) |
| `P_gen` | 0.0 W | `past` | generation, e.g. PV (positive) |
| `P_bat` | 0.0 W | `past` | battery power, positive = charging |
| `P_net` | 0.0 W | `latest` | net import (positive = import), the Py-CoSim 2.0 input; added to the others |
| `price` | 0.0 $/kWh | `past` | import price |
| `export_price` | 0.0 $/kWh | `past` | export price |

Out: `P_import`, `P_export` [W, mean over the step that ended: `average`, `interval="past"`],
`E_import`, `E_export` [kWh, cumulative], `cost` [$, cumulative, import cost minus export
revenue], `peak` [kW, running maximum of the import averaged over `demand_window`].

With `aligned=True` (the default) each `past` input is the mean over the meter's step that
just ended, whatever the producers' steps and time meanings are: an EnergyPlus meter
(`average`/`past`) is used as it is, `Battery.P` (`average`/`ahead`) contributes the power of
the same interval, PV power (`instant`) is averaged with the trapezoid rule and the price
(`hold`) is time-weighted — so energy is priced with the tariff of its own interval, also
for an hourly meter on a 15-minute building ([example](smmm.md#alignment-across-step-sizes)).
`aligned=False` reads every input as the latest value (the Py-CoSim 2.0 meter).

```python
sim.add(GridMeter("meter"))
sim.subscribe("meter.P_load", "*.P_elec", reduce="sum")    # every building's P_elec
sim.subscribe("meter.P_gen", "pv.P_ac")
sim.subscribe("meter.P_bat", "battery.P")
sim.subscribe("meter.price", "tariff.price")
# after the run: res["meter.cost"].iloc[-1], res["meter.peak"].iloc[-1]
```

**Pitfalls.**

* `P_net` is still read `latest`, but the price is now read `past`. That is right for
  EnergyPlus meters and `RCBuilding.P_elec` (their value at `t` describes `[t-h, t)`), but
  power from a component that describes the *coming* step (`Battery.P`, an EV charger written
  as a feedthrough component) is then priced with the previous step's price. In a one-week
  test with the smart EV charger of example 09, adding the EV to `P_net` overstated the cost
  by 3.6 %; connecting house and EV to `P_load` gave the exact cost. Use `P_load`, `P_gen`
  and `P_bat` for new models.
* `delay=True` on a connection to an aligned input (the old advice for exact pricing) now
  raises a `ConfigurationError`; drop it — the `past` read already does the job — or pass
  `read="latest"` on that connection.
* At the first step the meter accounts for the interval *before* the start; SMMM fills it
  with the first known values (one extrapolated read in `res.alignment`).

---

## Controls

### ThermostatSchedule

Occupied/unoccupied cooling and heating set-points.

```python
ThermostatSchedule(name, occupied_hours=(6, 22), cooling=(24.0, 29.4), heating=(21.0, 15.6),
                   weekend_occupied=False)
```

`cooling` and `heating` are `(occupied, unoccupied)` pairs. Out: `clg_sp`, `htg_sp` (`hold`).

### PrecoolingController

Pre-cool before a price peak (charging the building's thermal mass), relax during it.

```python
PrecoolingController(name, peak_hours=(16, 21), precool_hours=3.0, base_sp=24.0, precool_sp=22.0,
                     peak_sp=26.5, occupied_hours=None, unoccupied_sp=None, weekdays_only=True)
```

Mode at time `t`: outside `occupied_hours` → `unoccupied_sp` (or `base_sp` if not given);
weekends (with `weekdays_only`) → `base_sp`; in `peak_hours` → `peak_sp`; in the
`precool_hours` before the peak → `precool_sp`; otherwise `base_sp`. In: `enable` [1; 0
switches the strategy off → `base_sp`]. Out: `clg_sp`.

```python
sim.add(PrecoolingController("ctrl", peak_hours=(16, 21), precool_hours=3,
                             occupied_hours=(6, 21), unoccupied_sp=29.4))
sim.connect("ctrl.clg_sp", "office.clg_sp")
```

### PIController

Discrete PI controller with output limits and anti-windup.

```python
PIController(name, kp, ki=0.0, setpoint=0.0, u_min=0.0, u_max=1.0, reverse=False)
```

In: `measurement` [None], `setpoint` [None → the `setpoint` parameter]. Out: `u` (clipped to
`[u_min, u_max]`; `u_min` while `measurement` is `None`). Error = setpoint − measurement, or
measurement − setpoint with `reverse=True` (cooling loops: more output when too warm). The
integral uses the component's own step in seconds and is frozen while the output saturates.

### PeakShavingController

Rule-based battery dispatch for PV self-consumption, on-peak discharge and peak shaving.

```python
PeakShavingController(name, import_limit_kw, charge_kw, soc_target=0.9, charge_hours=(0, 6),
                      store_pv_surplus=True, discharge_on_peak=True)
```

In: `P_load` [0.0, W; net demand *before* the battery, negative = PV surplus], `soc` [0.5],
`is_peak` [0]. Out: `P_set` [W, positive = charge] for `Battery.P_set`. Rules, first match
wins:

1. PV surplus (`P_load < 0`) and `store_pv_surplus` → charge with the surplus;
2. `is_peak` and `discharge_on_peak` → discharge to cover the whole load (never charge
   from the grid on-peak; with a PV surplus and `store_pv_surplus=False` it does nothing);
3. `P_load > import_limit_kw` → discharge the excess;
4. within `charge_hours` and `soc < soc_target` → charge with up to `charge_kw` without
   exceeding the import limit;
5. otherwise 0.

The battery itself enforces its power and SOC limits. See example 04 for a complete set-up.

---

## People and travel: HouseholdOccupancy

`HouseholdOccupancy` gives the number of people at home in `n_units` dwellings, every 15
minutes, from real travel diaries of the **2017 U.S. National Household Travel Survey (NHTS)**.

```python
HouseholdOccupancy(name, n_units, *, group=None, commute=None, hhsize=None, urban=None,
                   households=None, resample="daily", prefix="occ")
```

| Parameter | Default | Meaning |
|---|---|---|
| `n_units` | — | number of dwellings |
| `group` | `None` | `"ev"` (households with a hybrid/electric/alternative-fuel vehicle) or `"non_ev"` |
| `commute` | `None` | `"long"` (commute proxy ≥ 30 min) or `"short"` (≤ 10 min) |
| `hhsize` | `None` | allowed household sizes, e.g. `[3, 4]` |
| `urban` | `None` | NHTS `HBHUR` codes: `"U"` urban, `"S"` suburban, `"C"` second city, `"T"` small town, `"R"` rural |
| `households` | `None` | your own pre-filtered table (instead of the four filters) |
| `resample` | `"daily"` | `"daily"`: each unit draws a new survey day every midnight from households of the same size in the selection; `None`: repeat one day |
| `prefix` | `"occ"` | output name prefix |

Out: `occ_00`, `occ_01`, … (people at home in unit *i*, zero-padded to at least two digits),
`total` [people], `fraction` [-, community-wide share of residents at home]. Households are
drawn with `ctx.rng`, so the same seed gives the same households. After `setup`,
`occ.assignment` is a DataFrame of the households assigned to each unit.

```python
occ = sim.add(HouseholdOccupancy("occ", n_units=23, commute="long"))
for i, unit in enumerate(units):
    sim.connect(f"occ.occ_{i:02d}", f"apartments.n_{unit}")   # People actuator per dwelling
```

### The data set and helper functions

```python
from pycosim.components import load_household_profiles, select_households

df = load_household_profiles()               # the whole packaged table (a copy)
long = select_households(commute="long", hhsize=[2, 3, 4])
```

The packaged file `nhts_household_profiles.csv.gz` holds **13,548 households**: all 5,548
households with a hybrid/electric/alternative-fuel vehicle and a random sample of 8,000 of
the other 106,949 households (seed 2017). Columns: `houseid`, `group`, `hhsize`,
`n_travelers`, `income_cat`, `vehicles`, `urban`, `state`, `commute_min`,
`first_departure_min`, `last_return_min`, and `p00` … `p95` — the fraction of household
members at home in each 15-minute slot of the survey day.

**Method** (`tools/nhts/build_household_profiles.py`):

* per person, at 15-minute resolution over the travel day 00:00–24:00, a person is at home
  before the first trip starts and after the last trip ends, and away in between;
* household members without any trip in the diary stayed home all day;
* the household profile is the fraction of members at home;
* the commute proxy `commute_min` is the duration of the first trip of the member who leaves
  home earliest.

**Limitations.** NHTS records one travel day per household, so day-to-day variability comes
from re-sampling similar households (`resample="daily"`), not from the same household.
Trip purposes are not part of the extract, so a return home in the middle of the day is
not detected (people are assumed away between first departure and last return). The commute
proxy is the first trip of the day, which is not always a commute. Weekdays and weekends are
not distinguished. Use the data for behavioural diversity and timing, not as a calibrated
occupancy model for a specific building.

### OccupancyMLP

A data-driven model (scikit-learn multi-layer perceptron; `pip install -e ".[ml]"`) that
learns the expected fraction at home from time of day and household size for a household
selection. It is a plain helper object, not a component — call it from your own component.

```python
from pycosim.components import OccupancyMLP, select_households

mlp = OccupancyMLP(hidden_layer_sizes=(32, 32), random_state=0, max_iter=300)
mlp.fit(select_households(commute="long"))
mlp.predict(hour=8.5, hhsize=3)            # -> array([fraction at home])

@pc.component(outputs="people")
def expected_people(t, hhsize=3):
    return float(mlp.predict(t.hour + t.minute / 60, hhsize)[0] * hhsize)
```

---

## AI decision making: LLMSupervisor

An LLM-in-the-loop supervisory controller with guardrails. Full guide:
[AI and RL](ai-and-rl.md#llm-in-the-loop-control).

```python
LLMSupervisor(name, backend, intent, setpoints, observations=(), step_size="1h",
              max_change=None, timeout=30.0, occupied_hours=None, decimals=2)
```

| Parameter | Default | Meaning |
|---|---|---|
| `backend` | — | an LLM backend object (below) |
| `intent` | — | what the operator wants, in plain language |
| `setpoints` | — | `{name: (min, max, default)}` (or `{name: {"min":..., "max":..., "default":...}}`): the decisions the LLM may make; each becomes an output |
| `observations` | `()` | `{input_name: "description [unit]"}` or a list of names: inputs shown to the LLM |
| `step_size` | `"1h"` | decision interval (outputs are held in between) |
| `max_change` | `None` | largest allowed change of any set-point per decision |
| `timeout` | 30.0 s | wait this long for an answer, then fall back |
| `occupied_hours` | `None` | adds an `occupied` flag (weekdays within these hours) to the prompt |
| `decimals` | 2 | rounding of observations in the prompt |

`LLMSupervisor(...).make_async(delay="15min")` lets it think in the background while the
simulation goes on ([asynchronous components](smmm.md#asynchronous-components)).

In: one port per observation. Out: one port per set-point (initial = its default, `hold`), `valid`
[1 if the LLM answer was used, 0 on fallback], `latency` [s], `reason` [text from the
model].

**Guardrails** applied to every answer: (1) parse the first JSON object, (2) keep only
declared set-points with finite numbers, (3) clip to `[min, max]`, (4) limit the change to
`max_change`, (5) on timeout, refusal, error or unusable output hold the last valid values
(initially the defaults). A call that times out is abandoned, so later decisions never wait
behind it. Counts are in `res.meta["component_metadata"][name]` (`calls`,
`valid`, `fallback`, `clipped`, `rate_limited`).

### Backends

| Backend | Constructor (defaults) | Notes |
|---|---|---|
| `AnthropicBackend` | `(model="claude-opus-5-5", max_tokens=16000, effort=None, server_fallback=True, client=None)` | Claude via the official `anthropic` SDK (`pip install anthropic`); credentials from `ANTHROPIC_API_KEY` or an `ant auth login` profile |
| `OpenAIBackend` | `(model, base_url="https://api.openai.com/v1", api_key=None, timeout=60.0, extra=None)` | OpenAI or any OpenAI-compatible server (vLLM, LM Studio, …); key from `api_key` or `OPENAI_API_KEY` |
| `OllamaBackend` | `(model="llama3.1", host="http://localhost:11434", timeout=120.0)` | local models served by Ollama |
| `OfflineStandInBackend` | `()` | **not an LLM**: deterministic keyword rules for tests and demos without a key |
| `FaultInjectionBackend` | `(inner, p_malformed=0, p_out_of_range=0, p_extra_keys=0, p_refusal=0, p_timeout=0, seed=0)` | wraps a backend and injects failures with the given probabilities |
| `RecordingBackend` | `(inner, path)` | wraps a backend; appends every exchange (answer or error) to a JSON-lines file |
| `ReplayBackend` | `(path, strict=True)` | answers from a recorded file (exact prompt match); recorded errors are raised again as `ReplayedError` |

**`OfflineStandInBackend` is not a language model.** It looks for keywords in the intent
(`save`, `cost`, `economy`, `minimise`, … → economy profile 26 / 19 °C; `nursing`, `elderly`,
`hospital`, `comfort`, … → comfort profile 23.5 / 22.5 °C; `office`, `work` → 24 / 21 °C;
otherwise 24 / 21 °C), sets back to 29 / 15.6 °C when the prompt says the building is
unoccupied, and adds 1 °C to the cooling set-point in the economy profile when the price is
≥ 0.2 $/kWh. It answers in the same JSON format as a real model so you can test the whole
pipeline offline. Results obtained with it say nothing about LLM behaviour.

```python
from pycosim.components import LLMSupervisor, OfflineStandInBackend

sim.add(LLMSupervisor(
    "llm", OfflineStandInBackend(),
    intent="This is a nursing home; residents are elderly. Keep them comfortable.",
    setpoints={"clg_sp": (21.0, 30.0, 24.0), "htg_sp": (15.0, 23.0, 20.0)},
    observations={"T_zone": "core zone air temperature [C]", "price": "electricity price [$/kWh]"},
    step_size="1h", max_change=3.0, occupied_hours=(7, 19),
))
sim.connect("office.T_zone", "llm.T_zone")
sim.connect("tariff.price", "llm.price")
sim.connect("llm.clg_sp", "office.clg_sp")
```

Also available: `extract_json(text)` (returns the first JSON object in a text or `None`) and
the base class `LLMBackend` for your own backend (implement `complete(system, prompt) -> str`).
