"""Example 06 - Travel behaviour drives building loads (transportation x buildings).

Each of the 23 dwelling units of the DOE mid-rise apartment prototype is
occupied by a household sampled from the 2017 National Household Travel
Survey (NHTS). The number of people at home in every unit, every 15 minutes,
follows that household's travel diary and is written into EnergyPlus through
the per-unit ``People`` actuators. Two thermostat policies are compared:

* fixed  : every unit keeps 23.9 C (75 F) all day,
* smart  : an occupancy-responsive thermostat sets the unit up to 26.7 C
           (80 F) whenever nobody is home.

Both policies write the per-unit cooling set-point through EnergyPlus'
``Zone Temperature Control`` actuators. The prototype's own door-switch
thermostat reset (an EMS program) and its window/sliding-door natural
ventilation are removed so that the two policies are compared on equal terms.

Household groups (``--groups``): ``long`` / ``short`` commute (commute proxy
>= 30 min / <= 10 min), ``ev`` / ``nonev`` (households with / without a
hybrid, electric or alternative-fuel vehicle).

All groups x policies run as *one* co-simulation (one EnergyPlus process per
apartment building, all in parallel), with ``--replicates`` independent
household samples each.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import pycosim as pc  # noqa: E402
from pycosim.components import HouseholdOccupancy  # noqa: E402
from _common import APARTMENT, APARTMENT_UNITS, TUCSON_EPW, apartment_zone  # noqa: E402

GROUPS = {"long": dict(commute="long"), "short": dict(commute="short"),
          "ev": dict(group="ev"), "nonev": dict(group="non_ev")}
COOL_SP, SETUP_SP = 23.9, 26.7


def sealed_conventional(model) -> None:
    """Remove the prototype's door-switch thermostat reset (EMS) and natural ventilation."""
    for cls in [c for c in model.classes() if c.startswith("EnergyManagementSystem:")]:
        model.remove(cls)
    model.remove("ZoneVentilation:WindandStackOpenArea")


def apartment_building(name: str, smart: bool = False) -> pc.EnergyPlusComponent:
    sensors = {f"T_{u}": ("Zone Mean Air Temperature", apartment_zone(u)) for u in APARTMENT_UNITS}
    sensors["T_out"] = ("Site Outdoor Air Drybulb Temperature", "Environment")
    meters = {f"Qc_{u}": (f"Cooling:EnergyTransfer:Zone:{apartment_zone(u)}", "W") for u in APARTMENT_UNITS}
    meters["P_elec"] = ("Electricity:Facility", "W")
    meters["P_cooling"] = ("Cooling:Electricity", "W")
    actuators = {f"n_{u}": ("People", "Number of People", f"OCCUP_{u}") for u in APARTMENT_UNITS}
    actuators["occ_schedule"] = ("Schedule:Compact", "Schedule Value", "OCC_APT_SCH")
    actuators.update({f"sp_{u}": ("Zone Temperature Control", "Cooling Setpoint", apartment_zone(u))
                      for u in APARTMENT_UNITS})
    return pc.EnergyPlusComponent(name, APARTMENT, TUCSON_EPW, sensors=sensors, meters=meters,
                                  actuators=actuators, sizing="weather_file", daylight_saving=False,
                                  preprocess=sealed_conventional)


def build(groups=("long", "short"), policies=("fixed", "smart"), replicates: int = 1, days: int = 7,
          seed: int = 2017, output_dir: Path = HERE / "output", name: str = "06_travel") -> pc.CoSimulation:
    start = pd.Timestamp("2023-07-17")
    sim = pc.CoSimulation(start=start, end=start + pd.Timedelta(days=days), step="15min", name=name,
                          output_dir=output_dir, seed=seed, log_level="WARNING")
    sim.add(pc.component(lambda t: 1.0, name="one", outputs="value"))
    for g in groups:
        for r in range(replicates):
            occ = f"occ_{g}_{r}"
            # one household sample per group & replicate, shared by both policies (paired comparison)
            sim.add(HouseholdOccupancy(occ, n_units=len(APARTMENT_UNITS), **GROUPS[g]))
            for policy in policies:
                bname = f"apt_{g}_{policy}_{r}"
                sim.add(apartment_building(bname))
                sim.connect("one.value", f"{bname}.occ_schedule")  # schedule x count = count
                for i, u in enumerate(APARTMENT_UNITS):
                    sim.connect(f"{occ}.occ_{i:02d}", f"{bname}.n_{u}")
                    sim.connect(f"{occ}.occ_{i:02d}", f"{bname}.sp_{u}",
                                transform=(lambda n: COOL_SP if n > 0.05 else SETUP_SP) if policy == "smart"
                                else (lambda n: COOL_SP))
    return sim


def summarize(res: pc.Results) -> pd.DataFrame:
    """Hourly cooling-load profile statistics for every apartment building."""
    df = res.df
    h = res.meta["step_s"] / 3600
    rows = []
    for b in sorted({c.split(".")[0] for c in df.columns if c.startswith("apt_")}):
        parts = b.split("_")  # apt_<group>_<policy>_<replicate>
        g, policy, r = "_".join(parts[1:-2]), parts[-2], parts[-1]
        qc = df[[c for c in df.columns if c.startswith(f"{b}.Qc_")]].sum(axis=1) / 1000  # kW thermal
        hourly = qc.groupby(qc.index.hour).mean()
        occ = df[[c for c in df.columns if c.startswith(f"occ_{g}_{r}.occ_")]].sum(axis=1)
        rows.append({
            "building": b, "group": g, "policy": policy, "replicate": int(r),
            "cooling_load_MWh": qc.sum() * h / 1000,
            "cooling_elec_MWh": df[f"{b}.P_cooling"].sum() * h / 1e6,
            "peak_hour": int(hourly.idxmax()), "valley_hour": int(hourly.idxmin()),
            "peak_to_valley": float(hourly.max() / max(hourly.min(), 1e-6)),
            "mean_people_home": float(occ.mean()),
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--groups", nargs="+", default=["long", "short"], choices=list(GROUPS))
    parser.add_argument("--policies", nargs="+", default=["fixed", "smart"], choices=["fixed", "smart"])
    parser.add_argument("--replicates", type=int, default=1)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    sim = build(args.groups, args.policies, args.replicates, args.days)
    n_eplus = sum(isinstance(c, pc.EnergyPlusComponent) for c in sim.components)
    print(f"{n_eplus} EnergyPlus apartment buildings ({n_eplus * len(APARTMENT_UNITS)} dwellings) in one co-simulation")
    res = sim.run(progress=True)
    table = summarize(res)
    table.to_csv(HERE / "output" / "summary.csv", index=False)
    print(table.groupby(["group", "policy"])[["cooling_load_MWh", "cooling_elec_MWh", "peak_to_valley",
                                               "mean_people_home"]].mean().round(3).to_string())

    if not args.no_plot:
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 4))
        df = res.df
        for b in sorted({c.split(".")[0] for c in df.columns if c.startswith("apt_")}):
            qc = df[[c for c in df.columns if c.startswith(f"{b}.Qc_")]].sum(axis=1) / 1000
            ax.plot(qc.groupby(qc.index.hour).mean(), label=b)
        ax.set_xlabel("Hour of day")
        ax.set_ylabel("Mean apartment cooling load [kW]")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(HERE / "output" / "travel_behavior.png", dpi=150)


if __name__ == "__main__":
    main()
