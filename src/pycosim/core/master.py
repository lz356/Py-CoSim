"""The co-simulation master algorithm (:class:`CoSimulation`).

Time advances on a fixed master grid ``t_k = start + k * dt``. Components may
run at any integer multiple of ``dt`` (multi-rate); between their updates
their outputs are held constant (zero-order hold). Within a step the master
executes three phases (see :mod:`pycosim.core.graph`):

1. *output*  -- non-feedthrough components report their state at ``t_k``;
2. *step*    -- feedthrough components run in topological order;
3. *update*  -- non-feedthrough components advance to ``t_{k+1}``.

Phase 3 of all EnergyPlus components runs concurrently (each EnergyPlus
instance lives in its own worker process), which is where the parallel
speed-up of multi-building co-simulations comes from.
"""
from __future__ import annotations

import logging
import math
import platform
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Sequence, Tuple, Union

import pandas as pd

from .._version import __version__
from .bus import DataBus
from .component import Component, Inputs, PortRef, coerce_outputs, iter_refs, normalize_step_size
from .context import Context
from .errors import ComponentError, ConfigurationError, PyCoSimError
from .graph import Connection, build_execution_order, resolve_reduce
from .observers import Observer, ProgressBar
from .results import Results
from .timeutils import DatetimeLike, DurationLike, format_duration, parse_datetime, parse_duration

__all__ = ["CoSimulation"]

logger = logging.getLogger("pycosim")
_MISSING = object()


class CoSimulation:
    """Orchestrates a set of connected components on a common clock.

    Parameters
    ----------
    start, end : str or datetime, optional
        Simulation window, ``end`` exclusive (``"2023-07-01"``,
        ``"2023-07-08"`` simulates seven full days). When omitted they are
        taken from the first EnergyPlus component's RunPeriod.
    step : str, number or timedelta, optional
        Master step (``"15min"``, ``900``). Defaults to the EnergyPlus time
        step when an EnergyPlus component is present.
    name : str
        Name of the run; results are written to ``output_dir/name``.
    output_dir : str or Path
        Root folder for all files written by the run.
    seed : int
        Seed for the per-component random generators (``ctx.rng``).
    save : bool
        Write results (CSV + JSON) to the output folder at the end.
    progress : bool
        Show a text progress bar.
    log_level : str or int, optional
        Level of the ``pycosim`` logger (default ``"INFO"``).
    """

    def __init__(
        self,
        start: Optional[DatetimeLike] = None,
        end: Optional[DatetimeLike] = None,
        step: Optional[DurationLike] = None,
        *,
        name: str = "cosim",
        output_dir: Union[str, Path] = "pycosim_runs",
        seed: Optional[int] = 0,
        save: bool = True,
        progress: bool = False,
        log_level: Union[str, int, None] = "INFO",
    ) -> None:
        self.name = name
        self.output_dir = Path(output_dir)
        if seed is None:  # draw a seed but record it, so the run can be reproduced afterwards
            import numpy as np

            seed = int(np.random.SeedSequence().entropy % (2**32))
        self.seed = seed
        self.save = save
        self.progress = progress
        self._start_arg = start
        self._end_arg = end
        self._step_arg = step
        self.start: Optional[datetime] = None
        self.end: Optional[datetime] = None
        self.dt: Optional[float] = None
        self.n_steps = 0
        self.times: List[datetime] = []
        self._components: Dict[str, Component] = {}
        self._connections: List[Connection] = []
        self._observers: List[Observer] = []
        self.bus: Optional[DataBus] = None
        self.k = -1
        self.status = "created"
        self._timing: Dict[str, List[float]] = {}
        self._failed_inputs: Optional[Dict[str, Any]] = None
        self._completed = 0
        self.partial_results: Optional[Results] = None
        _configure_logging(log_level)

    # ================================================================ building
    def add(self, *components: Component):
        """Add one or more components. Returns the component (or a tuple)."""
        for comp in components:
            if not isinstance(comp, Component):
                raise ConfigurationError(
                    f"{comp!r} is not a Component. Wrap functions with @pycosim.component "
                    "or subclass pycosim.Component."
                )
            if self.status != "created":
                raise ConfigurationError("Components must be added before the simulation is initialized")
            comp._ensure_ports()
            if comp.name in self._components:
                raise ConfigurationError(f"A component named {comp.name!r} already exists; names must be unique")
            self._components[comp.name] = comp
        return components[0] if len(components) == 1 else components

    def __getitem__(self, name: str) -> Component:
        try:
            return self._components[name]
        except KeyError:
            raise KeyError(f"No component named {name!r}. Known: {', '.join(self._components)}") from None

    def __contains__(self, name: str) -> bool:
        return name in self._components

    @property
    def components(self) -> List[Component]:
        return list(self._components.values())

    @property
    def connections(self) -> List[Connection]:
        return list(self._connections)

    def connect(
        self,
        source: Any,
        target: Any,
        *,
        delay: bool = False,
        transform=None,
        reduce=None,
    ) -> Connection:
        """Connect output port(s) to an input port.

        Parameters
        ----------
        source : str, PortRef or list of them
            ``"weather.T_out"``, ``component["T_out"]`` or a list for fan-in.
        target : str or PortRef
            Input port, e.g. ``"office.T_out"``.
        delay : bool
            Read the source value of the previous step (breaks algebraic loops).
        transform : callable, optional
            Applied to the value (unit conversions, scaling, ...).
        reduce : {"sum", "mean", "max", "min", "list"} or callable
            Required when several sources feed one input.
        """
        if self.status != "created":
            raise ConfigurationError("Connections must be made before the simulation is initialized")
        sources = iter_refs(source)
        tgt = PortRef.parse(target)
        if len(sources) > 1 and reduce is None:
            raise ConfigurationError(
                f"{len(sources)} sources connected to {tgt}: pass reduce='sum' (or 'mean', 'max', 'min', 'list', a callable)"
            )
        if any(str(c.target) == str(tgt) for c in self._connections):
            raise ConfigurationError(
                f"Input {tgt} is already connected. Connect several sources at once with a list and reduce=..."
            )
        conn = Connection(sources, tgt, bool(delay), transform, resolve_reduce(reduce))
        self._connections.append(conn)
        return conn

    def connect_matching(self, source: Union[str, Component], target: Union[str, Component],
                         ports: Optional[Iterable[str]] = None, delay: bool = False) -> List[Connection]:
        """Connect every output of ``source`` to the same-named input of ``target``."""
        src = self[source] if isinstance(source, str) else source
        dst = self[target] if isinstance(target, str) else target
        names = list(ports) if ports is not None else [p for p in src.output_ports if p in dst.input_ports]
        if not names:
            raise ConfigurationError(f"No matching port names between {src.name} outputs and {dst.name} inputs")
        return [self.connect(f"{src.name}.{n}", f"{dst.name}.{n}", delay=delay) for n in names]

    def add_observer(self, observer: Observer) -> Observer:
        if not isinstance(observer, Observer):
            raise ConfigurationError("observers must derive from pycosim.observers.Observer")
        self._observers.append(observer)
        return observer

    # ================================================================== status
    @property
    def now(self) -> datetime:
        """Current master time (``start`` before the first step)."""
        if self.k < 0 or not self.times:
            return self.start if self.start is not None else datetime.min
        return self.times[min(self.k, len(self.times) - 1)]

    @property
    def done(self) -> bool:
        return self.k + 1 >= self.n_steps

    @property
    def output_path(self) -> Path:
        return self.output_dir / self.name

    def describe(self) -> str:
        """Text overview of components, connections and execution order."""
        lines = [f"CoSimulation '{self.name}'"]
        if self.start is not None:
            lines.append(f"  window: {self.start} -> {self.end}, step {format_duration(self.dt)} ({self.n_steps} steps)")
        lines.append("  components:")
        for comp in self._components.values():
            lines.extend("    " + l for l in comp.describe().splitlines())
        lines.append("  connections:")
        for conn in self._connections:
            lines.append(f"    {conn}")
        try:
            order = build_execution_order(self.components, self._connections)
            moore = [c.name for c in self.components if not c.direct_feedthrough]
            lines.append(f"  execution: output[{', '.join(moore)}] -> step[{', '.join(c.name for c in order)}] "
                         f"-> update[{', '.join(moore)}]")
        except PyCoSimError as exc:
            lines.append(f"  execution order: INVALID ({exc})")
        return "\n".join(lines)

    # ============================================================ initialization
    def _resolve_clock(self) -> None:
        start = parse_datetime(self._start_arg) if self._start_arg is not None else None
        end = parse_datetime(self._end_arg) if self._end_arg is not None else None
        dt = parse_duration(self._step_arg) if self._step_arg is not None else None
        if start is None or end is None or dt is None:
            for comp in self._components.values():
                suggest = getattr(comp, "suggest_clock", None)
                if suggest is None:
                    continue
                hint = suggest() or {}
                start = start if start is not None else hint.get("start")
                end = end if end is not None else hint.get("end")
                if dt is None and hint.get("step") is not None:
                    dt = parse_duration(hint["step"])
                if start is not None and end is not None and dt is not None:
                    break
        if start is None or end is None:
            raise ConfigurationError("Simulation start/end unknown: pass start=... and end=... to CoSimulation")
        if dt is None:
            raise ConfigurationError("Master step unknown: pass step=... (e.g. '15min') to CoSimulation")
        if end <= start:
            raise ConfigurationError(f"end ({end}) must be after start ({start})")
        total = (end - start).total_seconds()
        n = total / dt
        n_steps = int(math.floor(n + 1e-9))
        if n_steps < 1:
            raise ConfigurationError("The simulation window is shorter than one master step")
        if abs(n - n_steps) > 1e-9:
            logger.warning("Window %s -> %s is not a multiple of the step; truncating to %d steps", start, end, n_steps)
            end = start + timedelta(seconds=n_steps * dt)
        self.start, self.end, self.dt, self.n_steps = start, end, dt, n_steps
        self.times = [start + timedelta(seconds=k * dt) for k in range(n_steps)]

    def _validate_connections(self) -> None:
        comps = self._components
        for conn in self._connections:
            tgt = conn.target
            if tgt.component not in comps:
                raise ConfigurationError(f"Connection {conn}: unknown component {tgt.component!r}")
            if tgt.port not in comps[tgt.component].input_ports:
                raise ConfigurationError(
                    f"Connection {conn}: {tgt.component!r} has no input {tgt.port!r}. "
                    f"Inputs: {list(comps[tgt.component].input_ports)}"
                )
            for src in conn.sources:
                if src.component not in comps:
                    raise ConfigurationError(f"Connection {conn}: unknown component {src.component!r}")
                if src.port not in comps[src.component].output_ports:
                    raise ConfigurationError(
                        f"Connection {conn}: {src.component!r} has no output {src.port!r}. "
                        f"Outputs: {list(comps[src.component].output_ports)}"
                    )

    def initialize(self) -> None:
        """Validate the set-up, resolve the clock and call ``setup`` on all components."""
        if self.status != "created":
            raise ConfigurationError(f"Cannot initialize a simulation in state {self.status!r}")
        if not self._components:
            raise ConfigurationError("No components were added")
        self._resolve_clock()
        self._validate_connections()
        comps = self.components
        self._order = build_execution_order(comps, self._connections)
        self._moore = [c for c in comps if not c.direct_feedthrough]
        self._multiple = {c.name: normalize_step_size(c, self.dt) for c in comps}
        self.bus = DataBus(self.times)
        self._out_refs: Dict[str, Dict[str, str]] = {}
        for comp in comps:
            refs = {}
            for port in comp.output_ports.values():
                ref = f"{comp.name}.{port.name}"
                self.bus.declare(ref, port.default, port.unit)
                refs[port.name] = ref
            self._out_refs[comp.name] = refs
        self.bus.track_previous(str(s) for c in self._connections if c.delay for s in c.sources)
        # input plans: (port, sources, delay, reduce, transform, default)
        by_target = {str(c.target): c for c in self._connections}
        self._plans: Dict[str, List[Tuple]] = {}
        for comp in comps:
            plan = []
            for port in comp.input_ports.values():
                conn = by_target.get(f"{comp.name}.{port.name}")
                if conn is None:
                    plan.append((port.name, None, False, None, None, port.default))
                else:
                    plan.append((port.name, [str(s) for s in conn.sources], conn.delay, conn.reduce,
                                 conn.transform, port.default))
            self._plans[comp.name] = plan
        self._timing = {c.name: [0, 0.0] for c in comps}
        if self.save:
            self.output_path.mkdir(parents=True, exist_ok=True)
        if self.progress and not any(isinstance(o, ProgressBar) for o in self._observers):
            self._observers.append(ProgressBar())
        logger.info("Initializing '%s': %d components, %d connections, %s -> %s, step %s (%d steps)",
                    self.name, len(comps), len(self._connections), self.start, self.end,
                    format_duration(self.dt), self.n_steps)
        self._wall_start = time.perf_counter()
        set_up: List[Component] = []
        try:
            for comp in comps:
                comp.ctx = Context(self, comp.name, self._multiple[comp.name], self.seed)
                t0 = time.perf_counter()
                try:
                    comp.setup(comp.ctx)
                except Exception as exc:
                    raise ComponentError(comp.name, "setup", None, exc) from exc
                set_up.append(comp)
                self._timing[comp.name][1] += time.perf_counter() - t0
                initial = getattr(comp, "initial_outputs", None)
                if callable(initial):
                    values = coerce_outputs(comp, initial())
                    for port, value in (values or {}).items():
                        self.bus.set_initial(self._out_refs[comp.name][port], value)
            for comp in comps:  # wait for slow start-ups (EnergyPlus sizing/warm-up) that run in parallel
                t0 = time.perf_counter()
                try:
                    comp.ready()
                except Exception as exc:
                    raise ComponentError(comp.name, "ready", None, exc) from exc
                self._timing[comp.name][1] += time.perf_counter() - t0
        except BaseException as exc:
            for comp in reversed(set_up):
                _safe(comp.abort)
            self.status = "failed"
            self._write_error_report(exc)
            raise
        self.status = "running"
        self._setup_time = time.perf_counter() - self._wall_start
        for obs in self._observers:
            obs.on_start(self)

    # ================================================================ stepping
    def _gather(self, comp: Component) -> Inputs:
        latest = self.bus._latest
        previous = self.bus._snapshot
        values = Inputs()
        for name, sources, delay, reduce, transform, default in self._plans[comp.name]:
            if sources is None:
                values[name] = default
                continue
            store = previous if delay else latest
            if len(sources) == 1:
                value = store.get(sources[0], _MISSING)
                if value is _MISSING:
                    value = default
            else:
                collected = [store.get(s) for s in sources]
                value = default if all(v is None for v in collected) else reduce(collected)
            if transform is not None and value is not None:
                value = transform(value)
            values[name] = value
        return values

    def _publish(self, comp: Component, result: Any, phase: str = "step", t: Optional[datetime] = None) -> None:
        try:
            values = coerce_outputs(comp, result)
        except PyCoSimError as exc:
            raise ComponentError(comp.name, phase, t, exc) from exc
        if values:
            refs = self._out_refs[comp.name]
            publish = self.bus.publish
            for port, value in values.items():
                publish(refs[port], value)

    def _call(self, comp: Component, phase: str, t: datetime, fn, *args):
        timing = self._timing[comp.name]
        t0 = time.perf_counter()
        try:
            return fn(*args)
        except PyCoSimError as exc:
            if isinstance(exc, ComponentError):
                raise
            raise ComponentError(comp.name, phase, t, exc) from exc
        except Exception as exc:
            raise ComponentError(comp.name, phase, t, exc) from exc
        finally:
            timing[0] += 1
            timing[1] += time.perf_counter() - t0

    def _step_gen(self) -> Generator[Tuple[str, Component, Inputs], Any, None]:
        """One master step as a generator.

        It yields ``("agent", component, inputs)`` right before an external
        agent (see :mod:`pycosim.gym`) acts, so that a reinforcement-learning
        loop can inject the action. Ordinary use simply exhausts it.
        """
        if self.status == "created":
            self.initialize()
        if self.status != "running":
            raise PyCoSimError(f"Cannot step a simulation in state {self.status!r}")
        if self.done:
            raise PyCoSimError("The simulation already reached its end time")
        k = self.k + 1
        self.k = k
        t = self.times[k]
        self.bus.begin_step(k)
        mult = self._multiple
        due_moore = [c for c in self._moore if k % mult[c.name] == 0]
        # Phase 1: state-based components report their outputs at t.
        for comp in due_moore:
            self._publish(comp, self._call(comp, "output", t, comp.output, t), "output", t)
        # Phase 2: feedthrough components in topological order.
        for comp in self._order:
            if k % mult[comp.name]:
                continue
            inputs = self._gather(comp)
            self._failed_inputs = inputs
            if getattr(comp, "_pc_external_agent", False):
                action = yield ("agent", comp, inputs)
                if action is not None:
                    result = self._call(comp, "act", t, comp.apply_action, t, inputs, action)
                else:
                    result = self._call(comp, "step", t, comp.step, t, inputs)
            else:
                result = self._call(comp, "step", t, comp.step, t, inputs)
            self._publish(comp, result, "step", t)
        # Phase 3: state-based components advance to t + dt (EnergyPlus runs in parallel here).
        for comp in due_moore:
            inputs = self._gather(comp)
            self._failed_inputs = inputs
            self._call(comp, "update", t, comp.update, t, inputs)
        self._failed_inputs = None
        self._completed = k + 1
        for obs in self._observers:
            obs.on_step(self, k)

    def step(self) -> bool:
        """Advance one master step. Returns ``False`` once the end is reached."""
        if self.status == "running" and self.done:
            raise PyCoSimError("The simulation already reached its end time; call finalize()")
        try:
            for _ in self._step_gen():
                pass
        except BaseException as exc:
            self._fail(exc)
            raise
        return not self.done

    def run(self, progress: Optional[bool] = None) -> Results:
        """Run the whole co-simulation and return :class:`Results`."""
        if progress is not None:
            self.progress = progress
        if self.status == "created":
            self.initialize()
        try:
            while not self.done:
                for _ in self._step_gen():
                    pass
        except BaseException as exc:
            self._fail(exc)
            raise
        return self.finalize()

    # ================================================================ shutdown
    def finalize(self) -> Results:
        """Finalize components, notify observers and assemble the results."""
        if self.status != "running":
            raise PyCoSimError(f"Cannot finalize a simulation in state {self.status!r}")
        errors = []
        for comp in self.components:
            t0 = time.perf_counter()
            try:
                comp.finalize()
            except Exception as exc:  # keep finalizing the others
                errors.append(ComponentError(comp.name, "finalize", None, exc))
            self._timing[comp.name][1] += time.perf_counter() - t0
        self.status = "finished"
        results = self._build_results(error=None)
        if self.save:
            results.save(self.output_path)
        for obs in self._observers:
            _safe(obs.on_end, self, results)
        logger.info("Finished '%s' in %.2f s (%d steps)", self.name, results.wall_time, self.k + 1)
        if errors:
            raise errors[0]
        return results

    def _fail(self, exc: BaseException) -> None:
        if self.status in ("failed", "finished"):
            return
        self.status = "failed"
        for comp in self.components:
            _safe(comp.abort)
        report = self._write_error_report(exc)
        try:
            results = self._build_results(error=report)
            self.partial_results = results
            if self.save:
                results.save(self.output_path)
        except Exception:  # pragma: no cover - never mask the original error
            results = None
        for obs in self._observers:
            _safe(obs.on_end, self, results)

    def _write_error_report(self, exc: BaseException) -> str:
        lines = [
            "Py-CoSim error report",
            "=====================",
            f"run        : {self.name}",
            f"time (wall): {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"sim time   : {self.now if self.k >= 0 else 'before first step'} (step {self.k})",
        ]
        if isinstance(exc, ComponentError):
            lines += [f"component  : {exc.component}", f"phase      : {exc.phase}"]
        if self._failed_inputs is not None:
            lines.append("inputs of the failing component at that step:")
            lines += [f"    {k} = {v!r}" for k, v in self._failed_inputs.items()]
        lines += ["", "traceback:", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))]
        if self.bus is not None and self.bus.events:
            lines.append("last events:")
            lines += [f"    {e['time']} [{e['source']}] {e['kind']}: {e['message']}" for e in self.bus.events[-20:]]
        lines.append("connections:")
        lines += [f"    {c}" for c in self._connections]
        text = "\n".join(lines)
        try:
            self.output_path.mkdir(parents=True, exist_ok=True)
            (self.output_path / "error_report.txt").write_text(text, encoding="utf-8")
            logger.error("Co-simulation '%s' failed: %s (report: %s)", self.name, exc,
                         self.output_path / "error_report.txt")
        except Exception:  # pragma: no cover
            pass
        return text

    def _build_results(self, error: Optional[str]) -> Results:
        df = self.bus.to_frame() if self.bus is not None else pd.DataFrame()
        events = pd.DataFrame(self.bus.events if self.bus is not None else [],
                              columns=None if (self.bus and self.bus.events) else ["time", "source", "kind", "message"])
        timing = pd.DataFrame(
            [
                {"component": n, "calls": int(c), "total_s": s, "mean_ms": (1000 * s / c) if c else 0.0}
                for n, (c, s) in self._timing.items()
            ]
        )
        wall = time.perf_counter() - getattr(self, "_wall_start", time.perf_counter())
        meta = {
            "name": self.name,
            "start": str(self.start),
            "end": str(self.end),
            "step": format_duration(self.dt) if self.dt else None,
            "step_s": self.dt,
            "n_steps": self.n_steps,
            "completed_steps": self._completed,
            "seed": self.seed,
            "wall_time_s": wall,
            "setup_time_s": getattr(self, "_setup_time", None),
            "components": {c.name: type(c).__name__ for c in self.components},
            "connections": [str(c) for c in self._connections],
            "pycosim_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        }
        for comp in self.components:
            extra = getattr(comp, "run_metadata", None)
            if callable(extra):
                try:
                    meta.setdefault("component_metadata", {})[comp.name] = extra()
                except Exception:  # pragma: no cover
                    pass
        units = dict(self.bus.units) if self.bus is not None else {}
        return Results(df, events, timing, meta, error, units)


def _safe(fn, *args) -> None:
    try:
        fn(*args)
    except Exception as exc:  # pragma: no cover - best effort clean-up
        logger.debug("Ignored error during clean-up: %s", exc)


def _configure_logging(level: Union[str, int, None]) -> None:
    if level is None:
        return
    pkg_logger = logging.getLogger("pycosim")
    if not pkg_logger.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[pycosim] %(levelname)s %(message)s"))
        pkg_logger.addHandler(handler)
    pkg_logger.setLevel(level if isinstance(level, int) else str(level).upper())
