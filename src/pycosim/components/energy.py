"""Distributed energy resources and grid-side components: PV, battery, tariffs, metering."""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple, Union

from ..core.component import Component

__all__ = ["PVSystem", "Battery", "TimeOfUseTariff", "GridMeter"]


class PVSystem(Component):
    """Simple PVWatts-style photovoltaic array.

    Plane-of-array irradiance from an isotropic-sky transposition of
    ``GHI``/``DNI``/``DHI``; cell temperature from the NOCT model; DC power
    with a linear temperature coefficient; fixed system losses; inverter
    efficiency and AC clipping.

    Inputs: ``GHI``, ``DNI``, ``DHI`` [W/m2], ``T_out`` [C], ``solar_zenith``,
    ``solar_azimuth`` [deg] (all provided by :class:`EPWWeather`).
    Outputs: ``P_ac`` [W], ``POA`` [W/m2].
    """

    inputs = {"GHI": 0.0, "DNI": 0.0, "DHI": 0.0, "T_out": 25.0, "solar_zenith": 90.0, "solar_azimuth": 180.0}
    outputs = {"P_ac": {"default": 0.0, "kind": "instant"}, "POA": {"default": 0.0, "kind": "instant"}}

    def __init__(self, name: str, capacity_kw: float, tilt: float = 20.0, azimuth: float = 180.0,
                 losses: float = 0.14, temp_coeff: float = -0.0037, noct: float = 45.0, inverter_eff: float = 0.96,
                 dc_ac_ratio: float = 1.2, albedo: float = 0.2):
        super().__init__(name)
        self.capacity_w = capacity_kw * 1000.0
        self.tilt, self.azimuth = tilt, azimuth
        self.losses, self.temp_coeff, self.noct = losses, temp_coeff, noct
        self.inverter_eff, self.ac_limit = inverter_eff, self.capacity_w / dc_ac_ratio
        self.albedo = albedo
        self.output_ports["P_ac"].unit = "W"
        self.output_ports["POA"].unit = "W/m2"

    def poa(self, ghi: float, dni: float, dhi: float, zenith: float, sun_az: float) -> float:
        beta = math.radians(self.tilt)
        z = math.radians(zenith)
        if zenith >= 90.0:
            beam = 0.0
        else:
            cos_aoi = (math.cos(z) * math.cos(beta)
                       + math.sin(z) * math.sin(beta) * math.cos(math.radians(sun_az - self.azimuth)))
            beam = max(dni, 0.0) * max(cos_aoi, 0.0)
        diffuse = max(dhi, 0.0) * (1.0 + math.cos(beta)) / 2.0
        ground = max(ghi, 0.0) * self.albedo * (1.0 - math.cos(beta)) / 2.0
        return beam + diffuse + ground

    def step(self, t, inputs):
        poa = self.poa(inputs.GHI, inputs.DNI, inputs.DHI, inputs.solar_zenith, inputs.solar_azimuth)
        t_cell = inputs.T_out + poa * (self.noct - 20.0) / 800.0
        p_dc = self.capacity_w * poa / 1000.0 * (1.0 + self.temp_coeff * (t_cell - 25.0)) * (1.0 - self.losses)
        p_ac = min(max(p_dc, 0.0) * self.inverter_eff, self.ac_limit)
        return {"P_ac": p_ac, "POA": poa}


class Battery(Component):
    """Battery energy storage with power limits, SOC limits and round-trip losses.

    Sign convention: ``P_set > 0`` charges (consumes power from the bus),
    ``P_set < 0`` discharges. The battery delivers what it can within its
    limits over the current step and reports the *actual* power ``P``.

    Inputs: ``P_set`` [W]. Outputs: ``P`` [W] -- the mean power over the
    step that starts at ``t`` (time meaning ``average``/``ahead``) --, ``soc``
    [-] and ``energy`` [kWh] at the end of that step.
    """

    inputs = {"P_set": 0.0}
    outputs = {"P": {"default": 0.0, "kind": "average", "interval": "ahead"}, "soc": None, "energy": None}

    def __init__(self, name: str, capacity_kwh: float, max_charge_kw: float, max_discharge_kw: Optional[float] = None,
                 eff_charge: float = 0.95, eff_discharge: float = 0.95, soc_init: float = 0.5, soc_min: float = 0.1,
                 soc_max: float = 0.95):
        super().__init__(name)
        if not 0 <= soc_min < soc_max <= 1:
            raise ValueError("require 0 <= soc_min < soc_max <= 1")
        self.capacity_j = capacity_kwh * 3.6e6
        self.max_charge = max_charge_kw * 1000.0
        self.max_discharge = (max_discharge_kw if max_discharge_kw is not None else max_charge_kw) * 1000.0
        self.eff_c, self.eff_d = eff_charge, eff_discharge
        self.soc_min, self.soc_max = soc_min, soc_max
        self.soc_init = float(soc_init)
        self.soc = self.soc_init
        self.output_ports["soc"].default = self.soc
        self.output_ports["energy"].default = self.soc * capacity_kwh
        self.output_ports["P"].unit = "W"
        self.output_ports["energy"].unit = "kWh"

    def setup(self, ctx) -> None:
        self.soc = self.soc_init

    def step(self, t, inputs):
        dt = self.ctx.step_size
        p = float(inputs.P_set or 0.0)
        e = self.soc * self.capacity_j
        if p > 0:  # charge
            room = (self.soc_max * self.capacity_j - e) / (self.eff_c * dt)
            p = min(p, self.max_charge, max(room, 0.0))
            e += p * self.eff_c * dt
        elif p < 0:  # discharge
            avail = (e - self.soc_min * self.capacity_j) * self.eff_d / dt
            p = -min(-p, self.max_discharge, max(avail, 0.0))
            e += p / self.eff_d * dt
        self.soc = e / self.capacity_j
        return {"P": p, "soc": self.soc, "energy": e / 3.6e6}


class TimeOfUseTariff(Component):
    """Time-of-use electricity price.

    Parameters
    ----------
    peak_hours : (start, end)
        On-peak window in local hours, end exclusive (default 16-21 h).
    peak_price, offpeak_price : float
        Energy prices in $/kWh.
    shoulder_hours, shoulder_price : optional mid-peak window.
    weekdays_only : bool
        Weekends are off-peak all day.
    months : iterable of int, optional
        Months in which the peak window applies (e.g. summer ``range(5, 11)``).

    Outputs: ``price`` [$/kWh], ``is_peak`` [0/1].
    """

    outputs = {"price": {"default": None, "kind": "hold"}, "is_peak": {"default": 0, "kind": "hold"}}

    def __init__(self, name: str, peak_hours: Tuple[int, int] = (16, 21), peak_price: float = 0.30,
                 offpeak_price: float = 0.10, shoulder_hours: Optional[Tuple[int, int]] = None,
                 shoulder_price: Optional[float] = None, weekdays_only: bool = True,
                 months: Optional[Iterable[int]] = None):
        super().__init__(name)
        self.peak_hours = peak_hours
        self.peak_price, self.offpeak_price = peak_price, offpeak_price
        self.shoulder_hours, self.shoulder_price = shoulder_hours, shoulder_price
        self.weekdays_only = weekdays_only
        self.months = set(months) if months is not None else None
        self.output_ports["price"].unit = "$/kWh"

    def is_peak(self, t: datetime) -> bool:
        if self.weekdays_only and t.weekday() >= 5:
            return False
        if self.months is not None and t.month not in self.months:
            return False
        return self.peak_hours[0] <= t.hour < self.peak_hours[1]

    def price_at(self, t: datetime) -> float:
        if self.is_peak(t):
            return self.peak_price
        if (self.shoulder_hours and self.shoulder_price is not None
                and self.shoulder_hours[0] <= t.hour < self.shoulder_hours[1]
                and not (self.weekdays_only and t.weekday() >= 5)
                and (self.months is None or t.month in self.months)):
            return self.shoulder_price
        return self.offpeak_price

    def step(self, t, inputs):
        return {"price": self.price_at(t), "is_peak": int(self.is_peak(t))}


class GridMeter(Component):
    """Point of common coupling: imports/exports, energy, cost and peak demand.

    At each of its steps the meter accounts for the interval that just ended,
    ``[t - h, t)``.

    Inputs:

    * ``P_load``, ``P_gen``, ``P_bat`` [W] -- consumption, generation and
      battery charging (positive) power; with ``aligned=True`` (default) they
      are read as the mean over the meter's step that just ended, whatever the
      producers' steps and time meanings are (connect several with a pattern,
      e.g. ``sim.subscribe("grid.P_load", "*.P_elec", reduce="sum")``);
    * ``P_net`` [W] -- net import (positive) read as the latest value (the
      Py-CoSim 2.0 behaviour; it is added to the other inputs);
    * ``price``, ``export_price`` [$/kWh] -- with ``aligned=True`` the mean
      price over the step that just ended (the price that applied to that
      energy), otherwise the latest price.

    Outputs: ``P_import``, ``P_export`` [W, mean over the step that ended],
    ``E_import`` [kWh, cumulative], ``E_export`` [kWh, cumulative], ``cost``
    [$, cumulative], ``peak`` [kW, running maximum of the import averaged over
    ``demand_window``].
    """

    inputs = {"P_net": 0.0, "P_load": 0.0, "P_gen": 0.0, "P_bat": 0.0, "price": 0.0, "export_price": 0.0}
    outputs = {"P_import": {"default": 0.0, "kind": "average", "interval": "past"},
               "P_export": {"default": 0.0, "kind": "average", "interval": "past"},
               "E_import": 0.0, "E_export": 0.0, "cost": 0.0, "peak": 0.0}

    def __init__(self, name: str, demand_window: str = "15min", aligned: bool = True):
        super().__init__(name)
        self.aligned = aligned
        if aligned:
            for port in ("P_load", "P_gen", "P_bat", "price", "export_price"):
                self.input_ports[port].read = "past"
        from ..core.timeutils import parse_duration

        self.demand_window = parse_duration(demand_window)
        self._e_imp = self._e_exp = self._cost = self._peak = 0.0
        self._window = []
        for port, unit in (("P_import", "W"), ("P_export", "W"), ("E_import", "kWh"), ("E_export", "kWh"),
                           ("cost", "$"), ("peak", "kW")):
            self.output_ports[port].unit = unit

    def setup(self, ctx) -> None:
        self._e_imp = self._e_exp = self._cost = self._peak = 0.0
        self._window = []

    def step(self, t, inputs):
        dt = self.ctx.step_size
        get = inputs.get
        p = (float(get("P_net") or 0.0) + float(get("P_load") or 0.0) - float(get("P_gen") or 0.0)
             + float(get("P_bat") or 0.0))
        imp, exp = max(p, 0.0), max(-p, 0.0)
        self._e_imp += imp * dt / 3.6e6
        self._e_exp += exp * dt / 3.6e6
        self._cost += (imp * (inputs.price or 0.0) - exp * (inputs.export_price or 0.0)) * dt / 3.6e6
        n = max(1, int(round(self.demand_window / dt)))
        self._window.append(imp)
        if len(self._window) > n:
            self._window.pop(0)
        if len(self._window) == n:
            self._peak = max(self._peak, sum(self._window) / n / 1000.0)
        return {"P_import": imp, "P_export": exp, "E_import": self._e_imp, "E_export": self._e_exp,
                "cost": self._cost, "peak": self._peak}
