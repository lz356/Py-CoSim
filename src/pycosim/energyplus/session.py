"""Turn EnergyPlus' callback-driven run into a lock-step, steppable process.

EnergyPlus owns its time loop and calls Python back at fixed points. To let an
external master decide *when* EnergyPlus may advance, the session blocks
inside the callback at the beginning of every zone time step:

.. code-block:: text

    EnergyPlus (worker)                         master (CoSimulation)
    -------------------                         ---------------------
    end of step k-1  -> snapshot outputs
    begin of step k  -> send ("obs", t_k, y)  ->  output(t_k) returns y
                       ... blocked ...            other components step
                    <- recv ("act", u)        <-  update(t_k, u)
    set actuators u, simulate [t_k, t_k+dt)

Outputs are read in ``callback_end_zone_timestep_after_zone_reporting`` so
that the value observed at master time ``t_k`` is exactly the value
EnergyPlus reports with time stamp ``t_k`` in its own output files.
Timestamps are built from (month, day, hour, zone time-step number) because
``current_time``/``minutes`` follow adaptive HVAC sub-steps.

This module must only depend on the standard library (it runs inside the
light-weight worker process).
"""
from __future__ import annotations

import math
import traceback
from typing import Any, Dict, List, Optional, Tuple

RUN_PERIOD_WEATHER = 3  # EnergyPlus KindOfSim for weather-file run periods
_METER_FACTORS = {"J": None, "W": "rate", "kW": "rate_k", "kWh": 1.0 / 3.6e6, "Wh": 1.0 / 3600.0, "MJ": 1e-6}


class _Detach(Exception):
    pass


class EPlusSession:
    """Runs one EnergyPlus simulation and exchanges data over ``channel``.

    ``channel`` needs ``send(obj)`` and ``recv()`` (a multiprocessing
    connection or a pair of queues).
    """

    def __init__(self, api, cfg: Dict[str, Any], channel):
        self.api = api
        self.cfg = cfg
        self.channel = channel
        self.sensors: Dict[str, Tuple[str, str]] = {k: tuple(v) for k, v in cfg.get("sensors", {}).items()}
        self.meters: Dict[str, Tuple[str, str]] = {k: tuple(v) for k, v in cfg.get("meters", {}).items()}
        self.internals: Dict[str, Tuple[str, str]] = {k: tuple(v) for k, v in cfg.get("internal_variables", {}).items()}
        self.actuators: Dict[str, Tuple[str, str, str]] = {k: tuple(v) for k, v in cfg.get("actuators", {}).items()}
        self.mode = cfg.get("mode", "cosim")
        # exchange with the master every `every` zone time steps; outputs are aggregated in between
        self.every = max(int(cfg.get("exchange_every", 1) or 1), 1)
        self.aggregate: Dict[str, str] = dict(cfg.get("aggregate", {}))
        self._acc: Dict[str, float] = {}
        self._n_acc = 0
        self._since = 0
        self._last_act: Dict[str, Any] = {}
        self.skip_until: Optional[Tuple[int, int, int, int]] = tuple(cfg["skip_until"]) if cfg.get("skip_until") else None
        self.resolved = False
        self.failed = False
        self.detached = False
        self.snapshot: Optional[Dict[str, float]] = None
        self.n_obs = 0
        self.n_per_hour = 0
        self.dt_s = 0.0
        self.h_sens: Dict[str, int] = {}
        self.h_met: Dict[str, int] = {}
        self.h_int: Dict[str, int] = {}
        self.h_act: Dict[str, int] = {}
        self.internal_values: Dict[str, float] = {}

    # --------------------------------------------------------------- running
    def run(self) -> int:
        api = self.api
        state = api.state_manager.new_state()
        self.state = state
        try:
            api.runtime.set_console_output_status(state, bool(self.cfg.get("verbose", False)))
        except Exception:  # very old API versions
            pass
        for var, key in self.sensors.values():
            api.exchange.request_variable(state, var, key)
        api.runtime.callback_begin_zone_timestep_after_init_heat_balance(state, self._on_begin)
        api.runtime.callback_end_zone_timestep_after_zone_reporting(state, self._on_end)
        rc = api.runtime.run_energyplus(state, list(self.cfg["args"]))
        if not self.failed:
            self.channel.send(("done", int(rc), self.snapshot, self.n_obs))
        try:
            api.state_manager.delete_state(state)
        except Exception:
            pass
        return int(rc)

    # ------------------------------------------------------------- callbacks
    def _on_begin(self, state) -> None:
        if self.failed or self.detached:
            return
        try:
            ex = self.api.exchange
            if not ex.api_data_fully_ready(state):
                return
            if ex.kind_of_sim(state) != RUN_PERIOD_WEATHER:
                return
            if not self.resolved:
                self._resolve(state)
                if self.failed or self.detached:
                    return
            if ex.warmup_flag(state):
                return
            stamp = self._stamp(state)
            if self.skip_until is not None:
                if stamp < self.skip_until:
                    return
                self.skip_until = None
            if self.every > 1:
                exchange = self._since % self.every == 0
                self._since += 1
                if not exchange:  # free-running sub-step: keep the last commands
                    if self._last_act:
                        self._apply(state, self._last_act)
                    return
                obs = self._aggregated() if self._n_acc else (
                    self.snapshot if self.snapshot is not None else self._read(state))
            else:
                obs = self.snapshot if self.snapshot is not None else self._read(state)
            self.channel.send(("obs", stamp, obs))
            self.n_obs += 1
            msg = self.channel.recv()
            kind = msg[0]
            if kind == "act":
                self._apply(state, msg[1])
                if self.every > 1:
                    self._last_act = dict(msg[1])
            elif kind == "detach":
                self._detach(state)
            else:
                raise RuntimeError(f"unexpected message {kind!r}")
        except _Detach:
            pass
        except (EOFError, BrokenPipeError, ConnectionResetError, OSError):
            # master went away -> nothing left to do
            self._fail_silently(state)
        except Exception:
            self._report("exception", traceback.format_exc())
            self._fail_silently(state)

    def _on_end(self, state) -> None:
        if not self.resolved or self.failed or self.detached:
            return
        try:
            if self.api.exchange.kind_of_sim(state) != RUN_PERIOD_WEATHER:
                return
            self.snapshot = self._read(state)
            if self.every > 1 and self.skip_until is None and self._since > 0:
                self._accumulate(self.snapshot)
        except Exception:
            self._report("exception", traceback.format_exc())
            self._fail_silently(state)

    # ---------------------------------------------------------- aggregation
    def _accumulate(self, values: Dict[str, float]) -> None:
        acc = self._acc
        for name, v in values.items():
            how = self.aggregate.get(name, "last")
            if how in ("average", "amount"):
                acc[name] = acc.get(name, 0.0) + float(v)
            else:
                acc[name] = v
        self._n_acc += 1

    def _aggregated(self) -> Dict[str, float]:
        out = {}
        n = self._n_acc
        for name, v in self._acc.items():
            out[name] = v / n if self.aggregate.get(name) == "average" else v
        self._acc = {}
        self._n_acc = 0
        return out

    # --------------------------------------------------------------- helpers
    def _stamp(self, state) -> Tuple[int, int, int, int]:
        ex = self.api.exchange
        step = ex.zone_time_step_number(state)
        minute = int(round((step - 1) * 60.0 / self.n_per_hour))
        return (ex.month(state), ex.day_of_month(state), ex.hour(state), minute)

    def _resolve(self, state) -> None:
        ex = self.api.exchange
        self.n_per_hour = int(ex.num_time_steps_in_hour(state))
        self.dt_s = 3600.0 / self.n_per_hour
        missing: List[Tuple[str, Tuple[str, ...]]] = []
        for name, (var, key) in self.sensors.items():
            h = ex.get_variable_handle(state, var, key)
            (missing.append(("sensor", name, (var, key))) if h < 0 else self.h_sens.__setitem__(name, h))
        for name, (meter, _unit) in self.meters.items():
            h = ex.get_meter_handle(state, meter)
            (missing.append(("meter", name, (meter,))) if h < 0 else self.h_met.__setitem__(name, h))
        for name, (var, key) in self.internals.items():
            h = ex.get_internal_variable_handle(state, var, key)
            (missing.append(("internal_variable", name, (var, key))) if h < 0 else self.h_int.__setitem__(name, h))
        for name, (ctype, control, key) in self.actuators.items():
            h = ex.get_actuator_handle(state, ctype, control, key)
            (missing.append(("actuator", name, (ctype, control, key))) if h < 0 else self.h_act.__setitem__(name, h))
        self.resolved = True
        if self.mode == "list_handles" or missing:
            try:
                available = ex.list_available_api_data_csv(state)
                if isinstance(available, bytes):
                    available = available.decode("utf-8", errors="replace")
            except Exception:
                available = ""
            if self.mode == "list_handles":
                self.channel.send(("handles", available))
                self._detach(state, raise_=False)
                self.failed = True  # suppress further messages; "done" not needed
                return
            self.channel.send(("error", "handles", missing, available))
            self.failed = True
            self._try_stop(state)
            return
        for name, h in self.h_int.items():
            self.internal_values[name] = ex.get_internal_variable_value(state, h)
        info = {
            "timesteps_per_hour": self.n_per_hour,
            "internal_variables": dict(self.internal_values),
        }
        self.channel.send(("ready", info))

    def _read(self, state) -> Dict[str, float]:
        ex = self.api.exchange
        out: Dict[str, float] = {}
        for name, h in self.h_sens.items():
            out[name] = ex.get_variable_value(state, h)
        for name, h in self.h_met.items():
            value = ex.get_meter_value(state, h)
            unit = self.meters[name][1]
            factor = _METER_FACTORS.get(unit)
            if factor == "rate":
                value = value / self.dt_s
            elif factor == "rate_k":
                value = value / self.dt_s / 1000.0
            elif factor is not None:
                value = value * factor
            out[name] = value
        out.update(self.internal_values)
        return out

    def _apply(self, state, values: Dict[str, Any]) -> None:
        ex = self.api.exchange
        for name, value in values.items():
            h = self.h_act[name]
            if value is None or (isinstance(value, float) and math.isnan(value)):
                ex.reset_actuator(state, h)
            else:
                ex.set_actuator_value(state, h, float(value))

    def _detach(self, state, raise_: bool = True) -> None:
        self.detached = True
        self._try_stop(state)
        if raise_:
            raise _Detach()

    def _try_stop(self, state) -> None:
        stop = getattr(self.api.runtime, "stop_simulation", None)
        if stop is not None:
            try:
                stop(state)
            except Exception:
                pass

    def _report(self, kind: str, text: str) -> None:
        try:
            self.channel.send(("error", kind, text))
        except Exception:
            pass

    def _fail_silently(self, state) -> None:
        self.failed = True
        self._try_stop(state)
