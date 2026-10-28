"""EnergyPlus integration code paths that do *not* need an EnergyPlus installation.

(Everything that actually runs EnergyPlus lives in ``test_energyplus.py``.)
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pycosim.core.errors import ConfigurationError, HandleNotFoundError
from pycosim.energyplus import discovery
from pycosim.energyplus.component import (
    EnergyPlusComponent,
    _explain_missing,
    _guess_unit,
    _parse_actuator,
    _parse_meter,
    _parse_pair,
)
from pycosim.energyplus.handles import parse_api_catalogue
from pycosim.energyplus.model import EnergyPlusModel, ModelError

# The format written by EnergyPlus 22.2's ``list_available_api_data_csv`` (headers, trailing ';' on actuators,
# no units) plus a few variants (units in brackets, blank lines, unknown sections) the parser must tolerate.
CATALOGUE = """**ACTUATORS**
Actuator,Weather Data,Outdoor Dry Bulb,Environment;
Actuator,Schedule:Compact,Schedule Value,CLGSETP_SCH;
Actuator,People,Number of People,CORE_ZN PEOPLE,[ ]

**INTERNAL_VARIABLES**
InternalVariable,Zone Floor Area,CORE_ZN,[m2]
**PLUGIN_GLOBAL_VARIABLES**
PluginGlobalVariable,my_global
**TRENDS**
**METERS**
OutputMeter,Electricity:Facility,[J]
OutputMeter,Gas:Facility
**VARIABLES**
OutputVariable,Site Outdoor Air Drybulb Temperature,ENVIRONMENT
OutputVariable,Zone Mean Air Temperature,CORE_ZN,[C]
SomethingNew,ignored,row
"""


def test_parse_api_catalogue():
    df = parse_api_catalogue(CATALOGUE)
    assert list(df.columns) == ["kind", "name", "control", "key", "unit", "spec"]
    assert df["kind"].value_counts().to_dict() == {"actuator": 3, "meter": 2, "variable": 2,
                                                   "internal_variable": 1, "plugin_global": 1}
    act = df[df["kind"] == "actuator"].reset_index(drop=True)
    assert act.loc[1, ["name", "control", "key"]].tolist() == ["Schedule:Compact", "Schedule Value", "CLGSETP_SCH"]
    assert ast.literal_eval(act.loc[1, "spec"]) == ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")
    assert act.loc[2, "key"] == "CORE_ZN PEOPLE" and act.loc[2, "unit"] == ""
    meters = df[df["kind"] == "meter"].reset_index(drop=True)
    assert meters.loc[0, "name"] == "Electricity:Facility" and meters.loc[0, "unit"] == "J"
    assert ast.literal_eval(meters.loc[1, "spec"]) == "Gas:Facility"
    var = df[(df["kind"] == "variable") & (df["key"] == "CORE_ZN")].iloc[0]
    assert var["name"] == "Zone Mean Air Temperature" and var["unit"] == "C"
    assert ast.literal_eval(var["spec"]) == ("Zone Mean Air Temperature", "CORE_ZN")
    iv = df[df["kind"] == "internal_variable"].iloc[0]
    assert (iv["name"], iv["key"], iv["unit"]) == ("Zone Floor Area", "CORE_ZN", "m2")


def test_parse_api_catalogue_empty():
    df = parse_api_catalogue("")
    assert df.empty and list(df.columns) == ["kind", "name", "control", "key", "unit", "spec"]


def test_explain_missing_suggests_close_matches():
    missing = [("actuator", "clg_sp", ("Schedule:Compact", "Schedule Value", "CLGSETP_SHC")),
               ("sensor", "T", ("Zone Mean Air Temperatur", "Core_ZN")),
               ("meter", "E", ("Electricity:Facilty",))]
    text = _explain_missing("office", missing, CATALOGUE)
    assert text.startswith("office: EnergyPlus could not find the following handles:")
    assert "- actuator 'clg_sp': ('Schedule:Compact', 'Schedule Value', 'CLGSETP_SHC')" in text
    lines = text.splitlines()
    i = lines.index("  - actuator 'clg_sp': ('Schedule:Compact', 'Schedule Value', 'CLGSETP_SHC')")
    assert lines[i + 1].strip().startswith("did you mean: Actuator,Schedule:Compact,Schedule Value,CLGSETP_SCH")
    assert "OutputVariable,Zone Mean Air Temperature,CORE_ZN" in text
    assert "OutputMeter,Electricity:Facility" in text
    assert "pycosim handles" in text
    assert "did you mean" not in _explain_missing("x", [("meter", "m", ("Nothing:Similar",))], "")


def test_handle_not_found_error_keeps_missing_list():
    err = HandleNotFoundError("msg", [("sensor", "T", ("a", "b"))])
    assert isinstance(err, ConfigurationError) and err.missing == [("sensor", "T", ("a", "b"))]
    assert HandleNotFoundError("msg").missing == []


# ------------------------------------------------------------------ spec parsing
def test_spec_parsers():
    assert _parse_pair("T", ("Zone Mean Air Temperature", "Core_ZN"), "sensor") == (
        "Zone Mean Air Temperature", "Core_ZN")
    assert _parse_pair("T", "Site Outdoor Air Drybulb Temperature", "sensor")[1] == "Environment"
    assert _parse_pair("T", {"variable": "V"}, "sensor") == ("V", "Environment")
    assert _parse_pair("T", {"variable": "V", "key": "K"}, "sensor") == ("V", "K")
    assert _parse_meter("E", "Electricity:Facility") == ("Electricity:Facility", "J")
    assert _parse_meter("E", ("Electricity:Facility", "kWh")) == ("Electricity:Facility", "kWh")
    assert _parse_meter("E", {"meter": "Gas:Facility", "unit": "W"}) == ("Gas:Facility", "W")
    assert _parse_actuator("a", ("Schedule:Compact", "Schedule Value", "X")) == ("Schedule:Compact",
                                                                               "Schedule Value", "X")
    assert _parse_actuator("a", {"component_type": "A", "control_type": "B", "key": "C"}) == ("A", "B", "C")


@pytest.mark.parametrize("call", [
    lambda: _parse_pair("T", ("only one",), "sensor"),
    lambda: _parse_pair("T", 42, "sensor"),
    lambda: _parse_meter("E", ("Electricity:Facility", "BTU")),
    lambda: _parse_meter("E", 3.0),
    lambda: _parse_actuator("a", ("Schedule:Compact", "Schedule Value")),
])
def test_spec_parser_errors(call):
    with pytest.raises(ConfigurationError):
        call()


def test_guess_unit():
    assert _guess_unit("Zone Mean Air Temperature") == "C"
    assert _guess_unit("Zone Air Relative Humidity") == "%"
    assert _guess_unit("Zone Air Humidity Ratio") == "kgWater/kgDryAir"
    assert _guess_unit("Facility Total Electricity Demand Rate") == "W"
    assert _guess_unit("Zone People Occupant Count") == "people"
    assert _guess_unit("Something Else") is None


# ------------------------------------------------------- component construction
def test_component_ports_from_handle_specs(tmp_path):
    ep = EnergyPlusComponent(
        "office", tmp_path / "missing.idf", tmp_path / "missing.epw",
        sensors={"T_core": ("Zone Mean Air Temperature", "Core_ZN")},
        meters={"P": ("Electricity:Facility", "W"), "E": "Electricity:Facility"},
        internal_variables={"A": ("Zone Floor Area", "Core_ZN")},
        actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")},
        timestep=4,
    )
    assert ep.direct_feedthrough is False
    assert list(ep.output_ports) == ["T_core", "P", "E", "A"]
    assert ep.output_ports["T_core"].unit == "C" and ep.output_ports["P"].unit == "W"
    assert ep.output_ports["E"].unit == "J"
    assert list(ep.input_ports) == ["clg_sp"] and ep.input_ports["clg_sp"].default is None
    assert ep.step_size == 900.0


@pytest.mark.parametrize("kwargs, message", [
    ({"executor": "cluster"}, "executor"),
    ({"sizing": "magic"}, "sizing"),
    ({"timestep": 7}, "timestep"),
    ({"sensors": {"x": ("V", "K")}, "actuators": {"x": ("A", "B", "C")}}, "both as sensor and actuator"),
])
def test_component_constructor_validation(tmp_path, kwargs, message):
    with pytest.raises(ConfigurationError, match=message):
        EnergyPlusComponent("ep", tmp_path / "m.idf", tmp_path / "w.epw", **kwargs)


def test_component_missing_weather_file_fails_setup(make_sim, tmp_path):
    from pycosim.core.errors import ComponentError

    sim = make_sim(log_level="CRITICAL")
    sim.add(EnergyPlusComponent("ep", tmp_path / "m.epJSON", tmp_path / "nope.epw"))
    with pytest.raises(ComponentError) as info:
        sim.run()
    assert isinstance(info.value.original, FileNotFoundError) and info.value.phase == "setup"


# --------------------------------------------------------------- model helpers
def tiny_model():
    return EnergyPlusModel({
        "Version": {"Version 1": {"version_identifier": "22.2"}},
        "RunPeriod": {"RP": {"begin_month": 12, "begin_day_of_month": 30, "end_month": 1, "end_day_of_month": 2,
                             "use_weather_file_holidays_and_special_days": "No", "day_of_week_for_start_day": "Sunday"}},
        "Zone": {"Core_ZN": {"x_origin": 2.0, "y_origin": 4.0, "z_origin": 1.0, "floor_area": 10.0, "volume": 30.0,
                             "ceiling_height": 3.0}},
        "BuildingSurface:Detailed": {"Floor": {"vertices": [
            {"vertex_x_coordinate": 0.0, "vertex_y_coordinate": 0.0, "vertex_z_coordinate": 0.0},
            {"vertex_x_coordinate": 2.0, "vertex_y_coordinate": 5.0, "vertex_z_coordinate": 0.0}]}},
        "Daylighting:ReferencePoint": {"P": {"x_coordinate_of_reference_point": 1.0,
                                             "y_coordinate_of_reference_point": 2.0,
                                             "z_coordinate_of_reference_point": 0.8}},
    })


def test_model_run_period_across_new_year():
    first, last, year = tiny_model().get_run_period()
    assert (first.isoformat(), last.isoformat(), year) == ("2023-12-30", "2024-01-02", None)


def test_model_set_run_period_keeps_flags_and_writes_years():
    m = tiny_model()
    m.set_run_period("2024-02-27", "2024-03-01")
    (name, rp), = m.objects("RunPeriod").items()
    assert name == "PyCoSim RunPeriod"
    assert rp["use_weather_file_holidays_and_special_days"] == "No"
    assert "day_of_week_for_start_day" not in rp  # the calendar year defines the weekday
    assert (rp["begin_year"], rp["end_year"]) == (2024, 2024)
    assert m.get_run_period()[2] == 2024
    with pytest.raises(ModelError):
        m.set_run_period("2024-03-02", "2024-03-01")


def test_model_scale_floor_area_geometry():
    m = tiny_model().scale_floor_area(4.0)
    zone = m.get("Zone", "core_zn")
    assert (zone["x_origin"], zone["y_origin"], zone["z_origin"]) == (4.0, 8.0, 1.0)
    assert (zone["floor_area"], zone["volume"], zone["ceiling_height"]) == (40.0, 120.0, 3.0)
    v = m.get("BuildingSurface:Detailed", "Floor")["vertices"][1]
    assert (v["vertex_x_coordinate"], v["vertex_y_coordinate"], v["vertex_z_coordinate"]) == (4.0, 10.0, 0.0)
    p = m.get("Daylighting:ReferencePoint", "P")
    assert (p["x_coordinate_of_reference_point"], p["z_coordinate_of_reference_point"]) == (2.0, 0.8)
    with pytest.raises(ModelError, match="positive"):
        tiny_model().scale_floor_area(0)
    simple = tiny_model()
    simple.add("Wall:Exterior", "W1", construction_name="c")
    with pytest.raises(ModelError, match="detailed geometry"):
        simple.scale_floor_area(2.0)


def test_model_object_helpers_and_errors(tmp_path):
    m = tiny_model()
    assert m.version == "22.2" and m.zone_names == ["Core_ZN"] and m.timesteps_per_hour == 6
    assert m.classes() == sorted(m.data)
    with pytest.raises(KeyError, match="no Output:Meter"):
        m.get("Output:Meter")
    with pytest.raises(KeyError, match="No Zone named 'Attic'"):
        m.get("Zone", "Attic")
    m.add("Zone", "Attic", floor_area=5.0)
    with pytest.raises(KeyError, match="specify a name"):
        m.get("Zone")
    m.set_field("Zone", "attic", "multiplier", 2)
    assert m.get("Zone", "Attic")["multiplier"] == 2
    m.remove("Zone", "Attic")
    assert m.names("Zone") == ["Core_ZN"]
    m.remove("Daylighting:ReferencePoint")
    assert "Daylighting:ReferencePoint" not in m.data
    m.add_output_variable("Zone Mean Air Temperature", "Core_ZN", frequency="Hourly")
    m.add_output_meter("Electricity:Facility")
    m.enable_sqlite()
    (ov,) = m.objects("Output:Variable").values()
    assert ov == {"key_value": "Core_ZN", "variable_name": "Zone Mean Air Temperature",
                  "reporting_frequency": "Hourly"}
    assert next(iter(m.objects("Output:Meter").values()))["key_name"] == "Electricity:Facility"
    m.set_timesteps_per_hour(12)
    assert m.timesteps_per_hour == 12
    with pytest.raises(ModelError):
        m.set_timesteps_per_hour(7)
    m.use_weather_file_sizing(summer="SummerTypical")
    assert m.objects("SizingPeriod:WeatherFileConditionType")["PyCoSim summer sizing"]["period_selection"] == \
        "SummerTypical"
    assert next(iter(m.objects("SimulationControl").values()))["run_simulation_for_sizing_periods"] == "No"
    copy = m.copy()
    copy.get("Zone", "Core_ZN")["floor_area"] = -1
    assert m.get("Zone", "Core_ZN")["floor_area"] == 10.0
    no_rp = EnergyPlusModel({})
    with pytest.raises(ModelError, match="no RunPeriod"):
        no_rp.get_run_period()
    assert no_rp.version is None
    assert "in-memory" in repr(no_rp)


def test_model_save_and_load_epjson(tmp_path):
    m = tiny_model()
    path = m.save(tmp_path / "sub" / "model.json")
    assert path.name == "model.epJSON" and path.exists()
    again = EnergyPlusModel.load(path)
    assert again.data == m.data and again.source == path
    with pytest.raises(FileNotFoundError):
        EnergyPlusModel.load(tmp_path / "nope.idf")
    (tmp_path / "model.osm").write_text("x")
    with pytest.raises(ModelError, match="Unsupported model format"):
        EnergyPlusModel.load(tmp_path / "model.osm")


def test_model_version_check():
    fake = discovery.EnergyPlusInstall(Path("C:/EnergyPlusV24-1-0"), (24, 1, 0))
    with pytest.raises(ModelError, match="version 22.2 but the installation is 24.1.0"):
        tiny_model()._check_version(fake)
    tiny_model()._check_version(discovery.EnergyPlusInstall(Path("."), (22, 2, 0)))


# ------------------------------------------------------------------- discovery
@pytest.mark.parametrize("text, version", [("EnergyPlusV22-2-0", (22, 2, 0)), ("EnergyPlus-9.6.0", (9, 6, 0)),
                                           ("v24_1", (24, 1, 0)), ("no digits", None)])
def test_parse_version(text, version):
    assert discovery._parse_version(text) == version


def test_make_install_from_fake_folder(tmp_path):
    root = tmp_path / "EnergyPlusV23-1-0"
    root.mkdir()
    assert discovery._make_install(root) is None  # neither executable nor IDD
    (root / "Energy+.idd").write_text("!IDD_Version 23.1.0\n", encoding="latin-1")
    inst = discovery._make_install(root)
    assert inst.version == (23, 1, 0) and inst.version_str == "23.1.0"
    assert not inst.has_python_api
    assert inst.idd == inst.root / "Energy+.idd"
    assert str(inst).startswith("EnergyPlus 23.1.0 at")
    with pytest.raises(discovery.EnergyPlusNotFoundError, match="no Python API"):
        inst.import_api()
    assert discovery._make_install(tmp_path / "does-not-exist") is None


def test_find_energyplus_explicit_path_errors(tmp_path):
    with pytest.raises(discovery.EnergyPlusNotFoundError, match="No EnergyPlus installation found"):
        discovery.find_energyplus(tmp_path / "nothing")
    root = tmp_path / "EnergyPlusV23-1-0"
    root.mkdir()
    (root / "Energy+.idd").write_text("!IDD_Version 23.1.0\n", encoding="latin-1")
    with pytest.raises(discovery.EnergyPlusNotFoundError, match="does not include the Python API"):
        discovery.find_energyplus(root)
    assert discovery.find_energyplus(root, require_api=False).version == (23, 1, 0)


def test_find_energyplus_honours_environment_variable(tmp_path, monkeypatch):
    root = tmp_path / "custom_eplus"
    root.mkdir()
    (root / "Energy+.idd").write_text("!IDD_Version 99.1.0\n", encoding="latin-1")
    monkeypatch.setenv("ENERGYPLUS_DIR", str(root))
    installs = discovery.find_all_energyplus()
    assert installs[0].root == root.resolve() and installs[0].version == (99, 1, 0)  # newest first
    assert discovery.find_energyplus(require_api=False, version="99").root == root.resolve()
    with pytest.raises(discovery.EnergyPlusNotFoundError, match="version 0.1"):
        discovery.find_energyplus(require_api=False, version="0.1")
