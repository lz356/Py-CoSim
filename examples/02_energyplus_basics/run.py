"""Example 02 - EnergyPlus as a co-simulation component.

The DOE small office reference building (EnergyPlus) is controlled by a
Python thermostat schedule. It shows the three kinds of EnergyPlus ports:

* sensors   (EnergyPlus output variables)  -> outputs, e.g. zone temperatures
* meters    (energy meters, here in W)     -> outputs
* actuators (EMS actuators)                -> inputs, e.g. the cooling set-point

Run ``pycosim handles ../models/small_office.idf -w ../weather/<epw> --kind actuator``
to list every sensor/actuator name the model offers.

Options: ``--live`` opens a live (online) plot while EnergyPlus runs;
``--release`` leaves the actuator unconnected so EnergyPlus' own schedule runs.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pycosim as pc  # noqa: E402
from pycosim.components import ThermostatSchedule  # noqa: E402
from pycosim.observers import LivePlot  # noqa: E402
from _common import make_office  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--live", action="store_true", help="show an online plot during the run")
    parser.add_argument("--release", action="store_true", help="do not override EnergyPlus' schedule")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    import pandas as pd

    start = pd.Timestamp("2023-07-03")
    sim = pc.CoSimulation(start=start, end=start + pd.Timedelta(days=args.days), step="15min",
                          name="02_energyplus_basics", output_dir=HERE / "output")
    office = sim.add(make_office())
    schedule = sim.add(ThermostatSchedule("schedule", occupied_hours=(7, 19), cooling=(23.5, 29.0),
                                          heating=(21.0, 15.6)))
    if not args.release:
        sim.connect("schedule.clg_sp", "office.clg_sp")
    if args.live:
        sim.add_observer(LivePlot([["office.T_core_zn", "schedule.clg_sp", "office.T_out"], "office.P_facility"],
                                  every=8, window=4 * 24 * 3))
    print(sim.describe())
    res = sim.run(progress=True)
    print(res.summary())
    meta = res.meta["component_metadata"]["office"]
    print(f"EnergyPlus {meta['energyplus_version']}: {meta['warnings']} warnings, {meta['severe_errors']} severe errors")
    print(f"EnergyPlus files (eplusout.err, .sql, ...) are in {sim.output_path / 'office' / 'eplus'}")

    if not args.no_plot:
        temps = [c for c in res.df.columns if c.startswith("office.T_") and c != "office.T_out"]
        fig = res.plot(temps + ["schedule.clg_sp"], ["office.P_facility", "office.P_hvac"], "office.T_out",
                       title="Example 02: EnergyPlus small office controlled from Python")
        fig.savefig(HERE / "output" / "energyplus_basics.png", dpi=150)
        if args.live:
            import matplotlib.pyplot as plt

            plt.ioff()
            plt.show()


if __name__ == "__main__":
    main()
