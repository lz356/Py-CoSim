"""Example 03 - Time-of-use pre-cooling with EnergyPlus.

Compares two co-simulations of the same office for a summer work week:

* baseline : constant 24 C cooling set-point during occupied hours,
* precool  : 22 C for 3 h before the 16-21 h price peak, 26.5 C during it.

A GridMeter integrates the electricity cost under the time-of-use tariff.
The script prints energy, on-peak energy, cost, peak demand and comfort.

Things to try: change ``precool_sp``/``peak_sp``/``precool_hours``, the
tariff prices, or replace the controller by your own function.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pandas as pd  # noqa: E402

import pycosim as pc  # noqa: E402
from pycosim.components import GridMeter, PrecoolingController, TimeOfUseTariff  # noqa: E402
from _common import make_office  # noqa: E402

PEAK = (16, 21)


def scenario(name: str, precool: bool, days: int) -> pc.Results:
    start = pd.Timestamp("2023-07-17")  # a Monday
    sim = pc.CoSimulation(start=start, end=start + pd.Timedelta(days=days), step="15min",
                          name=name, output_dir=HERE / "output", log_level="WARNING")
    sim.add(make_office("office"))
    sim.add(TimeOfUseTariff("tariff", peak_hours=PEAK, peak_price=0.30, offpeak_price=0.08))
    sim.add(PrecoolingController("ctrl", peak_hours=PEAK, precool_hours=3, base_sp=24.0,
                                 precool_sp=22.0 if precool else 24.0, peak_sp=26.5 if precool else 24.0,
                                 occupied_hours=(6, 21), unoccupied_sp=29.4))
    sim.add(GridMeter("meter"))
    sim.connect("ctrl.clg_sp", "office.clg_sp")
    sim.subscribe("meter.P_load", "office.P_facility")  # power and price of the same 15 min
    sim.connect("tariff.price", "meter.price")
    return sim.run(progress=True)


def kpis(res: pc.Results) -> dict:
    df = res.df
    h = res.meta["step_s"] / 3600
    peak = df["tariff.is_peak"] == 1
    zones = [c for c in df.columns if c.startswith("office.T_") and c != "office.T_out"]
    occupied = (df.index.hour >= 8) & (df.index.hour < 18) & (df.index.day_of_week < 5)
    excess = (df.loc[occupied, zones].mean(axis=1) - 26.0).clip(lower=0)
    return {
        "energy_kWh": df["office.P_facility"].sum() * h / 1000,
        "on_peak_kWh": df.loc[peak, "office.P_facility"].sum() * h / 1000,
        "cost_$": df["meter.cost"].iloc[-1],
        "peak_kW": df["office.P_facility"].max() / 1000,
        "on_peak_max_kW": df.loc[peak, "office.P_facility"].max() / 1000,
        "discomfort_Kh": excess.sum() * h,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    runs = {"baseline": scenario("03_baseline", False, args.days), "precool": scenario("03_precool", True, args.days)}
    table = pd.DataFrame({k: kpis(v) for k, v in runs.items()}).T
    table.loc["change_%"] = (table.loc["precool"] / table.loc["baseline"].replace(0, float("nan")) - 1) * 100
    print(table.round(2).to_string())
    if not args.no_plot:
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
        for key, res in runs.items():
            axes[0].plot(res.df.index, res["ctrl.clg_sp"], label=f"{key} set-point")
            axes[0].plot(res.df.index, res["office.T_core_zn"], ls="--", label=f"{key} core zone")
            axes[1].plot(res.df.index, res["office.P_facility"] / 1000, label=key)
        axes[0].set_ylabel("Temperature [C]")
        axes[1].set_ylabel("Facility power [kW]")
        for ax in axes:
            ax.legend(fontsize=8)
            ax.grid(alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(HERE / "output" / "precooling.png", dpi=150)


if __name__ == "__main__":
    main()
