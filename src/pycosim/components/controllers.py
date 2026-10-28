"""Reusable control logic: thermostat schedules, pre-cooling, PI loops, battery dispatch."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from ..core.component import Component

__all__ = ["ThermostatSchedule", "PrecoolingController", "PIController", "PeakShavingController"]


class ThermostatSchedule(Component):
    """Occupied/unoccupied cooling and heating set-points.

    Outputs ``clg_sp`` and ``htg_sp`` [C].
    """

    outputs = {"clg_sp": {"default": None, "kind": "hold"}, "htg_sp": {"default": None, "kind": "hold"}}

    def __init__(self, name: str, occupied_hours: Tuple[int, int] = (6, 22), cooling: Tuple[float, float] = (24.0, 29.4),
                 heating: Tuple[float, float] = (21.0, 15.6), weekend_occupied: bool = False):
        super().__init__(name)
        self.occupied_hours = occupied_hours
        self.cooling, self.heating = cooling, heating
        self.weekend_occupied = weekend_occupied

    def occupied(self, t: datetime) -> bool:
        if t.weekday() >= 5 and not self.weekend_occupied:
            return False
        return self.occupied_hours[0] <= t.hour < self.occupied_hours[1]

    def step(self, t, inputs):
        i = 0 if self.occupied(t) else 1
        return {"clg_sp": self.cooling[i], "htg_sp": self.heating[i]}


class PrecoolingController(Component):
    """Classic grid-interactive strategy: pre-cool before the peak, relax during it.

    During ``precool_hours`` before the peak window the cooling set-point is
    lowered to ``precool_sp`` (charging the building's thermal mass); during
    the peak it is raised to ``peak_sp``; otherwise ``base_sp`` applies (or
    ``unoccupied_sp`` outside ``occupied_hours``).

    Optional input ``enable`` (0/1, default 1) lets a coordinator switch the
    strategy on or off. Output ``clg_sp`` [C].
    """

    inputs = {"enable": 1}
    outputs = {"clg_sp": {"default": None, "kind": "hold"}}

    def __init__(self, name: str, peak_hours: Tuple[int, int] = (16, 21), precool_hours: float = 3.0,
                 base_sp: float = 24.0, precool_sp: float = 22.0, peak_sp: float = 26.5,
                 occupied_hours: Optional[Tuple[int, int]] = None, unoccupied_sp: Optional[float] = None,
                 weekdays_only: bool = True):
        super().__init__(name)
        self.peak_hours = peak_hours
        self.precool_hours = precool_hours
        self.base_sp, self.precool_sp, self.peak_sp = base_sp, precool_sp, peak_sp
        self.occupied_hours = occupied_hours
        self.unoccupied_sp = unoccupied_sp
        self.weekdays_only = weekdays_only

    def mode(self, t: datetime) -> str:
        hour = t.hour + t.minute / 60.0
        weekend = t.weekday() >= 5
        occ = self.occupied_hours
        if occ is not None and not (occ[0] <= hour < occ[1]):
            return "unoccupied" if self.unoccupied_sp is not None else "base"
        if weekend and self.weekdays_only:
            return "base"
        start, end = self.peak_hours
        if start <= hour < end:
            return "peak"
        if start - self.precool_hours <= hour < start:
            return "precool"
        return "base"

    def step(self, t, inputs):
        mode = self.mode(t)
        if mode == "unoccupied":
            return self.unoccupied_sp
        if not inputs.enable or mode == "base":
            return self.base_sp
        return self.precool_sp if mode == "precool" else self.peak_sp


class PIController(Component):
    """Discrete PI controller with output limits and anti-windup.

    Inputs: ``measurement`` and (optionally) ``setpoint``. Output: ``u``.
    ``reverse=True`` for cooling-type loops (more output when the measurement
    is above the set-point).
    """

    inputs = {"measurement": None, "setpoint": None}
    outputs = {"u": {"default": 0.0, "kind": "hold"}}

    def __init__(self, name: str, kp: float, ki: float = 0.0, setpoint: float = 0.0, u_min: float = 0.0,
                 u_max: float = 1.0, reverse: bool = False):
        super().__init__(name)
        self.kp, self.ki = kp, ki
        self.default_setpoint = setpoint
        self.u_min, self.u_max = u_min, u_max
        self.reverse = reverse
        self.integral = 0.0

    def setup(self, ctx) -> None:
        self.integral = 0.0

    def step(self, t, inputs):
        if inputs.measurement is None:
            return self.u_min
        sp = self.default_setpoint if inputs.setpoint is None else inputs.setpoint
        err = (inputs.measurement - sp) if self.reverse else (sp - inputs.measurement)
        dt = self.ctx.step_size
        self.integral += err * dt
        u = self.kp * err + self.ki * self.integral
        if u > self.u_max or u < self.u_min:
            limit = self.u_max if u > self.u_max else self.u_min
            if self.ki:
                # anti-windup: clamp the integral so that the output sits exactly at the limit
                self.integral = (limit - self.kp * err) / self.ki
            u = limit
        return u


class PeakShavingController(Component):
    """Rule-based battery dispatch for peak shaving, PV self-consumption and TOU arbitrage.

    Inputs
    ------
    P_load : W
        Net demand *before* the battery (loads minus PV; negative = surplus).
    soc : -
        Battery state of charge.
    is_peak : 0/1
        On-peak indicator from a tariff.

    Output ``P_set`` [W] (positive = charge), to be connected to
    :class:`Battery.P_set`.
    """

    inputs = {"P_load": 0.0, "soc": 0.5, "is_peak": 0}
    outputs = {"P_set": {"default": 0.0, "kind": "hold"}}

    def __init__(self, name: str, import_limit_kw: float, charge_kw: float, soc_target: float = 0.9,
                 charge_hours: Tuple[int, int] = (0, 6), store_pv_surplus: bool = True,
                 discharge_on_peak: bool = True):
        super().__init__(name)
        self.limit = import_limit_kw * 1000.0
        self.charge_w = charge_kw * 1000.0
        self.soc_target = soc_target
        self.charge_hours = charge_hours
        self.store_pv_surplus = store_pv_surplus
        self.discharge_on_peak = discharge_on_peak
        self.output_ports["P_set"].unit = "W"

    def step(self, t, inputs):
        load = float(inputs.P_load or 0.0)
        soc = float(inputs.soc if inputs.soc is not None else 0.5)
        if load < 0 and self.store_pv_surplus:
            return -load  # absorb PV surplus
        if inputs.is_peak and self.discharge_on_peak:
            return -max(load, 0.0)  # cover the whole on-peak demand if possible
        if load > self.limit:
            return -(load - self.limit)
        if self.charge_hours[0] <= t.hour < self.charge_hours[1] and soc < self.soc_target:
            return max(0.0, min(self.charge_w, self.limit - load))
        return 0.0
