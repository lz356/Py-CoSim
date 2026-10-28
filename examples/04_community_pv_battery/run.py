"""Example 04 - A grid-interactive community: N EnergyPlus buildings + PV + battery.

Ten small offices (each its own EnergyPlus instance, floor areas varied by
+/-10 %) share a community PV array, a community battery and one grid
connection with a time-of-use tariff. Four operating strategies are compared:

=========== =================================================================
baseline    PV only; offices follow their normal thermostat schedule
precool     + pre-cooling before the 16-21 h price peak, set-up during it
battery     + battery: PV surplus storage, on-peak discharge, peak shaving
coordinated pre-cooling and battery together
=========== =================================================================

All EnergyPlus instances advance in parallel worker processes; the Python
components (PV, battery, controllers, metering) run in the master process.

The grid meter subscribes to every building's power with a pattern
(``"bldg*.P_facility"``) and reads power and price over the 15 minutes that
just ended (``aligned=True``), so the energy of each interval is charged at
the price that applied to it. ``--naive`` reproduces the Py-CoSim 2.0
wiring, where the latest values are multiplied.

    python run.py                      # 10 buildings, 1 summer week, all strategies
    python run.py --buildings 4 --days 2 --strategies baseline coordinated
"""
import argparse
import sys
import time
from functools import partial
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import pycosim as pc  # noqa: E402
from pycosim.components import (  # noqa: E402
    Battery, EPWWeather, GridMeter, PeakShavingController, PrecoolingController, PVSystem, TimeOfUseTariff,
)
from _common import OFFICE_ZONES, TUCSON_EPW, make_office  # noqa: E402

PEAK = (16, 21)
STRATEGIES = ("baseline", "precool", "battery", "coordinated")


def _scale(factor, model):
    model.scale_floor_area(factor)


def build(strategy: str, n_buildings: int = 10, days: int = 7, seed: int = 7, pv_kw_per_bldg: float = 25.0,
          battery_kwh_per_bldg: float = 50.0, start: str = "2023-07-17", output_dir: Path = HERE / "output",
          executor: str = "process", aligned: bool = True) -> pc.CoSimulation:
    """Create the co-simulation for one strategy."""
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {STRATEGIES}")
    t0 = pd.Timestamp(start)
    sim = pc.CoSimulation(start=t0, end=t0 + pd.Timedelta(days=days), step="15min", name=f"04_{strategy}",
                          output_dir=output_dir, seed=seed, log_level="WARNING")
    factors = np.random.default_rng(seed).uniform(0.9, 1.1, n_buildings)
    names = [f"bldg{i:02d}" for i in range(n_buildings)]
    for name, f in zip(names, factors):
        sim.add(make_office(name, preprocess=partial(_scale, float(f)), executor=executor))

    precool = strategy in ("precool", "coordinated")
    use_battery = strategy in ("battery", "coordinated")
    sim.add(EPWWeather("weather", TUCSON_EPW))
    sim.add(TimeOfUseTariff("tariff", peak_hours=PEAK, peak_price=0.30, offpeak_price=0.08))
    sim.add(PVSystem("pv", capacity_kw=pv_kw_per_bldg * n_buildings, tilt=20))
    sim.add(PrecoolingController("thermostat", peak_hours=PEAK, precool_hours=3, base_sp=24.0,
                                 precool_sp=22.0 if precool else 24.0, peak_sp=26.5 if precool else 24.0,
                                 occupied_hours=(6, 21), unoccupied_sp=29.4))
    sim.add(GridMeter("grid", aligned=aligned))

    for name in names:  # one thermostat signal broadcast to all buildings
        sim.connect("thermostat.clg_sp", f"{name}.clg_sp")
    for port in ("GHI", "DNI", "DHI", "T_out", "solar_zenith", "solar_azimuth"):
        sim.connect(f"weather.{port}", f"pv.{port}")
    loads = [f"{n}.P_facility" for n in names]
    sim.add(pc.FunctionComponent(lambda t, P_bldgs=0.0, P_pv=0.0: P_bldgs - P_pv, name="net_load",
                                 outputs="P_net"))
    sim.connect(loads, "net_load.P_bldgs", reduce="sum")
    sim.connect("pv.P_ac", "net_load.P_pv")
    if use_battery:
        cap = battery_kwh_per_bldg * n_buildings
        sim.add(Battery("battery", capacity_kwh=cap, max_charge_kw=cap / 4, soc_init=0.5, soc_min=0.1, soc_max=0.95))
        sim.add(PeakShavingController("dispatch", import_limit_kw=12.0 * n_buildings, charge_kw=cap / 8,
                                      charge_hours=(0, 6), soc_target=0.9))
        sim.connect("net_load.P_net", "dispatch.P_load")
        sim.connect("battery.soc", "dispatch.soc", delay=True)  # battery reports SOC of the previous step
        sim.connect("tariff.is_peak", "dispatch.is_peak")
        sim.connect("dispatch.P_set", "battery.P_set")
    if aligned:  # TIDE: every flow and the price over the interval that just ended
        sim.subscribe("grid.P_load", "bldg*.P_facility", reduce="sum")
        sim.subscribe("grid.P_gen", "pv.P_ac")
        if use_battery:
            sim.subscribe("grid.P_bat", "battery.P")
    elif use_battery:  # Py-CoSim 2.0 wiring: latest values
        sim.connect(["net_load.P_net", "battery.P"], "grid.P_net", reduce="sum")
    else:
        sim.connect("net_load.P_net", "grid.P_net")
    sim.connect("tariff.price", "grid.price")
    return sim


def kpis(res: pc.Results, demand_charge: float = 15.0) -> dict:
    """Community key performance indicators."""
    df = res.df
    h = res.meta["step_s"] / 3600
    imp = df["grid.P_import"] / 1000
    aligned = res.meta.get("component_metadata", {}).get("grid", {}).get("aligned", False)
    # an aligned meter reports at t the interval [t - h, t): use the peak flag of that interval
    peak = (df["tariff.is_peak"].shift(1, fill_value=0) if aligned else df["tariff.is_peak"]) == 1
    bldgs = sorted({c.split(".")[0] for c in df.columns if c.startswith("bldg")})
    occupied = (df.index.hour >= 8) & (df.index.hour < 18) & (df.index.day_of_week < 5)
    discomfort = 0.0
    for b in bldgs:
        temps = df.loc[occupied, [f"{b}.T_{z.lower()}" for z in OFFICE_ZONES]].mean(axis=1)
        discomfort += (temps - 26.0).clip(lower=0).sum() * h
    load = df[[f"{b}.P_facility" for b in bldgs]].sum(axis=1) / 1000
    pv = df["pv.P_ac"] / 1000
    exported = df["grid.P_export"].sum() * h / 1000
    return {
        "load_MWh": load.sum() * h / 1000,
        "import_MWh": imp.sum() * h / 1000,
        "on_peak_import_MWh": imp[peak].sum() * h / 1000,
        "peak_import_kW": df["grid.peak"].iloc[-1],
        "energy_cost_$": df["grid.cost"].iloc[-1],
        "demand_charge_$": demand_charge * df["grid.peak"].iloc[-1],
        "pv_self_consumption_%": 100 * (1 - exported / max(pv.sum() * h, 1e-9)),
        "discomfort_Kh_per_bldg": discomfort / len(bldgs),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--buildings", type=int, default=10)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--strategies", nargs="+", default=list(STRATEGIES), choices=STRATEGIES)
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--naive", action="store_true", help="multiply latest values (Py-CoSim 2.0 wiring)")
    args = parser.parse_args()

    results, rows = {}, {}
    for strategy in args.strategies:
        t0 = time.perf_counter()
        sim = build(strategy, args.buildings, args.days, aligned=not args.naive)
        results[strategy] = res = sim.run(progress=True)
        rows[strategy] = {**kpis(res), "wall_time_s": time.perf_counter() - t0}
    table = pd.DataFrame(rows).T
    print(table.round(2).to_string())
    table.to_csv(HERE / "output" / "kpis.csv")

    if not args.no_plot:
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 1, sharex=True, figsize=(11, 6))
        for strategy, res in results.items():
            axes[0].plot(res.df.index, res["grid.P_import"] / 1000 - res["grid.P_export"] / 1000, lw=1, label=strategy)
        if "battery" in results:
            axes[1].plot(results["battery"].df.index, results["battery"]["battery.soc"], label="battery SOC (battery)")
        if "coordinated" in results:
            axes[1].plot(results["coordinated"].df.index, results["coordinated"]["battery.soc"],
                         label="battery SOC (coordinated)")
        axes[0].set_ylabel("Grid exchange [kW]")
        axes[1].set_ylabel("SOC [-]")
        for ax in axes:
            ax.grid(alpha=0.3)
            ax.legend(fontsize=8, ncol=4)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(HERE / "output" / "community.png", dpi=150)


if __name__ == "__main__":
    main()
