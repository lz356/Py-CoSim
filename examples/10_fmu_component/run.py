"""Example 10 - An FMU next to Python components (no EnergyPlus required, runs in ~10 s).

Py-CoSim does not need FMUs, but it can use them: ``FMUComponent`` wraps any
co-simulation FMU (FMI 2.0 or 3.0), e.g. a model exported from Modelica, so it
can be coupled with EnergyPlus and Python components. Here a single-zone house
packaged as an FMU is cooled by a Python PI controller and heat pump, and
driven by EPW weather:

    weather  --T_out, GHI--------> zone (FMU) --T_zone--> cooling (PI) --u--> heat_pump
    schedule --clg_sp--> cooling.setpoint                 heat_pump --Q_hvac--> zone

Without ``--fmu``, the demo FMU is built from ``thermal_zone_fmu.py`` with
pythonfmu. Requirements: ``pip install fmpy pythonfmu``.

Things to try:
* ``--fmu path/to/your.fmu`` with your own FMU (adapt the ``inputs=``/``outputs=``
  mapping in ``build()``; the error message lists the FMU's variables),
* change the FMU parameters (``parameters=``) or the controller gains,
* give the FMU its own communication step, e.g. ``step_size="30min"``.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pandas as pd  # noqa: E402

import pycosim as pc  # noqa: E402
from pycosim.components import EPWWeather, FMUComponent, PIController, ThermostatSchedule  # noqa: E402
from _common import TUCSON_EPW  # noqa: E402

CAPACITY_W = 9000.0  # cooling capacity of the heat pump (about 2.5 tons)


@pc.component(outputs=["Q_hvac", "P_elec"])
def heat_pump(t, u=0.0, T_out=35.0):
    """Part load ``u`` in [0, 1] -> heat added to the zone [W] (negative: cooling) and electric power [W]."""
    q_cool = CAPACITY_W * min(max(u, 0.0), 1.0)
    cop = max(2.0, 4.5 - 0.06 * (T_out - 25.0))  # less efficient when it is hot outside
    return {"Q_hvac": -q_cool, "P_elec": q_cool / cop}


def build_demo_fmu() -> Path:
    """Build ``output/ThermalZone.fmu`` from ``thermal_zone_fmu.py`` with pythonfmu."""
    try:
        from pythonfmu import FmuBuilder
    except ImportError:
        sys.exit("The demo FMU is built with pythonfmu: pip install pythonfmu  (or pass --fmu your_model.fmu)")
    out = HERE / "output"
    out.mkdir(parents=True, exist_ok=True)
    return Path(FmuBuilder.build_FMU(HERE / "thermal_zone_fmu.py", dest=out))


def build(fmu_path: Path, days: int, step: str) -> pc.CoSimulation:
    start = pd.Timestamp("2023-07-10")
    sim = pc.CoSimulation(start, start + pd.Timedelta(days=days), step, name="10_fmu",
                          output_dir=HERE / "output", log_level="WARNING")
    zone = FMUComponent(
        "zone", fmu_path,
        # port name -> FMU variable: restrict and rename the FMU's variables
        inputs={"T_out": "TOut", "GHI": "HGloHor", "Q_hvac": "QHVAC"},
        outputs={"T_zone": "TZon", "Q_env": "QEnv"},
        parameters={"UA": 165.0, "A_sol": 6.0, "T_start": 27.0},
        # T_zone is a sample at the communication point (the default, "instant");
        # Q_env is the mean over the step that just ended
        kinds={"Q_env": "average"},
    )
    sim.add(
        EPWWeather("weather", TUCSON_EPW, fields=["T_out", "GHI"]),
        zone,
        ThermostatSchedule("schedule", occupied_hours=(7, 22), cooling=(24.0, 28.0), weekend_occupied=True),
        PIController("cooling", kp=0.5, ki=0.5 / 3600, reverse=True, u_min=0.0, u_max=1.0),
        heat_pump,
    )
    sim.connect("weather.T_out", "zone.T_out")
    sim.connect("weather.GHI", "zone.GHI")
    sim.connect("zone.T_zone", "cooling.measurement")  # the FMU closes the loop: no algebraic loop
    sim.connect("schedule.clg_sp", "cooling.setpoint")
    sim.connect("cooling.u", "heat_pump.u")
    sim.connect("weather.T_out", "heat_pump.T_out")
    sim.connect("heat_pump.Q_hvac", "zone.Q_hvac")
    return sim


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fmu", type=Path, help="co-simulation FMU to use instead of the demo FMU")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--step", default="15min", help="master step (default 15min)")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    fmu_path = args.fmu or build_demo_fmu()
    print(f"FMU: {fmu_path}")
    sim = build(fmu_path, args.days, args.step)
    print(sim["zone"].describe())
    res = sim.run()

    hours = res.meta["step_s"] / 3600
    T, sp = res["zone.T_zone"], res["schedule.clg_sp"]
    occupied = sp < 25.0
    cooling_kwh = -res["heat_pump.Q_hvac"].sum() * hours / 1000
    elec_kwh = res["heat_pump.P_elec"].sum() * hours / 1000
    print(f"Cooling delivered {cooling_kwh:.0f} kWh (thermal), electricity {elec_kwh:.0f} kWh, "
          f"peak {res['heat_pump.P_elec'].max() / 1000:.2f} kW")
    print(f"Zone temperature when occupied: mean {T[occupied].mean():.2f} C, max {T[occupied].max():.2f} C; "
          f"{((T > sp + 1.0) & occupied).sum() * hours:.1f} h more than 1 K above the set-point")
    print(f"Mean heat gain through the envelope: {res['zone.Q_env'].mean() / 1000:.2f} kW")

    if not args.no_plot:
        fig = res.plot(["zone.T_zone", "schedule.clg_sp", "weather.T_out"], ["heat_pump.P_elec", "zone.Q_env"],
                       title="Example 10: FMU zone + Python PI controller and heat pump")
        out = HERE / "output" / "fmu_zone.png"
        fig.savefig(out, dpi=150)
        print(f"figure saved to {out}")


if __name__ == "__main__":
    main()
