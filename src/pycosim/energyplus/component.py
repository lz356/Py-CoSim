"""EnergyPlus as a steppable co-simulation component.

Example
-------
>>> office = EnergyPlusComponent(
...     "office",
...     model="small_office.idf",
...     weather="tucson.epw",
...     sensors={
...         "T_core": ("Zone Mean Air Temperature", "Core_ZN"),
...         "T_out": ("Site Outdoor Air Drybulb Temperature", "Environment"),
...     },
...     meters={"P_elec": ("Electricity:Facility", "W")},
...     actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")},
... )

Sensors, meters and internal variables become *outputs*; actuators become
*inputs*. An actuator input that is ``None`` (the default when unconnected)
releases the actuator so EnergyPlus' own schedules/controls apply.

Each instance runs EnergyPlus in its **own worker process** by default
(``executor="process"``): EnergyPlus is not thread-safe, processes give real
parallelism across buildings and a crashing model cannot take the master down.
``executor="thread"`` runs in-process (lower overhead, one instance at a time).
"""
from __future__ import annotations

import logging
import math
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple, Union

from ..core.component import Component
from ..core.errors import ConfigurationError, HandleNotFoundError, PyCoSimError, SynchronizationError
from ..core.timeutils import parse_duration
from .discovery import EnergyPlusInstall, find_energyplus
from .model import VALID_TIMESTEPS, EnergyPlusModel

__all__ = ["EnergyPlusComponent", "EnergyPlusRuntimeError"]

logger = logging.getLogger("pycosim.energyplus")
_THREAD_SLOT = threading.Semaphore(1)
# EnergyPlus (tested 22.2) is neither thread-safe nor re-entrant: concurrent states in one process deadlock,
# and a second simulation started in the same process after a first one has finished stalls at start-up.
_THREAD_RUNS = {"count": 0}


class EnergyPlusRuntimeError(PyCoSimError):
    """EnergyPlus terminated abnormally."""


# ------------------------------------------------------------------ spec parsing
def _parse_pair(name: str, spec: Any, what: str) -> Tuple[str, str]:
    if isinstance(spec, Mapping):
        return str(spec["variable"]), str(spec.get("key", "Environment"))
    if isinstance(spec, str):
        return spec, "Environment"
    if isinstance(spec, (list, tuple)) and len(spec) == 2:
        return str(spec[0]), str(spec[1])
    raise ConfigurationError(f"{what} {name!r}: expected (variable_name, key), got {spec!r}")


_METER_UNITS = ("J", "W", "kW", "kWh", "Wh", "MJ")


def _parse_meter(name: str, spec: Any) -> Tuple[str, str]:
    if isinstance(spec, str):
        meter, unit = spec, "J"
    elif isinstance(spec, Mapping):
        meter, unit = str(spec["meter"]), str(spec.get("unit", "J"))
    elif isinstance(spec, (list, tuple)) and len(spec) == 2:
        meter, unit = str(spec[0]), str(spec[1])
    else:
        raise ConfigurationError(f"meter {name!r}: expected 'Meter:Name' or (meter_name, unit), got {spec!r}")
    if unit not in _METER_UNITS:
        raise ConfigurationError(f"meter {name!r}: unit must be one of {_METER_UNITS} (got {unit!r})")
    return meter, unit


def _parse_actuator(name: str, spec: Any) -> Tuple[str, str, str]:
    if isinstance(spec, Mapping):
        return str(spec["component_type"]), str(spec["control_type"]), str(spec["key"])
    if isinstance(spec, (list, tuple)) and len(spec) == 3:
        return str(spec[0]), str(spec[1]), str(spec[2])
    raise ConfigurationError(f"actuator {name!r}: expected (component_type, control_type, key), got {spec!r}")


_UNITS_GUESS = (
    ("Temperature", "C"), ("Humidity Ratio", "kgWater/kgDryAir"), ("Relative Humidity", "%"),
    ("Rate per Area", "W/m2"), ("Energy", "J"), ("Rate", "W"), ("Power", "W"), ("Occupant Count", "people"),
)


def _guess_unit(variable: str) -> Optional[str]:
    for token, unit in _UNITS_GUESS:
        if token.lower() in variable.lower():
            return unit
    return None


# ------------------------------------------------------------------- endpoints
class _ProcessEndpoint:
    """Master side of a worker process connected through a local pipe/socket."""

    def __init__(self, cfg: Dict[str, Any], log_path: Path, startup_timeout: float):
        authkey = secrets.token_bytes(16)
        self.listener = Listener(authkey=authkey)
        env = os.environ.copy()
        env["PYCOSIM_AUTHKEY"] = authkey.hex()
        src_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = src_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        self.log_path = log_path
        self._log = open(log_path, "w", encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "pycosim.energyplus.worker", str(self.listener.address)],
            env=env, stdout=self._log, stderr=subprocess.STDOUT, cwd=str(log_path.parent),
            creationflags=creationflags,
        )
        # Accept the connection and send the configuration in the background so that many workers
        # can boot in parallel; connected() waits for it.
        self.conn = None
        self._startup_timeout = startup_timeout
        self._box: Dict[str, Any] = {}

        def handshake():
            try:
                conn = self.listener.accept()
                conn.send(cfg)
                self._box["conn"] = conn
            except Exception as exc:  # pragma: no cover - surfaced in connected()
                self._box["error"] = exc

        self._thread = threading.Thread(target=handshake, daemon=True)
        self._thread.start()

    def connected(self) -> None:
        """Block until the worker connected and received its configuration."""
        if self.conn is not None:
            return
        deadline = time.monotonic() + self._startup_timeout
        while self._thread.is_alive():
            self._thread.join(0.05)
            if self.proc.poll() is not None and self._thread.is_alive():
                self._close_listener()
                raise EnergyPlusRuntimeError(f"EnergyPlus worker exited during start-up.\n{self.log_tail()}")
            if time.monotonic() > deadline:
                self.kill()
                raise EnergyPlusRuntimeError(
                    f"EnergyPlus worker did not connect within {self._startup_timeout:.0f}s.\n{self.log_tail()}")
        if "error" in self._box:
            raise EnergyPlusRuntimeError(f"Could not connect to EnergyPlus worker: {self._box['error']}")
        self.conn = self._box["conn"]

    def _dead(self, exc: Optional[BaseException] = None) -> EnergyPlusRuntimeError:
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            pass
        detail = f" ({type(exc).__name__}: {exc})" if exc is not None else ""
        return EnergyPlusRuntimeError(
            f"EnergyPlus worker stopped (exit code {self.proc.returncode}){detail}.\n{self.log_tail()}")

    def send(self, msg) -> None:
        self.connected()
        try:
            self.conn.send(msg)
        except (BrokenPipeError, ConnectionResetError, EOFError, OSError) as exc:
            raise self._dead(exc) from None

    def recv(self, timeout: float):
        self.connected()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if self.conn.poll(0.2):
                    return self.conn.recv()
                if self.proc.poll() is not None and not self.conn.poll(0):
                    raise self._dead()
            except (BrokenPipeError, ConnectionResetError, EOFError, OSError) as exc:
                raise self._dead(exc) from None
            if time.monotonic() > deadline:
                raise EnergyPlusRuntimeError(f"No message from EnergyPlus within {timeout:.0f}s")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def join(self, timeout: float) -> Optional[int]:
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
        self.close()

    def close(self) -> None:
        for obj in (getattr(self, "conn", None),):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self._close_listener()
        try:
            self._log.close()
        except Exception:
            pass

    def _close_listener(self) -> None:
        try:
            self.listener.close()
        except Exception:
            pass

    def log_tail(self, n: int = 3000) -> str:
        try:
            self._log.flush()
            return Path(self.log_path).read_text(errors="replace")[-n:]
        except Exception:
            return ""


class _QueueChannel:
    def __init__(self, inbox: "queue.Queue", outbox: "queue.Queue"):
        self.inbox, self.outbox = inbox, outbox

    def send(self, msg) -> None:
        self.outbox.put(msg)

    def recv(self):
        msg = self.inbox.get()
        if msg is None:
            raise EOFError
        return msg


class _ThreadEndpoint:
    """Runs EnergyPlus in a background thread of the current process."""

    def __init__(self, cfg: Dict[str, Any], install: EnergyPlusInstall):
        if not _THREAD_SLOT.acquire(blocking=False):
            raise ConfigurationError(
                "Only one EnergyPlus instance can run in-thread per Python process (EnergyPlus is not "
                "thread-safe). Use executor='process' (the default) for multiple buildings."
            )
        self._released = False
        self.to_worker: "queue.Queue" = queue.Queue()
        self.from_worker: "queue.Queue" = queue.Queue()
        try:
            api_cls = install.import_api()
            from .session import EPlusSession

            self.session = EPlusSession(api_cls(), cfg, _QueueChannel(self.to_worker, self.from_worker))
        except BaseException:
            self._release()
            raise
        self.thread = threading.Thread(target=self._run, name="pycosim-energyplus", daemon=True)
        self.returncode: Optional[int] = None
        self.thread.start()

    def _run(self) -> None:
        import traceback

        try:
            self.returncode = self.session.run()
        except BaseException:
            self.from_worker.put(("error", "worker", traceback.format_exc()))
        finally:
            self._release()

    def _release(self) -> None:
        if not self._released:
            self._released = True
            _THREAD_SLOT.release()

    def send(self, msg) -> None:
        self.to_worker.put(msg)

    def recv(self, timeout: float):
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.from_worker.get(timeout=0.2)
            except queue.Empty:
                if not self.thread.is_alive() and self.from_worker.empty():
                    raise EnergyPlusRuntimeError("EnergyPlus thread stopped unexpectedly") from None
                if time.monotonic() > deadline:
                    raise EnergyPlusRuntimeError(f"No message from EnergyPlus within {timeout:.0f}s") from None

    def alive(self) -> bool:
        return self.thread.is_alive()

    def join(self, timeout: float) -> Optional[int]:
        self.thread.join(timeout)
        return None if self.thread.is_alive() else (self.returncode if self.returncode is not None else 0)

    def kill(self) -> None:
        # Threads cannot be killed: detach so EnergyPlus free-runs to its end without blocking.
        self.session.detached = True
        self.to_worker.put(("detach",))

    def close(self) -> None:
        pass

    def log_tail(self, n: int = 3000) -> str:
        return ""


# ------------------------------------------------------------------ component
class EnergyPlusComponent(Component):
    """Wrap an EnergyPlus model as a non-feedthrough co-simulation component.

    Parameters
    ----------
    name : str
        Component name.
    model : str, Path or EnergyPlusModel
        ``.idf`` / ``.epJSON`` file or an in-memory model.
    weather : str or Path
        EPW weather file.
    sensors : dict
        ``{output_name: (variable_name, key)}``; ``key`` is the zone/object
        name or ``"Environment"`` for site variables.
    meters : dict
        ``{output_name: meter_name}`` (value in J per time step) or
        ``{output_name: (meter_name, unit)}`` with unit in J, Wh, kWh, MJ
        (energy per step) or W, kW (average power over the step).
    actuators : dict
        ``{input_name: (component_type, control_type, key)}``.
    internal_variables : dict
        ``{output_name: (variable_name, key)}`` for static data such as
        ``("Zone Floor Area", "Core_ZN")``.
    timestep : int, optional
        EnergyPlus time steps per hour. By default derived from the master step.
    sync_run_period : bool
        Rewrite the model's RunPeriod to match the co-simulation window (default).
    sizing : {"model", "weather_file"}
        ``"weather_file"`` sizes HVAC from the extreme periods of the EPW file.
    daylight_saving : bool, optional
        ``False`` removes daylight-saving time from the model so its schedules
        follow local standard time like the co-simulation clock (``None``
        keeps the model as it is and logs a note if DST is defined).
    preprocess : callable, optional
        ``preprocess(model)`` to edit the :class:`EnergyPlusModel` before running.
    executor : {"process", "thread"}
        Where EnergyPlus runs (see module docstring). ``"thread"`` runs
        EnergyPlus inside the current Python process; because EnergyPlus can
        run only once per process, later thread-mode runs in the same process
        automatically use a worker process.
    energyplus_dir : str, optional
        EnergyPlus installation folder (auto-detected otherwise).
    verbose : bool
        Show EnergyPlus console output (in the worker log).
    timeout : float
        Seconds to wait for EnergyPlus at any single exchange.
    extra_args : list of str
        Additional EnergyPlus command line arguments (e.g. ``["-r"]``).
    """

    direct_feedthrough = False

    def __init__(
        self,
        name: str,
        model: Union[str, Path, EnergyPlusModel],
        weather: Union[str, Path],
        *,
        sensors: Optional[Mapping[str, Any]] = None,
        meters: Optional[Mapping[str, Any]] = None,
        actuators: Optional[Mapping[str, Any]] = None,
        internal_variables: Optional[Mapping[str, Any]] = None,
        timestep: Optional[int] = None,
        sync_run_period: bool = True,
        sizing: str = "model",
        daylight_saving: Optional[bool] = None,
        preprocess: Optional[Callable[[EnergyPlusModel], Any]] = None,
        executor: str = "process",
        energyplus_dir: Optional[Union[str, Path]] = None,
        verbose: bool = False,
        timeout: float = 600.0,
        extra_args: Optional[Sequence[str]] = None,
    ) -> None:
        super().__init__(name)
        if executor not in ("process", "thread"):
            raise ConfigurationError("executor must be 'process' or 'thread'")
        if sizing not in ("model", "weather_file"):
            raise ConfigurationError("sizing must be 'model' or 'weather_file'")
        self.model_source = model
        self.weather = Path(weather)
        self.sensors = {k: _parse_pair(k, v, "sensor") for k, v in (sensors or {}).items()}
        self.meters = {k: _parse_meter(k, v) for k, v in (meters or {}).items()}
        self.actuators = {k: _parse_actuator(k, v) for k, v in (actuators or {}).items()}
        self.internal_variables = {k: _parse_pair(k, v, "internal variable") for k, v in (internal_variables or {}).items()}
        self.timestep = timestep
        if timestep is not None:
            if int(timestep) not in VALID_TIMESTEPS:
                raise ConfigurationError(f"timestep must be one of {VALID_TIMESTEPS}")
            self.step_size = 3600.0 / int(timestep)
        self.sync_run_period = sync_run_period
        self.sizing = sizing
        self.daylight_saving = daylight_saving
        self.preprocess = preprocess
        self.executor = executor
        self.energyplus_dir = energyplus_dir
        self.verbose = verbose
        self.timeout = float(timeout)
        self.extra_args = list(extra_args or [])
        for out_name, (var, _key) in self.sensors.items():
            self.add_output(out_name, unit=_guess_unit(var), description=f"EnergyPlus variable '{var}'")
        for out_name, (meter, unit) in self.meters.items():
            self.add_output(out_name, unit=unit, description=f"EnergyPlus meter '{meter}'")
        for out_name, (var, _key) in self.internal_variables.items():
            self.add_output(out_name, description=f"EnergyPlus internal variable '{var}'")
        for in_name, spec in self.actuators.items():
            self.add_input(in_name, default=None, description=f"EnergyPlus actuator {spec}")
        overlap = set(self.output_ports) & set(self.input_ports)
        if overlap:
            raise ConfigurationError(f"{name}: names used both as sensor and actuator: {sorted(overlap)}")
        self._install: Optional[EnergyPlusInstall] = None
        self._model: Optional[EnergyPlusModel] = None
        self._endpoint = None
        self._done_msg = None
        self.final_outputs: Optional[Dict[str, float]] = None
        self.info: Dict[str, Any] = {}
        self._worker_wall = 0.0

    # ----------------------------------------------------------------- model
    @property
    def install(self) -> EnergyPlusInstall:
        if self._install is None:
            self._install = find_energyplus(self.energyplus_dir)
        return self._install

    @property
    def model(self) -> EnergyPlusModel:
        """The (copied) EnergyPlus model that will be simulated."""
        if self._model is None:
            if isinstance(self.model_source, EnergyPlusModel):
                self._model = self.model_source.copy()
            else:
                self._model = EnergyPlusModel.load(self.model_source, self.install)
        return self._model

    def suggest_clock(self) -> Dict[str, Any]:
        """Clock derived from the model (used when CoSimulation gets no start/end/step)."""
        first, last, _year = self.model.get_run_period()
        ts = self.timestep or self.model.timesteps_per_hour
        return {
            "start": datetime(first.year, first.month, first.day),
            "end": datetime(last.year, last.month, last.day) + timedelta(days=1),
            "step": 3600.0 / ts,
        }

    # -------------------------------------------------------------- lifecycle
    def setup(self, ctx) -> None:
        if not self.weather.exists():
            raise FileNotFoundError(f"Weather file not found: {self.weather}")
        step = ctx.step_size
        n = 3600.0 / step
        if abs(n - round(n)) > 1e-9 or int(round(n)) not in VALID_TIMESTEPS:
            raise ConfigurationError(
                f"{self.name}: a step of {step:g}s gives {n:g} EnergyPlus time steps per hour; "
                f"EnergyPlus needs one of {VALID_TIMESTEPS} (e.g. step='15min')"
            )
        offset = (ctx.start - datetime(ctx.start.year, ctx.start.month, ctx.start.day)).total_seconds()
        if self.sync_run_period and abs(offset / step - round(offset / step)) > 1e-9:
            raise ConfigurationError(
                f"{self.name}: the co-simulation starts at {ctx.start:%H:%M}, which is not a multiple of the "
                f"EnergyPlus time step ({step:g}s) after midnight")
        # fresh state for every run (components may be reused, e.g. by a Gymnasium environment)
        self._model = None
        self._done_msg = None
        self.final_outputs = None
        self.info = {}
        model = self.model
        model.set_timesteps_per_hour(int(round(n)))
        skip_until = None
        if self.sync_run_period:
            last = (ctx.end - timedelta(seconds=1)).date()
            model.set_run_period(ctx.start.date(), last)
            if ctx.start.time() != datetime.min.time():
                skip_until = [ctx.start.month, ctx.start.day, ctx.start.hour, ctx.start.minute]
        if self.sizing == "weather_file":
            model.use_weather_file_sizing()
        if self.daylight_saving is False:
            model.remove_daylight_saving()
        elif self.daylight_saving is None and model.has_daylight_saving:
            logger.info("%s: the model uses daylight-saving time; its schedules are shifted by 1 h in summer "
                        "relative to the co-simulation clock (local standard time). Pass daylight_saving=False "
                        "to remove it.", self.name)
        if self.preprocess is not None:
            self.preprocess(model)
        workdir = ctx.output_dir
        input_path = model.save(workdir / "in.epJSON")
        eplus_out = workdir / "eplus"
        eplus_out.mkdir(parents=True, exist_ok=True)
        args = ["-w", str(self.weather.resolve()), "-d", str(eplus_out.resolve())] + self.extra_args + [str(input_path.resolve())]
        cfg = {
            "eplus_root": str(self.install.root),
            "args": args,
            "sensors": {k: list(v) for k, v in self.sensors.items()},
            "meters": {k: list(v) for k, v in self.meters.items()},
            "internal_variables": {k: list(v) for k, v in self.internal_variables.items()},
            "actuators": {k: list(v) for k, v in self.actuators.items()},
            "verbose": self.verbose,
            "skip_until": skip_until,
            "mode": "cosim",
        }
        self._t_start = time.perf_counter()
        executor = self.executor
        if executor == "thread" and _THREAD_RUNS["count"] > 0:
            logger.warning(
                "%s: EnergyPlus already ran in this Python process and cannot be started in-process again; "
                "using executor='process' instead.", self.name)
            executor = "process"
        self._executor_used = executor
        if executor == "process":
            self._endpoint = _ProcessEndpoint(cfg, workdir / "worker.log", startup_timeout=min(self.timeout, 120.0))
        else:
            _THREAD_RUNS["count"] += 1
            self._endpoint = _ThreadEndpoint(cfg, self.install)

    def ready(self) -> None:
        """Wait until EnergyPlus finished input processing, sizing and resolved all handles."""
        msg = self._endpoint.recv(self.timeout)
        self._handle_startup(msg)

    def _handle_startup(self, msg) -> None:
        kind = msg[0]
        if kind == "ready":
            self.info = dict(msg[1])
            self.info["energyplus_version"] = self.install.version_str
            return
        if kind == "error" and msg[1] == "handles":
            missing, available = msg[2], msg[3]
            raise HandleNotFoundError(_explain_missing(self.name, missing, available), missing)
        if kind == "done":
            raise EnergyPlusRuntimeError(
                f"{self.name}: EnergyPlus finished (exit code {msg[1]}) before the co-simulation started.\n"
                + self._err_tail()
            )
        if kind == "error":
            raise EnergyPlusRuntimeError(f"{self.name}: EnergyPlus worker error:\n{msg[-1]}\n{self._err_tail()}")
        raise EnergyPlusRuntimeError(f"{self.name}: unexpected start-up message {kind!r}")

    def output(self, t: datetime) -> Dict[str, float]:
        msg = self._endpoint.recv(self.timeout)
        kind = msg[0]
        if kind == "obs":
            month, day, hour, minute = msg[1]
            if (t.month, t.day, t.hour, t.minute) != (month, day, hour, minute):
                raise SynchronizationError(
                    f"{self.name}: EnergyPlus is at {month:02d}-{day:02d} {hour:02d}:{minute:02d} but the master "
                    f"clock is at {t:%m-%d %H:%M}. Check the RunPeriod/time step or use sync_run_period=True."
                )
            return msg[2]
        if kind == "done":
            self._done_msg = msg
            if msg[1] != 0:
                raise EnergyPlusRuntimeError(
                    f"{self.name}: EnergyPlus terminated with exit code {msg[1]} at {t} (see the error file).\n"
                    + self._err_tail())
            raise SynchronizationError(
                f"{self.name}: EnergyPlus reached the end of its RunPeriod at {t} before the master clock. "
                "Make the RunPeriod cover the co-simulation window (or keep sync_run_period=True)."
            )
        if kind == "error":
            raise EnergyPlusRuntimeError(f"{self.name}: EnergyPlus error at {t}:\n{msg[-1]}\n{self._err_tail()}")
        raise EnergyPlusRuntimeError(f"{self.name}: unexpected message {kind!r}")

    def update(self, t: datetime, inputs) -> None:
        values = {}
        for name in self.actuators:
            v = inputs[name]
            if v is not None:
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise ConfigurationError(
                        f"{self.name}: actuator input {name!r} received {v!r}; expected a number or None "
                        "(None/NaN releases the actuator)") from None
                if math.isnan(v):
                    v = None
                elif math.isinf(v):
                    raise ConfigurationError(f"{self.name}: actuator input {name!r} received {v}")
            values[name] = v
        self._endpoint.send(("act", values))

    def finalize(self) -> None:
        ep = self._endpoint
        if ep is None:
            return
        try:
            if self._done_msg is None:
                while True:
                    msg = ep.recv(self.timeout)
                    if msg[0] == "obs":  # EnergyPlus has steps beyond the master window
                        ep.send(("detach",))
                        continue
                    if msg[0] == "done":
                        self._done_msg = msg
                        break
                    if msg[0] == "error":
                        raise EnergyPlusRuntimeError(f"{self.name}: {msg[-1]}\n{self._err_tail()}")
            rc = self._done_msg[1]
            self.final_outputs = self._done_msg[2]
            ep.join(60.0)
            self._worker_wall = time.perf_counter() - self._t_start
            if rc != 0:
                raise EnergyPlusRuntimeError(f"{self.name}: EnergyPlus exited with code {rc}.\n{self._err_tail()}")
            warnings, severe = self._err_counts()
            self.info.update(warnings=warnings, severe_errors=severe, exit_code=rc)
            if severe:
                logger.warning("%s: EnergyPlus reported %d severe errors (see %s)", self.name, severe, self.err_file)
        finally:
            ep.close()
            self._endpoint = None

    def abort(self) -> None:
        if self._endpoint is not None:
            try:
                self._endpoint.kill()
            finally:
                self._endpoint = None

    # --------------------------------------------------------------- results
    @property
    def err_file(self) -> Optional[Path]:
        if self.ctx is None:
            return None
        return self.ctx.output_dir / "eplus" / "eplusout.err"

    def _err_tail(self, n: int = 2500) -> str:
        path = self.err_file
        text = ""
        if path is not None and path.exists():
            text = path.read_text(errors="replace")[-n:]
        if self._endpoint is not None:
            log = self._endpoint.log_tail()
            if log.strip():
                text += "\n--- worker log ---\n" + log
        return text

    def _err_counts(self) -> Tuple[int, int]:
        path = self.err_file
        if path is None or not path.exists():
            return 0, 0
        import re

        text = path.read_text(errors="replace")
        m = re.search(r"Completed Successfully-- (\d+) Warning; (\d+) Severe Errors", text)
        return (int(m.group(1)), int(m.group(2))) if m else (text.count("** Warning **"), text.count("** Severe  **"))

    def run_metadata(self) -> Dict[str, Any]:
        meta = dict(self.info)
        meta.update(
            executor=getattr(self, "_executor_used", self.executor),
            model=str(self.model_source if not isinstance(self.model_source, EnergyPlusModel) else self.model_source.source),
            weather=str(self.weather),
            worker_wall_s=self._worker_wall,
        )
        return meta


def _explain_missing(name: str, missing, available_csv: str) -> str:
    import difflib

    lines = [f"{name}: EnergyPlus could not find the following handles:"]
    candidates = [l.strip() for l in (available_csv or "").splitlines() if l.strip() and not l.startswith("#")]
    for kind, port, spec in missing:
        lines.append(f"  - {kind} '{port}': {tuple(spec)}")
        needle = ",".join(spec).lower()
        pool = [c for c in candidates if c.lower().startswith({"sensor": "outputvariable", "meter": "outputmeter",
                                                                "actuator": "actuator",
                                                                "internal_variable": "internalvariable"}[kind])]
        close = difflib.get_close_matches(needle, [p.lower() for p in pool], n=3, cutoff=0.4)
        if close:
            originals = [pool[[p.lower() for p in pool].index(c)] for c in close]
            lines.append("      did you mean: " + " | ".join(originals))
    lines.append(
        "Variable/key names are case-insensitive but must otherwise match EnergyPlus exactly. "
        "List everything available with:  pycosim handles <model> -w <weather.epw>"
    )
    return "\n".join(lines)
