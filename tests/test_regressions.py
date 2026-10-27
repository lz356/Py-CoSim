"""Regression tests for issues found in code review (no EnergyPlus needed)."""
from datetime import datetime

import numpy as np
import pytest

import pycosim as pc
from pycosim.components import Battery, GridMeter, LLMSupervisor, OfflineStandInBackend, RCBuilding
from pycosim.components.llm import LLMBackend
from pycosim.gym import CoSimEnv, ExternalAgent


def _rl_sim(tmp_path, with_second_agent=False):
    sim = pc.CoSimulation("2023-07-03", "2023-07-03 06:00", "15min", save=False, output_dir=tmp_path,
                          log_level="WARNING")
    sim.add(RCBuilding("house", 100))
    sim.add(ExternalAgent("agent", ["T_zone"], {"clg_sp": (20.0, 28.0)}, step_size="1h"))
    sim.connect("house.T_zone", "agent.T_zone")
    sim.connect("agent.clg_sp", "house.clg_sp")
    if with_second_agent:
        sim.add(ExternalAgent("baseline", ["T_zone"], {"u": (0.0, 1.0)}, step_size="1h",
                              policy=lambda t, obs: [0.25]))
        sim.connect("house.T_zone", "baseline.T_zone")
    return sim


def test_nan_action_is_rejected(tmp_path):
    env = CoSimEnv(lambda: _rl_sim(tmp_path), "agent", reward=lambda o, s: 0.0)
    env.reset()
    with pytest.raises(Exception):
        env.step(np.array([np.nan], dtype=np.float32))


def test_action_goes_to_the_named_agent(tmp_path):
    env = CoSimEnv(lambda: _rl_sim(tmp_path, with_second_agent=True), "agent", reward=lambda o, s: 0.0)
    env.reset()
    done = False
    while not done:
        _, _, term, trunc, _ = env.step(np.array([27.0], dtype=np.float32))
        done = term or trunc
    df = env.results.df
    assert (df["agent.clg_sp"].iloc[4:] == 27.0).all()
    assert (df["baseline.u"].iloc[4:] == 0.25).all()       # the other agent used its own policy


def test_terminal_observation_reflects_last_action(tmp_path):
    seen = []
    env = CoSimEnv(lambda: _rl_sim(tmp_path), "agent", reward=lambda o, s: seen.append(o["T_zone"]) or 0.0)
    env.reset()
    done = False
    while not done:
        _, _, term, trunc, _ = env.step(np.array([20.0], dtype=np.float32))
        done = term or trunc
    assert seen[-1] != seen[-2] or len(seen) == 1


class _Echo(LLMBackend):
    def __init__(self, text):
        self.text = text

    def complete(self, system, prompt):
        return self.text


def _llm_sim(tmp_path, backend, obs_value):
    sim = pc.CoSimulation("2023-07-03", "2023-07-03 03:00", "1h", save=False, output_dir=tmp_path,
                          log_level="WARNING")
    sim.add(pc.component(lambda t: obs_value, name="src", outputs="x"))
    sup = sim.add(LLMSupervisor("llm", backend, "test", {"clg_sp": (21.0, 30.0, 24.0)}, observations={"x": "x"}))
    sim.connect("src.x", "llm.x")
    return sim, sup


def test_llm_numpy_observations_and_overflow(tmp_path):
    sim, sup = _llm_sim(tmp_path, _Echo('{"clg_sp": ' + "9" * 400 + "}"), np.float32(25.5))
    res = sim.run()  # neither the numpy observation nor the huge integer may crash the run
    assert res["llm.clg_sp"].between(21.0, 30.0).all()
    assert sup.stats["fallback"] == len(res.df)


def test_llm_max_change_must_be_positive():
    with pytest.raises(ValueError):
        LLMSupervisor("llm", OfflineStandInBackend(), "x", {"clg_sp": (21, 30, 24)}, max_change=-1)


def test_components_are_reset_between_runs(tmp_path):
    bat = Battery("bat", capacity_kwh=10, max_charge_kw=5, soc_init=0.5)
    meter = GridMeter("meter")
    house = RCBuilding("house", 100, T_init=24.0)

    def make():
        sim = pc.CoSimulation("2023-07-03", "2023-07-03 06:00", "15min", save=False, output_dir=tmp_path,
                              log_level="WARNING")
        sim.add(bat, meter, house, pc.component(lambda t: 2000.0, name="charge", outputs="p"))
        sim.connect("charge.p", "bat.P_set")
        sim.connect("house.P_elec", "meter.P_net")
        return sim

    first = make().run()
    second = make().run()
    assert first["bat.soc"].iloc[-1] == pytest.approx(second["bat.soc"].iloc[-1])
    assert first["meter.E_import"].iloc[-1] == pytest.approx(second["meter.E_import"].iloc[-1])
    assert first["house.T_zone"].iloc[-1] == pytest.approx(second["house.T_zone"].iloc[-1])


def test_llm_supervisor_reusable(tmp_path):
    sup = LLMSupervisor("llm", OfflineStandInBackend(), "office", {"clg_sp": (21, 30, 24)})

    def make():
        sim = pc.CoSimulation("2023-07-03", "2023-07-03 03:00", "1h", save=False, output_dir=tmp_path,
                              log_level="WARNING")
        sim.add(sup)
        return sim

    make().run()
    res = make().run()
    assert (res["llm.valid"] == 1).all()
