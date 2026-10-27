"""Reinforcement learning: expose any co-simulation as a Gymnasium environment.

Put an :class:`ExternalAgent` into the co-simulation where the RL policy
should act. Its *inputs* are the observations and its *outputs* the actions;
everything else (EnergyPlus buildings, PV, tariffs, ...) stays an ordinary
component. :class:`CoSimEnv` pauses the master right before the agent acts,
hands the observation to the learning algorithm and resumes with its action::

    def make_sim():
        sim = pc.CoSimulation("2023-07-03", "2023-07-10", "15min", save=False)
        ...
        sim.add(ExternalAgent("agent", observations=["T_zone", "price"],
                              actions={"clg_sp": (22.0, 28.0)}, step_size="1h"))
        ...
        return sim

    env = CoSimEnv(make_sim, agent="agent", reward=lambda obs, sim: -obs["P_elec"] * obs["price"])
    obs, info = env.reset()
    obs, reward, terminated, truncated, info = env.step(env.action_space.sample())

The same scenario runs without RL by giving the agent a ``policy`` callable.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from .core.component import Component
from .core.master import CoSimulation

try:  # gymnasium is optional
    import gymnasium as _gymnasium

    _EnvBase = _gymnasium.Env
except ImportError:  # pragma: no cover
    _gymnasium = None
    _EnvBase = object

__all__ = ["ExternalAgent", "CoSimEnv"]


class ExternalAgent(Component):
    """Placeholder for a decision maker that lives outside the co-simulation.

    Parameters
    ----------
    observations : list of str or dict
        Input ports (connect measurements to them).
    actions : dict
        ``{output_name: (low, high)}``.
    policy : callable, optional
        ``policy(t, observation_dict) -> action`` used when no RL environment
        drives the agent (e.g. to evaluate a trained policy).
    """

    _pc_external_agent = True

    def __init__(self, name: str, observations: Union[Sequence[str], Mapping[str, Any]],
                 actions: Mapping[str, Tuple[float, float]], step_size: Optional[str] = None,
                 policy: Optional[Callable[[Any, Dict[str, Any]], Any]] = None):
        super().__init__(name, step_size=step_size)
        obs = dict(observations) if isinstance(observations, Mapping) else {o: None for o in observations}
        for name_, default in obs.items():
            self.add_input(name_, default=default)
        self.bounds: Dict[str, Tuple[float, float]] = {}
        for act, (lo, hi) in actions.items():
            self.bounds[act] = (float(lo), float(hi))
            self.add_output(act, initial=(float(lo) + float(hi)) / 2.0)
        self.policy = policy
        self.last_action: Optional[Dict[str, float]] = None

    @property
    def observation_names(self):
        return list(self.input_ports)

    @property
    def action_names(self):
        return list(self.bounds)

    def to_dict(self, action: Any) -> Dict[str, float]:
        if isinstance(action, Mapping):
            values = {k: float(action[k]) for k in self.bounds}
        else:
            arr = np.asarray(action, dtype=float).reshape(-1)
            if arr.size != len(self.bounds):
                raise ValueError(f"expected {len(self.bounds)} action values, got {arr.size}")
            values = dict(zip(self.bounds, arr.tolist()))
        bad = [k for k, v in values.items() if not np.isfinite(v)]
        if bad:
            raise ValueError(f"{self.name}: non-finite action for {bad}: {values}")
        return {k: min(max(float(v), self.bounds[k][0]), self.bounds[k][1]) for k, v in values.items()}

    def apply_action(self, t, inputs, action) -> Dict[str, float]:
        self.last_action = self.to_dict(action)
        return self.last_action

    def step(self, t, inputs):
        if self.policy is None:
            return None  # hold the previous action
        return self.apply_action(t, inputs, self.policy(t, dict(inputs)))


class CoSimEnv(_EnvBase):
    """Gymnasium environment around a co-simulation factory.

    Parameters
    ----------
    make_sim : callable
        Returns a fresh, not yet initialized :class:`CoSimulation` (called at
        every ``reset``).
    agent : str
        Name of the :class:`ExternalAgent` component.
    reward : callable
        ``reward(obs_dict, sim) -> float``, evaluated at each decision point for
        the action taken at the previous one.
    observation_bounds : dict, optional
        ``{name: (low, high)}`` for the observation space (default unbounded).
    """

    metadata = {"render_modes": []}

    def __init__(self, make_sim: Callable[[], CoSimulation], agent: str,
                 reward: Callable[[Dict[str, Any], CoSimulation], float],
                 observation_bounds: Optional[Mapping[str, Tuple[float, float]]] = None):
        if _gymnasium is None:
            raise ImportError("CoSimEnv needs gymnasium: pip install gymnasium")
        from gymnasium import spaces

        self.make_sim = make_sim
        self.agent_name = agent
        self.reward_fn = reward
        probe = make_sim()
        agent_comp = probe[agent]
        if not isinstance(agent_comp, ExternalAgent):
            raise TypeError(f"component {agent!r} must be an ExternalAgent")
        self.obs_names = agent_comp.observation_names
        self.act_names = agent_comp.action_names
        lo = np.array([agent_comp.bounds[a][0] for a in self.act_names], dtype=np.float32)
        hi = np.array([agent_comp.bounds[a][1] for a in self.act_names], dtype=np.float32)
        self.action_space = spaces.Box(lo, hi, dtype=np.float32)
        ob = observation_bounds or {}
        olo = np.array([ob.get(n, (-np.inf, np.inf))[0] for n in self.obs_names], dtype=np.float32)
        ohi = np.array([ob.get(n, (-np.inf, np.inf))[1] for n in self.obs_names], dtype=np.float32)
        self.observation_space = spaces.Box(olo, ohi, dtype=np.float32)
        self.sim: Optional[CoSimulation] = None
        self._gen = None
        self.results = None

    def _vector(self, obs: Mapping[str, Any]) -> np.ndarray:
        return np.array([np.nan if obs.get(n) is None else float(obs[n]) for n in self.obs_names], dtype=np.float32)

    def _advance(self, action: Any = None) -> Optional[Dict[str, Any]]:
        """Run the master until the agent must act again; return its observation (None at the end)."""
        sim = self.sim
        try:
            while True:
                if self._gen is not None:
                    try:
                        req = self._gen.send(action)
                        action = None
                        while req[1].name != self.agent_name:  # another external agent: let its policy act
                            req = self._gen.send(None)
                        return dict(req[2])
                    except StopIteration:
                        self._gen = None
                        action = None
                if sim.done:
                    return None
                self._gen = sim._step_gen()
                try:
                    req = next(self._gen)
                    while req[1].name != self.agent_name:
                        req = self._gen.send(None)
                    return dict(req[2])
                except StopIteration:
                    self._gen = None
        except BaseException as exc:
            sim._fail(exc)
            raise

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        if _EnvBase is not object:
            super().reset(seed=seed)
        self.close()
        self.sim = self.make_sim()
        if seed is not None:
            self.sim.seed = seed
        elif _EnvBase is not object:  # new random draws in every episode, reproducible from the env seed
            self.sim.seed = int(self.np_random.integers(2**31 - 1))
        self.sim.initialize()
        self._gen = None
        obs = self._advance()
        if obs is None:
            raise RuntimeError("the agent never acts in this co-simulation (check its step_size)")
        self._last_obs = obs
        return self._vector(obs), {"time": self.sim.now}

    def step(self, action):
        if self.sim is None:
            raise RuntimeError("call reset() first")
        obs = self._advance(action)
        truncated = obs is None  # the co-simulation window ended (a time limit, not a terminal state)
        if truncated:
            obs = dict(self.sim._gather(self.sim[self.agent_name]))  # state after the last action
        self._last_obs = obs
        reward = float(self.reward_fn(obs, self.sim))
        info = {"time": self.sim.now}
        if truncated:
            self.results = self.sim.finalize()
        return self._vector(obs), reward, False, truncated, info

    def close(self) -> None:
        if self.sim is not None and self.sim.status == "running":
            for comp in self.sim.components:
                try:
                    comp.abort()
                except Exception:
                    pass
            self.sim.status = "failed"
        self.sim = None
        self._gen = None
