"""Tests of the Gymnasium wrapper, YAML scenarios and the command line interface."""
from __future__ import annotations

import shutil
import textwrap
import uuid
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

import pycosim as pc
from pycosim import cli
from pycosim.components import Constant, RCBuilding
from pycosim.config import build_simulation, load_scenario
from pycosim.core.errors import ConfigurationError

gymnasium = pytest.importorskip("gymnasium")
from pycosim.gym import CoSimEnv, ExternalAgent  # noqa: E402


# ========================================================================= gym
def make_rl_sim(tmp_path, policy=None, step_size="1h", end="2023-07-04"):
    def factory():
        sim = pc.CoSimulation("2023-07-03", end, "15min", output_dir=tmp_path, save=False, log_level="WARNING")
        sim.add(Constant("wx", T_out=35.0))
        sim.add(RCBuilding("house", floor_area=100.0))
        sim.add(ExternalAgent("agent", observations=["T_zone", "T_out"], actions={"clg_sp": (20.0, 28.0)},
                              step_size=step_size, policy=policy))
        sim.connect("wx.T_out", "house.T_out")
        sim.connect("wx.T_out", "agent.T_out")
        sim.connect("house.T_zone", "agent.T_zone")
        sim.connect("agent.clg_sp", "house.clg_sp")
        return sim

    return factory


def reward(obs, sim):
    return -sim.bus.latest("house.P_elec") / 1000.0


@pytest.fixture
def env(tmp_path):
    e = CoSimEnv(make_rl_sim(tmp_path), agent="agent", reward=reward,
                 observation_bounds={"T_zone": (-50.0, 80.0), "T_out": (-50.0, 80.0)})
    yield e
    e.close()


def test_env_spaces(env):
    assert env.action_space.shape == (1,) and env.action_space.dtype == np.float32
    np.testing.assert_array_equal(env.action_space.low, [20.0])
    np.testing.assert_array_equal(env.action_space.high, [28.0])
    assert env.observation_space.shape == (2,)
    assert env.obs_names == ["T_zone", "T_out"] and env.act_names == ["clg_sp"]


def test_env_full_episode(env):
    obs, info = env.reset(seed=3)
    assert obs.dtype == np.float32 and obs.tolist() == [24.0, 35.0]
    assert info["time"] == datetime(2023, 7, 3)
    assert env.sim.seed == 3
    steps, rewards, times, terminated = 0, [], [], False
    while not terminated:
        obs, r, terminated, truncated, info = env.step(np.array([22.0], dtype=np.float32))
        assert not truncated and env.observation_space.contains(obs)
        rewards.append(r)
        times.append(info["time"])
        steps += 1
    assert steps == 24  # one decision per hour
    assert times[0] == datetime(2023, 7, 3, 1)
    assert all(r < 0 for r in rewards)
    res = env.results
    assert res is not None and len(res.df) == 96
    assert (res["agent.clg_sp"] == 22.0).all()
    assert res["house.T_zone"].iloc[-1] == pytest.approx(22.0, abs=1e-6)


def test_env_actions_are_clipped_and_dicts_accepted(env):
    env.reset()
    env.step(np.array([99.0], dtype=np.float32))
    assert env.sim["agent"].last_action == {"clg_sp": 28.0}
    env.step({"clg_sp": 10.0})
    assert env.sim["agent"].last_action == {"clg_sp": 20.0}
    with pytest.raises(ValueError, match="expected 1 action values"):
        env.sim["agent"].to_dict([1.0, 2.0])


def test_env_reset_is_reproducible_and_restarts(env):
    env.reset(seed=0)
    for _ in range(3):
        env.step(np.array([21.0], dtype=np.float32))
    first_sim = env.sim
    obs, info = env.reset(seed=0)
    assert env.sim is not first_sim and first_sim.status == "failed"  # old run aborted by close()
    assert obs.tolist() == [24.0, 35.0] and info["time"] == datetime(2023, 7, 3)


def test_env_step_before_reset(env):
    with pytest.raises(RuntimeError, match="reset"):
        env.step(np.array([22.0], dtype=np.float32))


def test_env_passes_gymnasium_checker(env):
    from gymnasium.utils.env_checker import check_env

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # recommendation to normalise the action space
        check_env(env, skip_render_check=True)


def test_env_requires_external_agent(tmp_path):
    with pytest.raises(TypeError, match="ExternalAgent"):
        CoSimEnv(make_rl_sim(tmp_path), agent="house", reward=reward)


def test_env_reset_propagates_configuration_errors(tmp_path):
    env = CoSimEnv(make_rl_sim(tmp_path, step_size="10min"), agent="agent", reward=reward)
    with pytest.raises(ConfigurationError, match="not a positive integer multiple"):
        env.reset()


def test_env_agent_slower_than_window_acts_once(tmp_path):
    env = CoSimEnv(make_rl_sim(tmp_path, step_size="1d", end="2023-07-03 06:00"), agent="agent", reward=reward)
    env.reset()
    *_, terminated, _, _ = env.step(np.array([23.0], dtype=np.float32))
    assert terminated and len(env.results.df) == 24


def test_external_agent_with_policy_runs_without_rl(tmp_path):
    seen = []

    def policy(t, obs):
        seen.append(obs)
        return [26.0 if t.hour < 12 else 30.0]  # 30 is clipped to 28

    sim = make_rl_sim(tmp_path, policy=policy)()
    res = sim.run()
    assert len(seen) == 24 and set(seen[0]) == {"T_zone", "T_out"}
    assert res["agent.clg_sp"].loc["2023-07-03 06:00"] == 26.0
    assert res["agent.clg_sp"].loc["2023-07-03 18:00"] == 28.0


def test_external_agent_without_policy_holds_initial_action(tmp_path):
    sim = make_rl_sim(tmp_path)()
    res = sim.run()
    assert (res["agent.clg_sp"] == 24.0).all()  # midpoint of the action bounds


# ===================================================================== config
@pytest.fixture
def scenario_dir(tmp_path, epw_path, restore_sys_path):
    """A scenario folder with a local module, a copy of the weather file and a YAML file."""
    module = f"scen_mod_{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{module}.py").write_text(textwrap.dedent('''
        import pycosim as pc

        def setpoint_rule(t, T_zone=24.0, offset=0.0):
            return {"clg_sp": (23.0 if 6 <= t.hour < 18 else 27.0) + offset}

        class Scaler(pc.Component):
            inputs = {"x": 0.0}
            outputs = {"y": 0.0}

            def __init__(self, name, factor=1.0):
                super().__init__(name)
                self.factor = factor

            def step(self, t, inputs):
                return self.factor * inputs.x

        def to_kw(watts):
            return watts / 1000.0

        def biggest(values):
            return max(values)
    '''), encoding="utf-8")
    shutil.copy(epw_path, tmp_path / "weather.epw")
    yaml_text = textwrap.dedent(f'''
        simulation:
          name: yaml-demo
          start: 2023-07-03
          end: 2023-07-04
          step: 15min
          output_dir: results
          seed: 5
          log_level: WARNING
        components:
          weather:
            type: EPWWeather
            path: weather.epw
            fields: [T_out, GHI]
          house:
            type: RCBuilding
            floor_area: 150
          rule:
            function: {module}:setpoint_rule
            outputs: [clg_sp]
            step_size: 1h
          scale:
            type: {module}:Scaler
            factor: 2.0
          tariff:
            type: TimeOfUseTariff
            peak_hours: [16, 21]
          meter:
            type: GridMeter
        connections:
          - weather.T_out -> house.T_out
          - weather.GHI -> house.GHI
          - rule.clg_sp -> house.clg_sp
          - {{from: house.T_zone, to: rule.T_zone, delay: true}}
          - {{from: house.P_elec, to: scale.x, transform: "{module}:to_kw"}}
          - {{from: [house.P_elec, house.P_hvac], to: meter.P_net, reduce: max}}
          - {{from: [house.Q_cool, house.Q_heat], to: meter.export_price, reduce: "{module}:biggest"}}
          - tariff.price -> meter.price
    ''')
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return path, module


def test_load_scenario_builds_and_runs(scenario_dir, tmp_path):
    path, module = scenario_dir
    sim = load_scenario(path)
    assert sim.name == "yaml-demo" and sim.seed == 5
    assert sim.output_dir.resolve() == (tmp_path / "results").resolve()
    assert [c.name for c in sim.components] == ["weather", "house", "rule", "scale", "tariff", "meter"]
    assert type(sim["scale"]).__name__ == "Scaler" and sim["scale"].factor == 2.0
    assert sim["rule"].step_size == "1h" and list(sim["rule"].output_ports) == ["clg_sp"]
    assert str(sim["weather"].path) == str((tmp_path / "weather.epw").resolve())
    delayed = [c for c in sim.connections if c.delay]
    assert len(delayed) == 1 and str(delayed[0]) == "house.T_zone -> rule.T_zone (delayed)"
    res = sim.run()
    assert len(res.df) == 96
    assert res["house.T_zone"].loc["2023-07-03 12:00"] == pytest.approx(23.0, abs=1e-6)
    np.testing.assert_allclose(res["scale.y"], res["house.P_elec"] * 2.0 / 1000.0)  # imported transform
    np.testing.assert_allclose(res["meter.P_import"], res[["house.P_elec", "house.P_hvac"]].max(axis=1))
    assert (tmp_path / "results" / "yaml-demo" / "timeseries.csv").exists()


def test_load_scenario_overrides(scenario_dir, tmp_path):
    path, _ = scenario_dir
    sim = load_scenario(path, name="other", save=False, end="2023-07-03 06:00")
    assert sim.name == "other"
    res = sim.run()
    assert len(res.df) == 24
    assert not (tmp_path / "results" / "other" / "timeseries.csv").exists()


def test_build_simulation_from_dict(tmp_path, restore_sys_path):
    spec = {
        "simulation": {"start": "2023-07-03", "end": "2023-07-03 01:00", "step": "15min", "save": False,
                       "output_dir": str(tmp_path), "log_level": "WARNING"},
        "components": {"c": {"type": "Constant", "value": 2.0},
                       "pi": {"type": "pycosim.components:PIController", "kp": 1.0, "setpoint": 5.0,
                              "u_min": -10, "u_max": 10}},
        "connections": ["c.value -> pi.measurement"],
    }
    res = build_simulation(spec, base_dir=tmp_path).run()
    assert list(res["pi.u"]) == [3.0] * 4


@pytest.mark.parametrize("spec, message", [
    ({"components": {"x": {"type": "NoSuchThing"}}}, "Unknown component type 'NoSuchThing'"),
    ({"components": {"x": {"floor_area": 3}}}, "needs a 'type'"),
    ({"components": {"x": {"function": "math:sqrt", "colour": "red"}}}, "unknown settings"),
    ({"components": ["not", "a", "mapping"]}, "must be a mapping"),
    ({"components": {"c": {"type": "Constant", "v": 1.0}}, "connections": ["c.v to c.w"]}, "must look like"),
    ({"components": {"c": {"type": "Constant", "v": 1.0}}, "connections": [42]}, "Cannot read connection"),
])
def test_build_simulation_errors(tmp_path, restore_sys_path, spec, message):
    spec = {"simulation": {"output_dir": str(tmp_path), "save": False, "log_level": "WARNING"}, **spec}
    with pytest.raises(ConfigurationError, match=message):
        build_simulation(spec, base_dir=tmp_path)


def test_type_resolution(restore_sys_path, tmp_path):
    from pycosim.config import _import, _resolve_type
    from pycosim.energyplus.component import EnergyPlusComponent

    assert _resolve_type("RCBuilding") is RCBuilding
    assert _resolve_type("EnergyPlus") is EnergyPlusComponent
    assert _resolve_type("pycosim.components.RCBuilding") is RCBuilding
    assert _resolve_type("pycosim.components:RCBuilding") is RCBuilding
    assert _import("os.path:join") is __import__("os").path.join
    with pytest.raises(ConfigurationError, match="module:Name"):
        _import("nodots")


def test_build_simulation_adds_base_dir_to_sys_path(tmp_path, restore_sys_path):
    import sys

    build_simulation({"simulation": {"output_dir": str(tmp_path)}}, base_dir=tmp_path)
    assert str(tmp_path.resolve()) in sys.path


# ========================================================================= CLI
def test_cli_version(capsys):
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == pc.__version__


def test_cli_without_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "usage: pycosim" in capsys.readouterr().out


def test_cli_rejects_unknown_command(capsys):
    with pytest.raises(SystemExit):
        cli.main(["frobnicate"])


def test_cli_run(scenario_dir, tmp_path, capsys):
    path, _ = scenario_dir
    assert cli.main(["run", str(path), "--name", "cli-run", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "Py-CoSim run 'cli-run'" in out and "status : OK" in out
    folder = (tmp_path / "results" / "cli-run").resolve()
    assert "results written to" in out and out.strip().endswith(str(folder))
    df = pd.read_csv(folder / "timeseries.csv", index_col=0, parse_dates=True)
    assert len(df) == 96 and "house.T_zone" in df.columns


def test_cli_run_with_plot(scenario_dir, monkeypatch, capsys):
    import matplotlib.pyplot as plt

    shown = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: shown.append(True))
    path, _ = scenario_dir
    assert cli.main(["run", str(path), "--name", "plotted", "--quiet", "--plot"]) == 0
    assert shown == [True]
