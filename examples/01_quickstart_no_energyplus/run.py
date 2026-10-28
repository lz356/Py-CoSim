"""Example 01 - Your first co-simulation (no EnergyPlus required, runs in ~1 s).

A house (2R2C thermal model) is coupled with weather data, a time-of-use
tariff and a price-aware thermostat written as a plain Python function.

    weather --T_out, GHI--> house --T_zone--> (results)
    tariff  --price-------> thermostat --clg_sp--> house

Things to try:
* change the thermostat logic (it is just a Python function),
* change ``floor_area`` or the tariff's ``peak_hours``,
* replace RCBuilding by an EnergyPlusComponent (see example 02).
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pycosim as pc  # noqa: E402
from pycosim.components import EPWWeather, RCBuilding, TimeOfUseTariff  # noqa: E402
from _common import TUCSON_EPW  # noqa: E402


@pc.component(outputs="clg_sp")
def thermostat(t, price=0.10):
    """Cooling set-point [C]: relax during expensive hours, set up at night."""
    if 7 <= t.hour < 22:
        return 26.0 if price > 0.2 else 24.0
    return 27.0


def build() -> pc.CoSimulation:
    sim = pc.CoSimulation(start="2023-07-03", end="2023-07-10", step="15min",
                          name="01_quickstart", output_dir=HERE / "output")
    sim.add(
        EPWWeather("weather", TUCSON_EPW),
        RCBuilding("house", floor_area=150),
        TimeOfUseTariff("tariff", peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.10),
        thermostat,
    )
    sim.connect("weather.T_out", "house.T_out")
    sim.connect("weather.GHI", "house.GHI")
    sim.connect("tariff.price", "thermostat.price")
    sim.connect("thermostat.clg_sp", "house.clg_sp")
    return sim


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    sim = build()
    print(sim.describe())
    res = sim.run(progress=True)
    print(res.summary())

    hours = res.meta["step_s"] / 3600
    energy_kwh = res["house.P_elec"].sum() * hours / 1000
    cost = (res["house.P_elec"] / 1000 * res["tariff.price"]).sum() * hours
    print(f"Electricity: {energy_kwh:.1f} kWh, cost ${cost:.2f}, max zone temperature {res['house.T_zone'].max():.1f} C")

    if not args.no_plot:
        fig = res.plot(["house.T_zone", "thermostat.clg_sp", "weather.T_out"], "house.P_elec", "tariff.price",
                       title="Example 01: RC house + price-aware thermostat")
        out = HERE / "output" / "quickstart.png"
        fig.savefig(out, dpi=150)
        print(f"figure saved to {out}")


if __name__ == "__main__":
    main()
