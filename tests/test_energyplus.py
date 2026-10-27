"""Integration tests that run EnergyPlus (skipped when no installation with the Python API is found).

Each co-simulation covers at most one or two days at a 15-minute (or hourly) step; most runs are only
a few hours long, so the whole module takes well under a minute.
"""
from __future__ import annotations

import ast
import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

import pycosim as pc
from pycosim import cli
from pycosim.components import Constant, EPWWeather
from pycosim.core.errors import ComponentError, HandleNotFoundError, SynchronizationError
from pycosim.energyplus.discovery import find_energyplus
from pycosim.energyplus.model import EnergyPlusModel, ModelError

pytestmark = pytest.mark.energyplus

try:
    INSTALL = find_energyplus()
except Exception as exc:  # pragma: no cover - depends on the machine
    pytest.skip(f"EnergyPlus with Python API not found: {exc}", allow_module_level=True)

T_CORE = ("Zone Mean Air Temperature", "Core_ZN")
T_OUT = ("Site Outdoor Air Drybulb Temperature", "Environment")
CLG_SP = ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")


@pytest.fixture(scope="module")
def office_model(idf_path) -> EnergyPlusModel:
    return EnergyPlusModel.load(idf_path, INSTALL)


def ep_sim(tmp_path, start="2023-07-03", end="2023-07-04", step="15min", **kw):
    kw.setdefault("save", False)
    kw.setdefault("log_level", "WARNING")
    return pc.CoSimulation(start, end, step, output_dir=tmp_path, **kw)


# ======================================================================= install
def test_installation_found():
    assert INSTALL.version[:2] >= (9, 4)
    assert INSTALL.has_python_api and INSTALL.executable.exists() and INSTALL.idd.exists()
    assert find_energyplus(INSTALL.root).root == INSTALL.root
    assert find_energyplus(INSTALL.executable).root == INSTALL.root
    assert find_energyplus(version=INSTALL.version_str[:4]).root == INSTALL.root
    api = INSTALL.import_api()
    assert api.__name__ == "EnergyPlusAPI"


def test_cli_doctor(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert f"EnergyPlus {INSTALL.version_str}" in out and "pyenergyplus import ok" in out


# ========================================================================= model
def test_model_load_edit_save_round_trip(office_model, tmp_path):
    m = office_model
    assert m.version.startswith(f"{INSTALL.version[0]}.{INSTALL.version[1]}")
    assert {"Core_ZN", "Perimeter_ZN_1", "Perimeter_ZN_2", "Perimeter_ZN_3", "Perimeter_ZN_4"} <= set(m.zone_names)
    assert {"CLGSETP_SCH", "HTGSETP_SCH", "BLDG_OCC_SCH"} <= set(m.names("Schedule:Compact"))
    assert m.get_run_period() == (date(2023, 8, 1), date(2023, 8, 2), None)
    assert m.timesteps_per_hour == 1
    assert isinstance(m.get("Zone", "core_zn"), dict)
    assert "small_office.idf" in repr(m)

    assert m.has_daylight_saving  # DOE prototype models define US daylight-saving time
    no_dst = m.copy()
    no_dst.remove_daylight_saving()
    assert not no_dst.has_daylight_saving and m.has_daylight_saving
    assert all(rp["use_weather_file_daylight_saving_period"] == "No" for rp in no_dst.objects("RunPeriod").values())

    edited = m.copy()
    edited.set_timesteps_per_hour(4)
    edited.set_run_period("2023-07-03", date(2023, 7, 4))
    edited.use_weather_file_sizing()
    edited.add_output_variable(*T_CORE)
    assert m.timesteps_per_hour == 1 and m.get_run_period()[0] == date(2023, 8, 1)  # the original is untouched
    assert edited.timesteps_per_hour == 4
    assert edited.get_run_period() == (date(2023, 7, 3), date(2023, 7, 4), 2023)
    assert "SizingPeriod:DesignDay" not in edited.data
    assert len(edited.objects("SizingPeriod:WeatherFileConditionType")) == 2

    path = edited.save(tmp_path / "office.epJSON")
    reloaded = EnergyPlusModel.load(path)
    assert reloaded.data == edited.data
    assert json.loads(path.read_text(encoding="utf-8"))["Timestep"] == {"Timestep 1": {"number_of_timesteps_per_hour": 4}}


def test_idf_conversion_is_cached(idf_path, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("PYCOSIM_CACHE_DIR", str(cache))
    first = EnergyPlusModel.load(idf_path, INSTALL)
    cached = list(cache.glob("small_office-*.epJSON"))
    assert len(cached) == 1
    mtime = cached[0].stat().st_mtime_ns
    second = EnergyPlusModel.load(idf_path, INSTALL)
    assert second.data == first.data and cached[0].stat().st_mtime_ns == mtime


def test_invalid_idf_raises_model_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCOSIM_CACHE_DIR", str(tmp_path / "cache"))
    bad = tmp_path / "garbage.idf"
    bad.write_text("this is not an IDF file;\n", encoding="utf-8")
    with pytest.raises(ModelError):
        EnergyPlusModel.load(bad, INSTALL)


def test_scale_floor_area_doubles_zone_floor_area(idf_path, epw_path, office_model, tmp_path):
    scaled_models = []

    def double(model):
        scaled_models.append(model)
        model.scale_floor_area(2.0)

    areas = {"A_core": ("Zone Floor Area", "Core_ZN"), "A_p1": ("Zone Floor Area", "Perimeter_ZN_1")}
    sim = ep_sim(tmp_path, end="2023-07-03 01:00", name="scale")
    base = sim.add(pc.EnergyPlusComponent("base", idf_path, epw_path, internal_variables=areas))
    big = sim.add(pc.EnergyPlusComponent("big", office_model, epw_path, internal_variables=areas, preprocess=double))
    res = sim.run()
    assert len(scaled_models) == 1 and scaled_models[0] is not office_model  # the component edits a copy
    assert office_model.get("Zone", "Core_ZN") == EnergyPlusModel.load(idf_path, INSTALL).get("Zone", "Core_ZN")
    for port in areas:
        assert res[f"base.{port}"].iloc[0] > 50
        np.testing.assert_allclose(res[f"big.{port}"], 2.0 * res[f"base.{port}"], rtol=1e-9)
        assert big.info["internal_variables"][port] == pytest.approx(2 * base.info["internal_variables"][port])
    written = json.loads((tmp_path / "scale" / "big" / "in.epJSON").read_text(encoding="utf-8"))
    assert written["Timestep"]["Timestep 1"]["number_of_timesteps_per_hour"] == 4


# =========================================================== co-simulation runs
@pytest.fixture(scope="module")
def override_run(idf_path, epw_path, tmp_path_factory):
    """One day, two office buildings in parallel; one gets its cooling set-point overridden 10:00-12:00."""
    out = tmp_path_factory.mktemp("override")

    def office(name):
        return pc.EnergyPlusComponent(
            name, idf_path, epw_path, sizing="weather_file", daylight_saving=False,
            sensors={"T_core": T_CORE, "T_out": T_OUT,
                     "sp": ("Zone Thermostat Cooling Setpoint Temperature", "Core_ZN")},
            meters={"P_W": ("Electricity:Facility", "W"), "P_kW": ("Electricity:Facility", "kW"),
                    "E_kWh": ("Electricity:Facility", "kWh"), "E_J": "Electricity:Facility"},
            actuators={"clg_sp": CLG_SP},
        )

    @pc.component(outputs="clg_sp")
    def override(t):
        # a dict is needed to publish None (a bare None return means "keep the previous output")
        return {"clg_sp": 30.0 if 10 <= t.hour < 12 else None}

    sim = ep_sim(out, name="override", save=True)
    sim.add(office("base"), office("ovr"), override, EPWWeather("wx", epw_path))
    sim.connect("override.clg_sp", "ovr.clg_sp")
    res = sim.run()
    return sim, res


def test_sensors_and_meters(override_run):
    sim, res = override_run
    df = res.df
    assert len(df) == 96 and df.index[0] == datetime(2023, 7, 3)
    assert df["base.T_core"].between(15, 35).all()
    # CLGSETP_SCH: 24 C from 06:00 to 22:00 on weekdays, 26.7 C otherwise. With daylight saving removed the
    # schedule follows the co-simulation clock (values observed at t describe the step ending at t).
    sp = df["base.sp"]
    assert (sp.loc["2023-07-03 06:15":"2023-07-03 22:00"] == 24.0).all()
    assert (sp.loc["2023-07-03 00:15":"2023-07-03 06:00"] == 26.7).all()
    assert (sp.loc["2023-07-03 22:15":] == 26.7).all()
    p = df["base.P_W"].iloc[1:]
    assert (p > 0).all()
    e_j = df["base.E_J"].iloc[1:]
    np.testing.assert_allclose(p, e_j / 900.0, rtol=1e-12)
    np.testing.assert_allclose(df["base.P_kW"].iloc[1:] * 1000.0, p, rtol=1e-12)
    np.testing.assert_allclose(df["base.E_kWh"].iloc[1:] * 3.6e6, e_j, rtol=1e-12)
    assert res.units["base.P_W"] == "W" and res.units["base.E_J"] == "J" and res.units["base.T_core"] == "C"
    info = sim["base"].info
    assert info["timesteps_per_hour"] == 4 and info["exit_code"] == 0 and info["severe_errors"] == 0
    meta = res.meta["component_metadata"]["base"]
    assert meta["executor"] == "process" and meta["energyplus_version"] == INSTALL.version_str
    folder = sim.output_path / "base"
    assert (folder / "in.epJSON").exists() and (folder / "eplus" / "eplusout.err").exists()
    assert (folder / "worker.log").exists()


def test_energyplus_weather_matches_epw_component(override_run):
    _, res = override_run
    diff = (res["wx.T_out"] - res["base.T_out"]).abs()
    # EnergyPlus starts the first simulated hour from that day's hour-24 record (warm-up artefact),
    # afterwards both interpolate the EPW records identically.
    assert diff.loc["2023-07-03 01:00":].max() < 1e-9


def test_actuator_override_and_release(override_run):
    _, res = override_run
    df = res.df
    before = df.loc[:"2023-07-03 10:00"]
    pd.testing.assert_series_equal(before["ovr.T_core"], before["base.T_core"], check_names=False)
    # values observed at t describe the step that ends at t -> the override shows from 10:15 to 12:00
    assert (df["ovr.sp"].loc["2023-07-03 10:15":"2023-07-03 12:00"] == 30.0).all()
    assert df["ovr.T_core"].loc["2023-07-03 12:00"] > df["base.T_core"].loc["2023-07-03 12:00"] + 2.0
    assert df["ovr.P_W"].loc["2023-07-03 11:00"] < df["base.P_W"].loc["2023-07-03 11:00"]
    # None releases the actuator: the schedule applies again (EnergyPlus applies a released schedule
    # actuator one zone time step later, hence 12:30 rather than 12:15)
    pd.testing.assert_series_equal(df["ovr.sp"].loc["2023-07-03 12:30":], df["base.sp"].loc["2023-07-03 12:30":],
                                   check_names=False)
    assert abs(df["ovr.T_core"].loc["2023-07-03 14:00"] - df["base.T_core"].loc["2023-07-03 14:00"]) < 0.2
    assert df["override.clg_sp"].loc["2023-07-03 12:00":].isna().all()


def test_process_and_thread_executors_give_identical_results(idf_path, epw_path, tmp_path):
    from pycosim.energyplus.component import _THREAD_RUNS

    if _THREAD_RUNS["count"]:
        pytest.skip("EnergyPlus already ran in-thread in this process (it can only do so once)")
    frames = {}
    for executor in ("process", "thread"):
        sim = ep_sim(tmp_path, start="2023-07-03 06:00", end="2023-07-03 12:00", name=executor)
        sim.add(Constant("sp", value=25.0))
        ep = sim.add(pc.EnergyPlusComponent("office", idf_path, epw_path, executor=executor,
                                            sensors={"T_core": T_CORE, "T_out": T_OUT},
                                            meters={"P": ("Electricity:Facility", "W")},
                                            actuators={"clg_sp": CLG_SP}))
        sim.connect("sp.value", "office.clg_sp")
        frames[executor] = sim.run().df
        assert ep.info["exit_code"] == 0 and ep.run_metadata()["executor"] == executor
    pd.testing.assert_frame_equal(frames["process"], frames["thread"])
    df = frames["process"]
    assert len(df) == 24 and df.index[0] == datetime(2023, 7, 3, 6)  # window starting after midnight
    assert df["office.T_core"].loc["2023-07-03 09:00":].between(24.9, 25.1).all()


def test_second_in_thread_run_falls_back_to_a_process(idf_path, epw_path, tmp_path, caplog):
    from pycosim.energyplus.component import _THREAD_RUNS

    if not _THREAD_RUNS["count"]:
        pytest.skip("needs a previous in-thread EnergyPlus run in this process")
    sim = ep_sim(tmp_path, end="2023-07-03 01:00")
    ep = sim.add(pc.EnergyPlusComponent("office", idf_path, epw_path, executor="thread", sensors={"T": T_OUT}))
    with caplog.at_level("WARNING", logger="pycosim"):
        res = sim.run()
    assert res.meta["component_metadata"]["office"]["executor"] == "process"
    assert any("cannot be started in-process again" in r.getMessage() for r in caplog.records)


def test_misspelled_handles_raise_with_suggestions(idf_path, epw_path, tmp_path):
    sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", output_dir=tmp_path, save=False,
                          log_level="CRITICAL", name="misspelled")
    ep = sim.add(pc.EnergyPlusComponent(
        "office", idf_path, epw_path,
        sensors={"T": ("Zone Mean Air Temperatur", "Core_ZN")},
        actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SHC")}))
    with pytest.raises(ComponentError) as info:
        sim.run()
    err = info.value.original
    assert isinstance(err, HandleNotFoundError) and info.value.phase in ("setup", "ready")
    assert [m[:2] for m in err.missing] == [("sensor", "T"), ("actuator", "clg_sp")]
    text = str(err)
    assert "did you mean" in text
    assert "Actuator,Schedule:Compact,Schedule Value,CLGSETP_SCH" in text
    assert "Zone Mean Air Temperature,CORE_ZN" in text
    assert "did you mean" in str(info.value)  # also visible through the wrapping ComponentError
    # the failed start-up released the worker: no endpoint and no open log handle (matters on Windows)
    assert ep._endpoint is None
    (tmp_path / "misspelled" / "office" / "worker.log").unlink()


def test_clock_taken_from_the_model(idf_path, epw_path, tmp_path):
    sim = pc.CoSimulation(output_dir=tmp_path, save=False, log_level="WARNING", name="modelclock")
    ep = sim.add(pc.EnergyPlusComponent("office", idf_path, epw_path, sensors={"T_out": T_OUT}))
    sim.add(EPWWeather("wx", epw_path, fields=["T_out"], solar=False))
    res = sim.run()
    # small_office.idf: RunPeriod Aug 1 - Aug 2, 1 time step per hour
    assert (sim.start, sim.end, sim.dt, sim.n_steps) == (datetime(2023, 8, 1), datetime(2023, 8, 3), 3600.0, 48)
    assert len(res.df) == 48 and ep.info["timesteps_per_hour"] == 1
    np.testing.assert_allclose(res["office.T_out"].iloc[1:], res["wx.T_out"].iloc[1:], atol=1e-9)
    four = pc.EnergyPlusComponent("x", idf_path, epw_path, timestep=4).suggest_clock()
    assert four == {"start": datetime(2023, 8, 1), "end": datetime(2023, 8, 3), "step": 900.0}


def test_synchronization_error_when_run_period_is_too_short(office_model, epw_path, tmp_path):
    model = office_model.copy()
    model.set_run_period("2023-07-03", "2023-07-03")  # one day ...
    sim = ep_sim(tmp_path, end="2023-07-05", step="1h", name="short", log_level="CRITICAL")  # ... two-day window
    ep = sim.add(pc.EnergyPlusComponent("office", model, epw_path, sync_run_period=False, sensors={"T": T_OUT}))
    with pytest.raises(ComponentError) as info:
        sim.run()
    err = info.value
    assert isinstance(err.original, SynchronizationError) and err.phase == "output"
    assert err.time == datetime(2023, 7, 4)
    assert "reached the end of its RunPeriod" in str(err.original)
    assert len(sim.partial_results.df) == 25 and sim.partial_results["office.T"].iloc[:24].notna().all()
    assert ep._endpoint is None  # aborted by the master
    assert (tmp_path / "short" / "error_report.txt").exists()


def test_synchronization_error_when_calendars_differ(office_model, epw_path, tmp_path):
    sim = ep_sim(tmp_path, end="2023-07-03 06:00", step="1h", name="mismatch", log_level="CRITICAL")
    sim.add(pc.EnergyPlusComponent("office", office_model, epw_path, sync_run_period=False, sensors={"T": T_OUT}))
    with pytest.raises(ComponentError) as info:
        sim.run()
    assert isinstance(info.value.original, SynchronizationError)
    assert "EnergyPlus is at 08-01 00:00 but the master clock is at 07-03 00:00" in str(info.value.original)


def test_invalid_master_step_for_energyplus(idf_path, epw_path, tmp_path):
    sim = ep_sim(tmp_path, step="7min", log_level="CRITICAL")
    sim.add(pc.EnergyPlusComponent("office", idf_path, epw_path))
    with pytest.raises(ComponentError, match="EnergyPlus needs one of"):
        sim.run()


# ======================================================================= handles
def test_list_available_handles(office_model, epw_path):
    from pycosim.energyplus.handles import list_available_handles

    df = list_available_handles(office_model, epw_path)
    assert {"actuator", "variable", "meter", "internal_variable"} <= set(df["kind"])
    act = df[(df["kind"] == "actuator") & (df["key"].str.upper() == "CLGSETP_SCH")]
    assert len(act) == 1
    assert ast.literal_eval(act["spec"].iloc[0]) == ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")
    iv = df[(df["kind"] == "internal_variable") & (df["name"] == "Zone Floor Area")]
    assert "CORE_ZN" in set(iv["key"].str.upper())
    assert "Electricity:Facility" in set(df.loc[df["kind"] == "meter", "name"])
    assert office_model.get_run_period()[0] == date(2023, 8, 1)  # the caller's model is not modified


def test_cli_handles_csv(idf_path, epw_path, tmp_path, capsys):
    out = tmp_path / "handles.csv"
    code = cli.main(["handles", str(idf_path), "-w", str(epw_path), "--kind", "actuator", "--grep", "clgsetp",
                     "--csv", str(out)])
    assert code == 0
    df = pd.read_csv(out)
    assert len(df) >= 1 and (df["kind"] == "actuator").all()
    assert df["key"].str.upper().str.contains("CLGSETP").all()
    assert f"wrote {len(df)} rows" in capsys.readouterr().out
