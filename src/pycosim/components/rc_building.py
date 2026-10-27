"""A light-weight resistance-capacitance (RC) building model.

Two thermal nodes (indoor air + furniture, and building mass) and an ideal
HVAC system. It mirrors the interface and timing semantics of
:class:`~pycosim.EnergyPlusComponent` (non-feedthrough; outputs at ``t`` are
the state at ``t`` and the average HVAC power over the previous step), so a
co-simulation can be prototyped in seconds with the RC model and then switched
to EnergyPlus by replacing a single component. It is also what the test-suite
and the "no EnergyPlus required" examples use.

Energy balances (W)::

    C_a dT_a/dt = H_ae (T_out - T_a) + H_am (T_m - T_a) + f_c (Q_int + Q_sol) + Q_hvac
    C_m dT_m/dt = H_me (T_out - T_m) + H_am (T_a - T_m) + (1 - f_c) (Q_int + Q_sol)

with ``Q_sol = A_sol * GHI``. The ideal HVAC system keeps the air temperature
at the end of each step between the heating and cooling set-points, within its
capacity, and its electricity use is ``Q_cool / COP_c + Q_heat / COP_h``.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

from ..core.component import Component

__all__ = ["RCBuilding"]


class RCBuilding(Component):
    """2R2C single-zone building with ideal heating/cooling.

    Parameters scale with ``floor_area`` (m2) unless given explicitly.

    Inputs: ``T_out`` [C], ``GHI`` [W/m2], ``Q_int`` [W] internal gains,
    ``clg_sp``/``htg_sp`` [C] set-points (``None`` -> defaults).
    Outputs: ``T_zone``, ``T_mass`` [C], ``Q_cool``, ``Q_heat`` [W thermal],
    ``P_hvac``, ``P_elec`` [W electric, incl. ``base_load``].
    """

    direct_feedthrough = False
    inputs = {"T_out": 25.0, "GHI": 0.0, "Q_int": 0.0, "clg_sp": None, "htg_sp": None}

    def __init__(self, name: str, floor_area: float = 100.0, *, H_ae: Optional[float] = None,
                 H_me: Optional[float] = None, H_am: Optional[float] = None, C_air: Optional[float] = None,
                 C_mass: Optional[float] = None, A_sol: Optional[float] = None, f_conv: float = 0.5,
                 cool_capacity: Optional[float] = None, heat_capacity: Optional[float] = None,
                 cop_cool: float = 3.0, cop_heat: float = 3.0, base_load: Optional[float] = None,
                 default_setpoints: Tuple[float, float] = (24.0, 20.0), T_init: float = 24.0,
                 substep: float = 60.0):
        super().__init__(name)
        a = float(floor_area)
        self.floor_area = a
        self.H_ae = H_ae if H_ae is not None else 0.6 * a       # windows + infiltration, W/K
        self.H_me = H_me if H_me is not None else 0.5 * a       # opaque envelope, W/K
        self.H_am = H_am if H_am is not None else 9.1 * 2.5 * a  # ISO 13790 h_ms * A_m
        self.C_air = C_air if C_air is not None else 1.0e4 * a  # J/K
        self.C_mass = C_mass if C_mass is not None else 1.65e5 * a  # J/K (medium mass)
        self.A_sol = A_sol if A_sol is not None else 0.04 * a   # effective solar aperture, m2
        self.f_conv = f_conv
        self.cool_cap = cool_capacity if cool_capacity is not None else 120.0 * a
        self.heat_cap = heat_capacity if heat_capacity is not None else 100.0 * a
        self.cop_cool, self.cop_heat = cop_cool, cop_heat
        self.base_load = base_load if base_load is not None else 8.0 * a
        self.default_clg, self.default_htg = default_setpoints
        self.substep = substep
        self.T_init = float(T_init)
        self.T_a = self.T_m = float(T_init)
        self._q_cool = self._q_heat = 0.0
        for port, unit, init in (("T_zone", "C", self.T_a), ("T_mass", "C", self.T_m), ("Q_cool", "W", 0.0),
                                 ("Q_heat", "W", 0.0), ("P_hvac", "W", 0.0), ("P_elec", "W", self.base_load)):
            self.add_output(port, initial=init, unit=unit)

    def setup(self, ctx) -> None:
        self.T_a = self.T_m = self.T_init
        self._q_cool = self._q_heat = 0.0

    # ----------------------------------------------------------- dynamics
    def _simulate(self, T_a: float, T_m: float, T_out: float, q_air: float, q_mass: float, q_hvac: float,
                  dt: float) -> Tuple[float, float]:
        """Backward-Euler integration of the 2-node model over ``dt`` seconds."""
        n = max(1, int(math.ceil(dt / self.substep)))
        h = dt / n
        ca, cm = self.C_air / h, self.C_mass / h
        a11 = ca + self.H_ae + self.H_am
        a22 = cm + self.H_me + self.H_am
        a12 = a21 = -self.H_am
        det = a11 * a22 - a12 * a21
        for _ in range(n):
            b1 = ca * T_a + self.H_ae * T_out + q_air + q_hvac
            b2 = cm * T_m + self.H_me * T_out + q_mass
            T_a = (b1 * a22 - a12 * b2) / det
            T_m = (a11 * b2 - a21 * b1) / det
        return T_a, T_m

    def output(self, t):
        q_cool, q_heat = self._q_cool, self._q_heat
        p_hvac = q_cool / self.cop_cool + q_heat / self.cop_heat
        return {"T_zone": self.T_a, "T_mass": self.T_m, "Q_cool": q_cool, "Q_heat": q_heat,
                "P_hvac": p_hvac, "P_elec": p_hvac + self.base_load}

    def update(self, t, inputs):
        dt = self.ctx.step_size
        clg = self.default_clg if inputs.clg_sp is None else float(inputs.clg_sp)
        htg = self.default_htg if inputs.htg_sp is None else float(inputs.htg_sp)
        T_out = float(inputs.T_out)
        gains = float(inputs.Q_int or 0.0) + self.A_sol * max(float(inputs.GHI or 0.0), 0.0)
        q_air, q_mass = self.f_conv * gains, (1.0 - self.f_conv) * gains
        free_a, _ = self._simulate(self.T_a, self.T_m, T_out, q_air, q_mass, 0.0, dt)
        q = 0.0
        if free_a > clg or free_a < htg:
            unit_a, _ = self._simulate(self.T_a, self.T_m, T_out, q_air, q_mass, 1000.0, dt)
            gain_per_w = (unit_a - free_a) / 1000.0
            target = clg if free_a > clg else htg
            q = (target - free_a) / gain_per_w
            q = max(-self.cool_cap, min(self.heat_cap, q))
        self.T_a, self.T_m = self._simulate(self.T_a, self.T_m, T_out, q_air, q_mass, q, dt)
        self._q_cool, self._q_heat = max(-q, 0.0), max(q, 0.0)
