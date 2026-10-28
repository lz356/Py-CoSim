"""Shared paths for the examples (each example adds this folder to sys.path)."""
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent
MODELS = EXAMPLES / "models"
WEATHER = EXAMPLES / "weather"
TUCSON_EPW = WEATHER / "USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"
SMALL_OFFICE = MODELS / "small_office.idf"
APARTMENT = MODELS / "apartment_midrise.idf"

# The five conditioned zones of the DOE small office reference building
OFFICE_ZONES = ["Core_ZN", "Perimeter_ZN_1", "Perimeter_ZN_2", "Perimeter_ZN_3", "Perimeter_ZN_4"]

# The 23 dwelling units of the DOE mid-rise apartment prototype: floor (G/M/T) + position
APARTMENT_UNITS = [f + p for f in "GMT" for p in ("N1", "N2", "NE", "NW", "S1", "S2", "SE", "SW")
                   if f + p != "GSE"]  # the ground-floor SE corner is the office


def apartment_zone(unit: str) -> str:
    return f"{unit[0]} {unit[1:]} Apartment"


def make_office(name: str = "office", **kwargs):
    """DOE small office as an EnergyPlus component: zone temperatures, power meters, cooling set-point actuator."""
    import pycosim as pc

    sensors = {f"T_{z.lower()}": ("Zone Mean Air Temperature", z) for z in OFFICE_ZONES}
    sensors["T_out"] = ("Site Outdoor Air Drybulb Temperature", "Environment")
    kwargs.setdefault("model", SMALL_OFFICE)
    kwargs.setdefault("weather", TUCSON_EPW)
    kwargs.setdefault("daylight_saving", False)  # Arizona does not observe daylight-saving time
    return pc.EnergyPlusComponent(
        name,
        sensors=sensors,
        meters={"P_facility": ("Electricity:Facility", "W"), "P_hvac": ("Electricity:HVAC", "W")},
        actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")},
        **kwargs,
    )
