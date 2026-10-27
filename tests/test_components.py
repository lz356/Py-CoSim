"""Tests of the ready-made components (pycosim.components)."""
from __future__ import annotations

import math
import types
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from pycosim.components import (
    Battery,
    Constant,
    DailyProfile,
    EPWWeather,
    GridMeter,
    HouseholdOccupancy,
    OccupancyMLP,
    PeakShavingController,
    PIController,
    PrecoolingController,
    PVSystem,
    RCBuilding,
    ThermostatSchedule,
    TimeOfUseTariff,
    TimeSeriesSource,
    load_household_profiles,
    read_epw,
    select_households,
    solar_position,
)
from pycosim.components.occupancy import PROFILE_COLUMNS
from pycosim.components.weather import hour_of_year
from pycosim.core.component import Inputs

TUCSON = (32.13, -110.95, -7.0)
MONDAY = datetime(2023, 7, 3)
SATURDAY = datetime(2023, 7, 8)


def stub_ctx(step_size=900.0):
    return types.SimpleNamespace(step_size=step_size)


# ======================================================================= weather
@pytest.fixture(scope="module")
def epw_records(epw_path):
    with open(epw_path, encoding="latin-1") as fh:
        lines = fh.read().splitlines()[8:]
    rows = [line.split(",") for line in lines if line.strip()]
    return {"T_out": np.array([float(r[6]) for r in rows]), "GHI": np.array([float(r[13]) for r in rows]),
            "RH": np.array([float(r[8]) for r in rows]), "keys": [(int(r[1]), int(r[2]), int(r[3])) for r in rows]}


@pytest.fixture(scope="module")
def weather(epw_path):
    return EPWWeather("wx", epw_path)


def test_read_epw(epw_path, epw_records):
    df, loc = read_epw(epw_path)
    assert len(df) == 8760
    assert loc.city.startswith("Tucson")
    assert (loc.latitude, loc.longitude, loc.timezone) == TUCSON
    assert loc.elevation == pytest.approx(777.0)
    np.testing.assert_array_equal(df["T_out"].to_numpy(), epw_records["T_out"])
    assert (df.month.iloc[0], df.day.iloc[0], df.hour.iloc[0]) == (1, 1, 1)


def test_hour_of_year():
    assert hour_of_year(datetime(2023, 1, 1)) == 0
    assert hour_of_year(datetime(2023, 1, 1, 1, 30)) == 1.5
    assert hour_of_year(datetime(2023, 3, 1)) == 59 * 24
    assert hour_of_year(datetime(2024, 2, 29, 6)) == hour_of_year(datetime(2023, 2, 28, 6))
    assert hour_of_year(datetime(2023, 12, 31, 23)) == 8759


def test_epw_record_h_is_the_hour_ending_at_h(weather, epw_records):
    t_out = epw_records["T_out"]
    assert weather.value_at("T_out", datetime(2023, 1, 1, 1)) == t_out[0]
    assert weather.value_at("T_out", datetime(2023, 1, 1, 2)) == t_out[1]
    # July 3, hour 15 record -> value at 15:00 on July 3
    idx = epw_records["keys"].index((7, 3, 15))
    assert weather.value_at("T_out", datetime(2023, 7, 3, 15)) == t_out[idx]


def test_epw_linear_interpolation(weather, epw_records):
    t_out = epw_records["T_out"]
    assert weather.value_at("T_out", datetime(2023, 1, 1, 1, 30)) == pytest.approx((t_out[0] + t_out[1]) / 2)
    assert weather.value_at("T_out", datetime(2023, 1, 1, 1, 15)) == pytest.approx(0.75 * t_out[0] + 0.25 * t_out[1])
    # before the first record of the year the data wraps around to Dec 31, 24:00
    assert weather.value_at("T_out", datetime(2023, 1, 1, 0, 0)) == t_out[-1]
    assert weather.value_at("T_out", datetime(2023, 1, 1, 0, 30)) == pytest.approx((t_out[-1] + t_out[0]) / 2)
    ghi = epw_records["GHI"]
    i = epw_records["keys"].index((7, 3, 12))
    assert weather.value_at("GHI", datetime(2023, 7, 3, 12, 45)) == pytest.approx(0.25 * ghi[i] + 0.75 * ghi[i + 1])


def test_epw_without_interpolation(epw_path, epw_records):
    wx = EPWWeather("wx", epw_path, fields=["T_out"], interpolate=False, solar=False)
    t_out = epw_records["T_out"]
    assert wx.value_at("T_out", datetime(2023, 1, 1, 0, 30)) == t_out[0]  # inside hour 1 -> record 1
    assert wx.value_at("T_out", datetime(2023, 1, 1, 1, 0)) == t_out[0]
    assert wx.value_at("T_out", datetime(2023, 1, 1, 1, 15)) == t_out[1]
    assert list(wx.output_ports) == ["T_out"]


def test_epw_leap_day_maps_to_feb_28(weather):
    assert weather.value_at("T_out", datetime(2024, 2, 29, 10)) == weather.value_at("T_out", datetime(2023, 2, 28, 10))


def test_epw_component_outputs_and_errors(weather, epw_path):
    assert list(weather.output_ports) == ["T_out", "RH", "GHI", "DNI", "DHI", "wind_speed", "solar_zenith",
                                         "solar_azimuth"]
    assert weather.output_ports["T_out"].unit == "C" and weather.output_ports["GHI"].unit == "W/m2"
    out = weather.step(datetime(2023, 7, 3, 12), Inputs())
    assert set(out) == set(weather.output_ports)
    assert out["solar_zenith"] < 30
    with pytest.raises(ValueError, match="Unknown EPW fields"):
        EPWWeather("wx", epw_path, fields=["T_out", "humidity"])
    extra = EPWWeather("wx2", epw_path, fields=["pressure", "T_dew"], solar=False)
    assert list(extra.output_ports) == ["pressure", "T_dew"]


def test_epw_in_simulation(make_sim, epw_path, epw_records):
    sim = make_sim(start="2023-07-03", end="2023-07-04", step="15min")
    sim.add(EPWWeather("wx", epw_path))
    res = sim.run()
    i = epw_records["keys"].index((7, 3, 1))
    assert res["wx.T_out"].loc["2023-07-03 01:00"] == epw_records["T_out"][i]
    assert res["wx.GHI"].loc["2023-07-03 00:00":"2023-07-03 04:00"].max() == 0.0
    assert res["wx.GHI"].max() > 800


# ================================================================ solar position
def test_solar_position_noon_in_july():
    lat, lon, tz = TUCSON
    zenith_by_minute = [solar_position(datetime(2023, 7, 15) + timedelta(minutes=m), lat, lon, tz)[0]
                        for m in range(0, 1440, 5)]
    z_min = min(zenith_by_minute)
    assert 9.0 <= z_min <= 12.0  # latitude - declination ~ 32.1 - 21.5
    # Tucson is 6 deg west of its MST meridian -> solar noon ~12:30 local standard time
    minute_of_min = 5 * int(np.argmin(zenith_by_minute))
    assert 12 * 60 + 15 <= minute_of_min <= 12 * 60 + 45
    z_noon, az_noon = solar_position(datetime(2023, 7, 15, 12), lat, lon, tz)
    assert 9.0 <= z_noon <= 13.5
    assert 90 < az_noon < 180  # still in the south-east before solar noon


def test_solar_position_morning_evening_night():
    lat, lon, tz = TUCSON
    z_m, az_m = solar_position(datetime(2023, 7, 15, 8), lat, lon, tz)
    z_e, az_e = solar_position(datetime(2023, 7, 15, 17), lat, lon, tz)
    assert z_m < 90 and 45 < az_m < 135   # sun in the east in the morning
    assert z_e < 90 and 225 < az_e < 315  # and in the west in the afternoon
    for t in (datetime(2023, 7, 15, 0), datetime(2023, 7, 15, 23), datetime(2023, 1, 15, 3)):
        assert solar_position(t, lat, lon, tz)[0] > 90  # sun down at night


def test_solar_position_winter_solstice():
    z, az = solar_position(datetime(2023, 12, 21, 12, 30), *TUCSON)
    assert z == pytest.approx(32.13 + 23.44, abs=1.0)
    assert az == pytest.approx(180, abs=5)


# ======================================================================= sources
def test_constant(make_sim):
    c = Constant("lim", P_max=50e3, mode="eco")
    assert c.output_ports["P_max"].default == 50e3
    assert c.step(MONDAY, Inputs()) == {"P_max": 50e3, "mode": "eco"}
    with pytest.raises(ValueError):
        Constant("empty")


def test_daily_profile_list_and_weekend():
    prof = DailyProfile("p", list(range(24)), weekend=[100.0] * 24, output="sp", unit="C")
    assert prof.output_ports["sp"].unit == "C"
    assert prof.step(MONDAY.replace(hour=7, minute=45), Inputs()) == {"sp": 7.0}
    assert prof.step(SATURDAY.replace(hour=7), Inputs()) == {"sp": 100.0}
    with pytest.raises(ValueError, match="24 hourly"):
        DailyProfile("bad", [1, 2, 3])


def test_daily_profile_change_points():
    prof = DailyProfile("p", {7: 24.0, 19: 29.0})
    values = [prof.step(MONDAY.replace(hour=h), Inputs())["value"] for h in range(24)]
    assert values[:7] == [29.0] * 7  # before the first change point: wraps from the last one
    assert values[7:19] == [24.0] * 12 and values[19:] == [29.0] * 5
    assert DailyProfile("q", {0: 1.0}).step(MONDAY, Inputs())["value"] == 1.0


def ns_index(values):
    """Nanosecond-resolution DatetimeIndex (see the xfail test on other resolutions below)."""
    return pd.DatetimeIndex(pd.to_datetime(values)).as_unit("ns")


@pytest.fixture
def series():
    idx = pd.date_range("2023-07-03", periods=4, freq="1h", unit="ns")
    return pd.DataFrame({"a": [0.0, 10.0, 20.0, 30.0], "b": [1.0, 2.0, 3.0, 4.0]}, index=idx)


@pytest.mark.parametrize("method, minute, expected", [
    ("previous", 0, 0.0), ("previous", 59, 0.0), ("previous", 60, 10.0),
    ("linear", 30, 5.0), ("linear", 90, 15.0),
    ("nearest", 29, 0.0), ("nearest", 31, 10.0),
])
def test_time_series_source_methods(series, method, minute, expected):
    src = TimeSeriesSource("ts", series, method=method)
    assert src.step(MONDAY + timedelta(minutes=minute), Inputs())["a"] == pytest.approx(expected)


def test_time_series_source_outside_range_and_columns(series):
    src = TimeSeriesSource("ts", series, columns=["b"], units={"b": "kW"})
    assert list(src.output_ports) == ["b"] and src.output_ports["b"].unit == "kW"
    assert src.step(MONDAY - timedelta(hours=5), Inputs()) == {"b": 1.0}
    assert src.step(MONDAY + timedelta(days=5), Inputs()) == {"b": 4.0}
    lin = TimeSeriesSource("lin", series, method="linear")
    assert lin.step(MONDAY + timedelta(days=5), Inputs())["a"] == 30.0
    with pytest.raises(ValueError, match="method"):
        TimeSeriesSource("bad", series, method="cubic")


def test_time_series_source_from_series_and_repeat_yearly():
    s = pd.Series([1.0, 2.0], index=ns_index(["2019-07-03 00:00", "2019-07-03 01:00"]), name="load")
    src = TimeSeriesSource("ts", s, repeat_yearly=True)
    assert list(src.output_ports) == ["load"]
    assert src.step(datetime(2023, 7, 3, 1), Inputs()) == {"load": 2.0}
    assert src.step(datetime(2023, 7, 3, 0, 59), Inputs()) == {"load": 1.0}
    unnamed = TimeSeriesSource("u", pd.Series([5.0], index=ns_index(["2023-01-01"])))
    assert list(unnamed.output_ports) == ["value"]
    leap = TimeSeriesSource("leap", pd.Series([7.0, 8.0], index=ns_index(["2023-02-28", "2023-03-01"])),
                            repeat_yearly=True)
    assert leap.step(datetime(2024, 2, 29, 12), Inputs()) == {"value": 7.0}


def test_time_series_source_unsorted_and_string_index():
    df = pd.DataFrame({"v": [3.0, 1.0, 2.0]}, index=["2023-07-03 02:00", "2023-07-03 00:00", "2023-07-03 01:00"])
    df.index = pd.to_datetime(df.index).as_unit("ns")
    src = TimeSeriesSource("ts", df.iloc[::-1].copy())
    assert [src.step(MONDAY + timedelta(hours=h), Inputs())["v"] for h in range(3)] == [1.0, 2.0, 3.0]


@pytest.mark.parametrize("source", ["us_index", "csv_file"])
def test_time_series_source_non_nanosecond_index(tmp_path, source):
    idx = pd.date_range("2023-07-03", periods=4, freq="1h", unit="us")
    data = pd.DataFrame({"a": [0.0, 10.0, 20.0, 30.0]}, index=idx)
    if source == "csv_file":
        data.to_csv(tmp_path / "data.csv")
        data = tmp_path / "data.csv"
    src = TimeSeriesSource("ts", data, method="linear")
    assert src.step(MONDAY + timedelta(minutes=30), Inputs()) == {"a": 5.0}


# ===================================================================== RC model
def rc_sim(make_sim, T_out, clg=None, htg=None, days=1, step="15min", **rc_kw):
    sim = make_sim(start="2023-07-03", end=str(MONDAY + timedelta(days=days)), step=step)
    sim.add(Constant("env", T_out=T_out, GHI=0.0))
    house = sim.add(RCBuilding("house", floor_area=100.0, **rc_kw))
    sim.connect("env.T_out", "house.T_out")
    sim.connect("env.GHI", "house.GHI")
    if clg is not None or htg is not None:
        sim.add(Constant("sp", clg_sp=clg, htg_sp=htg))
        sim.connect("sp.clg_sp", "house.clg_sp")
        sim.connect("sp.htg_sp", "house.htg_sp")
    return sim, house


def test_rc_building_interface():
    house = RCBuilding("h")
    assert house.direct_feedthrough is False
    assert list(house.output_ports) == ["T_zone", "T_mass", "Q_cool", "Q_heat", "P_hvac", "P_elec"]
    assert house.output_ports["T_zone"].default == 24.0 and house.output_ports["P_elec"].default == 800.0
    assert set(house.input_ports) == {"T_out", "GHI", "Q_int", "clg_sp", "htg_sp"}


def test_rc_building_cooling_holds_setpoint(make_sim):
    sim, house = rc_sim(make_sim, T_out=40.0, clg=25.0, htg=18.0, T_init=26.0)
    res = sim.run()
    T = res["house.T_zone"].to_numpy()
    assert T[0] == 26.0  # initial state is reported at the first step
    np.testing.assert_allclose(T[1:], 25.0, atol=1e-6)  # exactly at the set-point after each step
    q_cool = res["house.Q_cool"].to_numpy()
    assert q_cool[0] == 0.0 and (q_cool[2:] > 0).all() and (res["house.Q_heat"] == 0).all()
    assert (q_cool <= house.cool_cap + 1e-9).all()
    np.testing.assert_allclose(res["house.P_hvac"], res["house.Q_cool"] / 3.0)
    np.testing.assert_allclose(res["house.P_elec"], res["house.P_hvac"] + 800.0)


def test_rc_building_heating_holds_setpoint(make_sim):
    sim, _ = rc_sim(make_sim, T_out=-10.0, clg=26.0, htg=21.0, cop_heat=2.0, T_init=20.0)
    res = sim.run()
    np.testing.assert_allclose(res["house.T_zone"].iloc[1:], 21.0, atol=1e-6)
    assert (res["house.Q_heat"].iloc[1:] > 0).all() and (res["house.Q_cool"] == 0).all()
    np.testing.assert_allclose(res["house.P_hvac"], res["house.Q_heat"] / 2.0)


def test_rc_building_default_setpoints_when_unconnected(make_sim):
    sim, _ = rc_sim(make_sim, T_out=40.0)
    res = sim.run()
    np.testing.assert_allclose(res["house.T_zone"].iloc[1:], 24.0, atol=1e-6)
    sim2, _ = rc_sim(make_sim, T_out=-20.0, T_init=19.0)
    np.testing.assert_allclose(sim2.run()["house.T_zone"].iloc[1:], 20.0, atol=1e-6)


def test_rc_building_capacity_limit(make_sim):
    sim, house = rc_sim(make_sim, T_out=45.0, clg=22.0, htg=18.0, cool_capacity=500.0)
    res = sim.run()
    assert res["house.T_zone"].iloc[-1] > 22.5  # cannot hold the set-point
    np.testing.assert_allclose(res["house.Q_cool"].iloc[1:], 500.0)


def test_rc_building_free_floats_with_wide_band(make_sim):
    sim, _ = rc_sim(make_sim, T_out=35.0, clg=100.0, htg=-100.0, days=10, step="1h", T_init=20.0)
    res = sim.run()
    T = res["house.T_zone"].to_numpy()
    assert (res["house.Q_cool"] == 0).all() and (res["house.Q_heat"] == 0).all()
    assert (np.diff(T) > 0).all()  # monotonically warming towards the outdoor temperature
    assert T[-1] < 35.0 and T[-1] > 30.0
    Tm = res["house.T_mass"].to_numpy()
    assert (Tm <= T + 1e-9).all()  # the heavy mass node lags behind the air node


def test_rc_building_equilibrium_without_driving_forces(make_sim):
    sim, _ = rc_sim(make_sim, T_out=24.0, clg=100.0, htg=-100.0, T_init=24.0)
    res = sim.run()
    np.testing.assert_allclose(res["house.T_zone"], 24.0, atol=1e-9)


def test_rc_building_gains_raise_temperature(make_sim):
    sim = make_sim(start="2023-07-03", end="2023-07-03 06:00")
    sim.add(Constant("env", T_out=20.0, GHI=800.0, Q_int=2000.0))
    sim.add(RCBuilding("house", T_init=20.0, default_setpoints=(100.0, -100.0)))
    sim.connect_matching("env", "house")
    res = sim.run()
    assert (np.diff(res["house.T_zone"].to_numpy()) > 0).all()


# ============================================================================ PV
def test_pv_poa_horizontal_equals_beam_plus_diffuse():
    pv = PVSystem("pv", capacity_kw=10, tilt=0.0)
    z = 30.0
    assert pv.poa(ghi=900, dni=800, dhi=100, zenith=z, sun_az=180) == pytest.approx(800 * math.cos(math.radians(z)) + 100)
    assert pv.poa(ghi=0, dni=0, dhi=0, zenith=120, sun_az=0) == 0.0
    # sun behind the array: no beam component
    tilted = PVSystem("pv2", capacity_kw=10, tilt=90.0, azimuth=180.0)
    assert tilted.poa(ghi=100, dni=800, dhi=0, zenith=60, sun_az=0) == pytest.approx(100 * 0.2 * 0.5)


def test_pv_limits_and_night():
    pv = PVSystem("pv", capacity_kw=10, tilt=20.0, dc_ac_ratio=1.2)
    pv.ctx = stub_ctx()
    night = pv.step(MONDAY, Inputs(GHI=0.0, DNI=0.0, DHI=0.0, T_out=25.0, solar_zenith=110.0, solar_azimuth=0.0))
    assert night == {"P_ac": 0.0, "POA": 0.0}
    noon = pv.step(MONDAY, Inputs(GHI=1000.0, DNI=900.0, DHI=100.0, T_out=25.0, solar_zenith=20.0,
                                  solar_azimuth=180.0))
    assert 0 < noon["P_ac"] <= 10_000 / 1.2
    huge = pv.step(MONDAY, Inputs(GHI=3000.0, DNI=3000.0, DHI=500.0, T_out=0.0, solar_zenith=20.0,
                                  solar_azimuth=180.0))
    assert huge["P_ac"] == pytest.approx(10_000 / 1.2)  # inverter clipping
    hot = pv.step(MONDAY, Inputs(GHI=1000.0, DNI=900.0, DHI=100.0, T_out=45.0, solar_zenith=20.0,
                                 solar_azimuth=180.0))
    assert hot["P_ac"] < noon["P_ac"]  # negative temperature coefficient


def test_pv_with_weather_over_a_day(make_sim, epw_path):
    sim = make_sim(start="2023-07-03", end="2023-07-04", step="15min")
    sim.add(EPWWeather("wx", epw_path), PVSystem("pv", capacity_kw=10.0))
    sim.connect_matching("wx", "pv")
    res = sim.run()
    p = res["pv.P_ac"]
    assert (p >= 0).all() and p.max() <= 10_000 / 1.2 + 1e-9
    assert p.loc["2023-07-03 00:00":"2023-07-03 04:00"].max() == 0.0
    assert p.loc["2023-07-03 21:00":].max() == 0.0
    assert p.loc["2023-07-03 11:00":"2023-07-03 14:00"].min() > 3000
    energy_kwh = p.sum() * 0.25 / 1000
    assert 30 < energy_kwh < 90  # 3-9 kWh/kWp on a summer day in Arizona


# ======================================================================= battery
def run_battery(batt, setpoints, dt=900.0):
    batt.ctx = stub_ctx(dt)
    return [batt.step(MONDAY, Inputs(P_set=p)) for p in setpoints]


def test_battery_power_limits():
    batt = Battery("b", capacity_kwh=100, max_charge_kw=5, max_discharge_kw=8)
    out = run_battery(batt, [20_000, -20_000, 3_000, None])
    assert out[0]["P"] == 5_000 and out[1]["P"] == -8_000 and out[2]["P"] == 3_000 and out[3]["P"] == 0.0
    assert out[0]["soc"] > 0.5


def test_battery_soc_limits():
    batt = Battery("b", capacity_kwh=10, max_charge_kw=100, soc_init=0.5, soc_min=0.2, soc_max=0.9)
    out = run_battery(batt, [100_000] * 4)
    assert out[0]["P"] == pytest.approx((0.9 - 0.5) * 10 * 3.6e6 / (0.95 * 900))  # fills up in one step
    assert out[-1]["soc"] == pytest.approx(0.9)
    assert out[-1]["P"] == pytest.approx(0.0, abs=1e-6)
    out = run_battery(batt, [-100_000] * 4)
    assert out[-1]["soc"] == pytest.approx(0.2)
    assert all(o["soc"] >= 0.2 - 1e-12 for o in out)
    assert out[-1]["P"] == pytest.approx(0.0, abs=1e-6)


def test_battery_energy_balance_with_losses():
    batt = Battery("b", capacity_kwh=50, max_charge_kw=10, eff_charge=0.9, eff_discharge=0.8, soc_init=0.5)
    rng = np.random.default_rng(1)
    setpoints = rng.uniform(-15_000, 15_000, size=200)
    out = run_battery(batt, setpoints, dt=600.0)
    p = np.array([o["P"] for o in out])
    stored_in = (p[p > 0] * 0.9).sum() * 600 / 3.6e6
    taken_out = (-p[p < 0] / 0.8).sum() * 600 / 3.6e6
    assert out[-1]["energy"] - 25.0 == pytest.approx(stored_in - taken_out, abs=1e-9)
    socs = np.array([o["soc"] for o in out])
    assert socs.min() >= 0.1 - 1e-12 and socs.max() <= 0.95 + 1e-12
    np.testing.assert_allclose([o["energy"] for o in out], socs * 50)
    # round trip: 10 kW in for one step comes back as 10 kW * 0.9 * 0.8 for one step
    rt = Battery("rt", capacity_kwh=100, max_charge_kw=10, eff_charge=0.9, eff_discharge=0.8, soc_init=0.5)
    assert run_battery(rt, [10_000.0])[0]["P"] == 10_000.0
    assert rt.soc == pytest.approx(0.5 + 10_000 * 0.9 * 900 / 3.6e8)
    back = run_battery(rt, [-7_200.0])[0]
    assert back["P"] == -7_200.0 and back["soc"] == pytest.approx(0.5)


def test_battery_validation_and_initial_outputs():
    with pytest.raises(ValueError):
        Battery("b", 10, 5, soc_min=0.9, soc_max=0.5)
    b = Battery("b", capacity_kwh=20, max_charge_kw=5, soc_init=0.3)
    assert b.output_ports["soc"].default == 0.3 and b.output_ports["energy"].default == pytest.approx(6.0)


def test_battery_in_simulation_respects_its_step_size(make_sim):
    sim = make_sim(start="2023-07-03", end="2023-07-03 02:00", step="15min")
    sim.add(Constant("cmd", P_set=4_000.0))
    batt = sim.add(Battery("batt", capacity_kwh=100, max_charge_kw=10, eff_charge=1.0, soc_init=0.5))
    batt.step_size = "1h"
    sim.connect("cmd.P_set", "batt.P_set")
    res = sim.run()
    # two hourly steps at 4 kW -> +8 kWh
    assert res["batt.energy"].iloc[-1] == pytest.approx(58.0)
    assert list(res["batt.energy"].round(6)) == [54.0] * 4 + [58.0] * 4


# ======================================================================= tariff
def test_tou_tariff_peak_offpeak_weekend():
    tou = TimeOfUseTariff("tou", peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.10)
    assert tou.step(MONDAY.replace(hour=16), Inputs()) == {"price": 0.30, "is_peak": 1}
    assert tou.step(MONDAY.replace(hour=20, minute=59), Inputs())["price"] == 0.30
    assert tou.step(MONDAY.replace(hour=21), Inputs()) == {"price": 0.10, "is_peak": 0}
    assert tou.step(MONDAY.replace(hour=15, minute=45), Inputs())["is_peak"] == 0
    assert tou.step(SATURDAY.replace(hour=17), Inputs()) == {"price": 0.10, "is_peak": 0}
    every_day = TimeOfUseTariff("tou2", weekdays_only=False)
    assert every_day.step(SATURDAY.replace(hour=17), Inputs())["is_peak"] == 1


def test_tou_tariff_shoulder_and_months():
    tou = TimeOfUseTariff("tou", peak_hours=(16, 21), shoulder_hours=(12, 16), shoulder_price=0.2,
                          months=range(5, 11))
    assert tou.price_at(MONDAY.replace(hour=13)) == 0.2
    assert tou.price_at(MONDAY.replace(hour=17)) == 0.30
    assert tou.price_at(SATURDAY.replace(hour=13)) == 0.10  # no shoulder on weekends
    january = datetime(2023, 1, 9, 17)  # a Monday outside the summer months
    assert not tou.is_peak(january) and tou.price_at(january) == 0.10


# =================================================================== grid meter
def test_grid_meter_accumulation():
    meter = GridMeter("m", demand_window="15min")
    meter.ctx = stub_ctx(900.0)
    seq = [(10_000, 0.2, 0.0), (-4_000, 0.2, 0.05), (2_000, 0.1, 0.0), (None, 0.1, 0.0)]
    outs = [meter.step(MONDAY, Inputs(P_net=p, price=c, export_price=e)) for p, c, e in seq]
    last = outs[-1]
    assert last["E_import"] == pytest.approx((10_000 + 2_000) * 0.25 / 1000)
    assert last["E_export"] == pytest.approx(1.0)
    assert last["cost"] == pytest.approx(2.5 * 0.2 + 0.5 * 0.1 - 1.0 * 0.05)
    assert last["peak"] == pytest.approx(10.0)
    assert outs[1]["P_import"] == 0.0 and outs[1]["P_export"] == 4_000
    assert [o["E_import"] for o in outs] == sorted(o["E_import"] for o in outs)


def test_grid_meter_peak_uses_demand_window_average():
    meter = GridMeter("m", demand_window="15min")
    meter.ctx = stub_ctx(300.0)  # 3 samples per demand window
    outs = [meter.step(MONDAY, Inputs(P_net=p, price=0.0, export_price=0.0)) for p in [9_000, 0, 0, 0, 3_000, 3_000]]
    assert outs[0]["peak"] == 0.0 and outs[1]["peak"] == 0.0  # window not yet full
    assert outs[2]["peak"] == pytest.approx(3.0)  # (9 + 0 + 0) / 3
    assert outs[-1]["peak"] == pytest.approx(3.0)


# ================================================================== controllers
def test_thermostat_schedule():
    ts = ThermostatSchedule("ts", occupied_hours=(6, 22), cooling=(24.0, 29.4), heating=(21.0, 15.6))
    assert ts.step(MONDAY.replace(hour=6), Inputs()) == {"clg_sp": 24.0, "htg_sp": 21.0}
    assert ts.step(MONDAY.replace(hour=22), Inputs()) == {"clg_sp": 29.4, "htg_sp": 15.6}
    assert ts.step(SATURDAY.replace(hour=12), Inputs()) == {"clg_sp": 29.4, "htg_sp": 15.6}
    assert ThermostatSchedule("w", weekend_occupied=True).occupied(SATURDAY.replace(hour=12))


def test_precooling_controller_modes():
    pc_ = PrecoolingController("pre", peak_hours=(16, 21), precool_hours=3.0, base_sp=24.0, precool_sp=22.0,
                               peak_sp=26.5)
    on = Inputs(enable=1)
    cases = {(MONDAY, 10, 0): ("base", 24.0), (MONDAY, 12, 59): ("base", 24.0), (MONDAY, 13, 0): ("precool", 22.0),
             (MONDAY, 15, 45): ("precool", 22.0), (MONDAY, 16, 0): ("peak", 26.5), (MONDAY, 20, 45): ("peak", 26.5),
             (MONDAY, 21, 0): ("base", 24.0), (SATURDAY, 14, 0): ("base", 24.0), (SATURDAY, 17, 0): ("base", 24.0)}
    for (day, h, m), (mode, sp) in cases.items():
        t = day.replace(hour=h, minute=m)
        assert pc_.mode(t) == mode, t
        assert pc_.step(t, on) == sp, t
    assert pc_.step(MONDAY.replace(hour=17), Inputs(enable=0)) == 24.0
    assert pc_.step(MONDAY.replace(hour=14), Inputs(enable=0)) == 24.0


def test_precooling_fractional_hours_and_occupancy():
    pc_ = PrecoolingController("pre", precool_hours=1.5, occupied_hours=(8, 18), unoccupied_sp=29.0)
    assert pc_.mode(MONDAY.replace(hour=14, minute=15)) == "base"
    assert pc_.mode(MONDAY.replace(hour=14, minute=30)) == "precool"
    assert pc_.mode(MONDAY.replace(hour=19)) == "unoccupied"  # inside the peak window but unoccupied
    assert pc_.step(MONDAY.replace(hour=19), Inputs(enable=0)) == 29.0
    assert pc_.step(MONDAY.replace(hour=6), Inputs(enable=1)) == 29.0
    no_unocc = PrecoolingController("pre2", occupied_hours=(8, 18))
    assert no_unocc.mode(MONDAY.replace(hour=19)) == "base"
    every_day = PrecoolingController("pre3", weekdays_only=False)
    assert every_day.mode(SATURDAY.replace(hour=17)) == "peak"


def test_pi_controller_integrates_and_tracks():
    pi = PIController("pi", kp=0.0, ki=0.001, setpoint=1.0, u_min=-10, u_max=10)
    pi.ctx = stub_ctx(60.0)
    us = [pi.step(MONDAY, Inputs(measurement=0.0, setpoint=None)) for _ in range(3)]
    assert us == pytest.approx([0.06, 0.12, 0.18])
    assert pi.step(MONDAY, Inputs(measurement=0.0, setpoint=2.0)) == pytest.approx(0.18 + 0.12)
    assert pi.step(MONDAY, Inputs(measurement=None, setpoint=None)) == -10
    rev = PIController("rev", kp=2.0, setpoint=24.0, u_min=0.0, u_max=100.0, reverse=True)
    rev.ctx = stub_ctx(60.0)
    assert rev.step(MONDAY, Inputs(measurement=26.0, setpoint=None)) == 4.0
    assert rev.step(MONDAY, Inputs(measurement=20.0, setpoint=None)) == 0.0


def test_pi_controller_anti_windup():
    pi = PIController("pi", kp=0.1, ki=0.01, setpoint=22.0, u_min=0.0, u_max=1.0, reverse=True)
    pi.ctx = stub_ctx(60.0)
    for _ in range(100):  # large, persistent error that would saturate the output
        u = pi.step(MONDAY, Inputs(measurement=30.0, setpoint=None))
    assert u == pytest.approx(1.0)
    # back-calculation: the integral is clamped where kp*err + ki*I equals the limit (no wind-up beyond it)
    assert pi.integral == pytest.approx((1.0 - 0.1 * 8.0) / 0.01)
    # as soon as the error changes sign the output leaves saturation within one step
    u_neg = pi.step(MONDAY, Inputs(measurement=21.0, setpoint=None))
    assert 0.0 <= u_neg < 1.0


@pytest.mark.parametrize("kp, ki", [(0.0, 1.0), (0.1, 0.01)])
def test_pi_controller_reaches_limit_under_persistent_error(kp, ki):
    pi = PIController("pi", kp=kp, ki=ki, setpoint=22.0, u_min=0.0, u_max=1.0, reverse=True)
    pi.ctx = stub_ctx(60.0)
    us = [pi.step(MONDAY, Inputs(measurement=30.0, setpoint=None)) for _ in range(20)]
    assert us[-1] == pytest.approx(1.0)


def test_peak_shaving_controller():
    ps = PeakShavingController("ps", import_limit_kw=50, charge_kw=10, soc_target=0.9, charge_hours=(0, 6))
    noon = MONDAY.replace(hour=12)
    assert ps.step(noon, Inputs(P_load=-5_000.0, soc=0.5, is_peak=0)) == 5_000.0   # store PV surplus
    assert ps.step(noon, Inputs(P_load=30_000.0, soc=0.5, is_peak=1)) == -30_000.0  # cover on-peak demand
    assert ps.step(noon, Inputs(P_load=70_000.0, soc=0.5, is_peak=0)) == -20_000.0  # shave above the limit
    assert ps.step(noon, Inputs(P_load=30_000.0, soc=0.5, is_peak=0)) == 0.0
    night = MONDAY.replace(hour=2)
    assert ps.step(night, Inputs(P_load=20_000.0, soc=0.5, is_peak=0)) == 10_000.0
    assert ps.step(night, Inputs(P_load=45_000.0, soc=0.5, is_peak=0)) == 5_000.0  # limited by import limit
    assert ps.step(night, Inputs(P_load=20_000.0, soc=0.95, is_peak=0)) == 0.0     # already full
    passive = PeakShavingController("p2", 50, 10, store_pv_surplus=False, discharge_on_peak=False)
    assert passive.step(noon, Inputs(P_load=-5_000.0, soc=0.5, is_peak=1)) == 0.0


def test_peak_shaving_with_battery_and_meter(make_sim):
    sim = make_sim(start="2023-07-03", end="2023-07-04", step="15min")
    load = [20_000.0] * 96
    load[64:84] = [80_000.0] * 20  # 16:00-21:00 spike
    idx = pd.date_range("2023-07-03", periods=96, freq="15min", unit="ns")
    sim.add(TimeSeriesSource("load", pd.Series(load, index=idx, name="P")))
    sim.add(Battery("batt", capacity_kwh=500, max_charge_kw=100, soc_init=0.9))
    sim.add(PeakShavingController("ctrl", import_limit_kw=50, charge_kw=50, discharge_on_peak=False))
    sim.add(GridMeter("meter"))
    sim.connect("load.P", "ctrl.P_load")
    sim.connect("batt.soc", "ctrl.soc", delay=True)
    sim.connect("ctrl.P_set", "batt.P_set")
    sim.connect(["load.P", "batt.P"], "meter.P_net", reduce="sum")
    res = sim.run()
    assert res["meter.P_import"].max() == pytest.approx(50_000.0)
    assert res["meter.peak"].iloc[-1] == pytest.approx(50.0)
    assert res["batt.soc"].iloc[-1] < 0.9


# ==================================================================== occupancy
def test_household_data_and_selection():
    df = load_household_profiles()
    assert len(df) == 13548
    assert set(PROFILE_COLUMNS) <= set(df.columns)
    prof = df[PROFILE_COLUMNS].to_numpy()
    assert prof.min() >= 0.0 and prof.max() <= 1.0
    df.loc[0, "hhsize"] = 999  # a copy: the cached table is untouched
    assert load_household_profiles().loc[0, "hhsize"] != 999

    ev = select_households(group="ev")
    assert (ev["group"] == "ev").all() and len(ev) == 5548
    long_ = select_households(commute="long")
    assert (long_["commute_min"] >= 30).all()
    short = select_households(commute="short", hhsize=[1, 2], urban=["U", "S"])
    assert (short["commute_min"] <= 10).all() and set(short["hhsize"]) <= {1, 2}
    assert set(short["urban"]) <= {"U", "S"}
    assert short.index[0] == 0 and short.index.is_monotonic_increasing
    with pytest.raises(ValueError, match="commute"):
        select_households(commute="medium")
    with pytest.raises(ValueError, match="No household"):
        select_households(hhsize=[42])
    sample = df.iloc[::100]
    custom = select_households(group="non_ev", data=sample)
    assert len(custom) == (sample["group"] == "non_ev").sum() > 0


def occupancy_run(make_sim, seed, name, n_units=12, days=2, **kw):
    sim = make_sim(start="2023-07-03", end=str(MONDAY + timedelta(days=days)), step="15min", seed=seed, name=name)
    occ = sim.add(HouseholdOccupancy("occ", n_units, **kw))
    return sim.run(), occ


def test_household_occupancy_reproducible_and_bounded(make_sim):
    a, occ = occupancy_run(make_sim, 3, "a")
    b, _ = occupancy_run(make_sim, 3, "b")
    c, _ = occupancy_run(make_sim, 4, "c")
    pd.testing.assert_frame_equal(a.df, b.df)
    assert not a.df.equals(c.df)
    units = [f"occ.occ_{i:02d}" for i in range(12)]
    sizes = occ.assignment["hhsize"].to_numpy(dtype=float)
    values = a.df[units].to_numpy()
    assert (values >= 0).all() and (values <= sizes[None, :] + 1e-9).all()
    np.testing.assert_allclose(a["occ.total"], values.sum(axis=1))
    np.testing.assert_allclose(a["occ.fraction"], values.sum(axis=1) / sizes.sum())
    assert a["occ.fraction"].between(0, 1).all()
    assert a.meta["component_metadata"]["occ"]["n_units"] == 12
    assert list(occ.assignment.columns) == ["houseid", "group", "hhsize", "commute_min", "urban", "income_cat"]


def test_household_occupancy_daily_resampling(make_sim):
    daily, _ = occupancy_run(make_sim, 1, "daily", n_units=20, days=2)
    fixed, _ = occupancy_run(make_sim, 1, "fixed", n_units=20, days=2, resample=None)
    day1, day2 = fixed.df.iloc[:96].to_numpy(), fixed.df.iloc[96:].to_numpy()
    np.testing.assert_array_equal(day1, day2)  # one day repeated
    d1, d2 = daily.df.iloc[:96].to_numpy(), daily.df.iloc[96:].to_numpy()
    assert not np.array_equal(d1, d2)
    np.testing.assert_array_equal(d1, day1)  # first day identical, new diaries from day 2


def test_household_occupancy_selection_and_validation(make_sim):
    res, occ = occupancy_run(make_sim, 0, "sel", n_units=5, days=1, commute="long", hhsize=[3], group="ev",
                             prefix="unit")
    assert (occ.assignment["hhsize"] == 3).all() and (occ.assignment["commute_min"] >= 30).all()
    assert "occ.unit_04" in res
    with pytest.raises(ValueError, match="n_units"):
        HouseholdOccupancy("o", 0)
    with pytest.raises(ValueError, match="resample"):
        HouseholdOccupancy("o", 2, resample="weekly")
    many = HouseholdOccupancy("o", 101)
    assert many.unit_ports[0] == "occ_000" and many.unit_ports[-1] == "occ_100"


@pytest.mark.filterwarnings("ignore::sklearn.exceptions.ConvergenceWarning")
def test_occupancy_mlp():
    with pytest.raises(RuntimeError, match="fit"):
        OccupancyMLP().predict(8, 2)
    data = select_households(commute="long").head(60)
    model = OccupancyMLP(hidden_layer_sizes=(8,), max_iter=40, random_state=0).fit(data)
    hours = np.arange(0, 24, 0.5)
    pred = model.predict(hours, 3)
    assert pred.shape == hours.shape and pred.min() >= 0 and pred.max() <= 1
    assert model.predict(3.0, 3)[0] > model.predict(12.0, 3)[0]  # commuters are home at night
    again = OccupancyMLP(hidden_layer_sizes=(8,), max_iter=40, random_state=0).fit(data)
    np.testing.assert_allclose(again.predict(hours, 3), pred)
