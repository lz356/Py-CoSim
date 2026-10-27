"""Example 07 - Reinforcement learning on a co-simulation (Gymnasium interface).

Any Py-CoSim scenario becomes a Gymnasium environment by inserting an
``ExternalAgent`` where the policy should act. Here the agent chooses the
hourly cooling set-point of a house to trade off electricity cost under a
time-of-use tariff against comfort.

To stay dependency-free and fast, the policy is linear and trained with the
cross-entropy method (a simple, robust black-box RL algorithm). The same
``CoSimEnv`` works with Stable-Baselines3, RLlib, etc.

    python run.py                    # RC house, trains in ~20-60 s
    python run.py --energyplus       # same task on the EnergyPlus small office (slower)
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402

import pycosim as pc  # noqa: E402
from pycosim.components import EPWWeather, RCBuilding, TimeOfUseTariff  # noqa: E402
from pycosim.gym import CoSimEnv, ExternalAgent  # noqa: E402
from _common import TUCSON_EPW, make_office  # noqa: E402

OBS = ["T_zone", "T_out", "price", "hour_sin", "hour_cos", "P_elec"]
SP_RANGE = (21.0, 28.0)


@pc.component(outputs=["hour_sin", "hour_cos"])
def clock(t):
    angle = 2 * np.pi * (t.hour + t.minute / 60) / 24
    return {"hour_sin": float(np.sin(angle)), "hour_cos": float(np.cos(angle))}


def make_sim(energyplus: bool = False, days: int = 3, start: str = "2023-07-17"):
    import pandas as pd

    t0 = pd.Timestamp(start)
    sim = pc.CoSimulation(t0, t0 + pd.Timedelta(days=days), "15min", save=False, log_level="WARNING")
    sim.add(TimeOfUseTariff("tariff", peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.08))
    sim.add(clock.copy("clock"))
    sim.add(ExternalAgent("agent", observations=OBS, actions={"clg_sp": SP_RANGE}, step_size="1h"))
    if energyplus:
        sim.add(make_office("bldg"))
        sim.connect("bldg.T_core_zn", "agent.T_zone")
        sim.connect("bldg.T_out", "agent.T_out")
        sim.connect("bldg.P_facility", "agent.P_elec", transform=lambda w: w / 1000)
    else:
        sim.add(EPWWeather("weather", TUCSON_EPW), RCBuilding("bldg", floor_area=150))
        sim.connect("weather.T_out", "bldg.T_out")
        sim.connect("weather.GHI", "bldg.GHI")
        sim.connect("bldg.T_zone", "agent.T_zone")
        sim.connect("weather.T_out", "agent.T_out")
        sim.connect("bldg.P_elec", "agent.P_elec", transform=lambda w: w / 1000)
    sim.connect("tariff.price", "agent.price")
    sim.connect("clock.hour_sin", "agent.hour_sin")
    sim.connect("clock.hour_cos", "agent.hour_cos")
    sim.connect("agent.clg_sp", "bldg.clg_sp")
    return sim


def reward(obs, sim) -> float:
    """Negative hourly cost minus a comfort penalty for daytime hours above 26 C."""
    hour = sim.now.hour
    cost = obs["P_elec"] * obs["price"]  # $ for one hour at this power
    occupied = 8 <= hour < 22
    discomfort = max(obs["T_zone"] - 26.0, 0.0) if occupied else 0.0
    return -cost - 0.5 * discomfort


def features(obs: np.ndarray) -> np.ndarray:
    T_zone, T_out, price, hs, hc, p = obs
    return np.array([1.0, (T_zone - 25) / 3, (T_out - 30) / 10, price * 5, hs, hc, p / 5])


def policy_action(theta: np.ndarray, obs: np.ndarray) -> np.ndarray:
    lo, hi = SP_RANGE
    z = float(np.tanh(features(obs) @ theta))
    return np.array([lo + (z + 1) / 2 * (hi - lo)], dtype=np.float32)


def episode_return(env: CoSimEnv, act) -> float:
    obs, _ = env.reset()
    total, done = 0.0, False
    while not done:
        obs, r, done, _, _ = env.step(act(obs))
        total += r
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--energyplus", action="store_true")
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--population", type=int, default=16)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    env = CoSimEnv(lambda: make_sim(args.energyplus, args.days), agent="agent", reward=reward)
    for sp in (24.0, 26.0):
        print(f"constant {sp:.0f} C policy: return {episode_return(env, lambda o, sp=sp: np.array([sp])):8.3f}")

    rng = np.random.default_rng(args.seed)
    dim = len(features(np.zeros(len(OBS))))
    mean, std = np.zeros(dim), np.ones(dim)
    n_elite = max(2, args.population // 4)
    for it in range(args.iterations):
        thetas = mean + std * rng.standard_normal((args.population, dim))
        scores = np.array([episode_return(env, lambda o, th=th: policy_action(th, o)) for th in thetas])
        elite = thetas[np.argsort(scores)[-n_elite:]]
        mean, std = elite.mean(axis=0), elite.std(axis=0) + 0.05
        print(f"iteration {it + 1}: best {scores.max():8.3f}  mean {scores.mean():8.3f}")
    print(f"trained policy: return {episode_return(env, lambda o: policy_action(mean, o)):8.3f}")
    np.save(HERE / "policy_theta.npy", mean)

    # The trained policy can also run *inside* an ordinary co-simulation:
    sim = make_sim(args.energyplus, args.days)
    sim["agent"].policy = lambda t, obs: policy_action(mean, np.array([obs[k] for k in OBS]))
    sim.save, sim.output_dir, sim.name = True, HERE / "output", "07_trained_policy"
    res = sim.run()
    print(res.df[["agent.clg_sp", "bldg.T_zone" if not args.energyplus else "bldg.T_core_zn"]]
          .resample("1h").mean().head(24).round(2).to_string())


if __name__ == "__main__":
    main()
