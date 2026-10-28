"""The co-simulation master -- the management half of SMMM (:class:`CoSimulation`).

Time advances on a fixed master grid ``t_k = start + k * dt``. Components may
run at any integer multiple of ``dt`` (multi-rate). Components never call each
other: they publish *messages* to the data bus (the messaging half of SMMM,
:mod:`pycosim.core.bus`) and read what they subscribed to. Within a step the
master executes

0. *deliver* -- answers of asynchronous components whose decision delay ends
   at ``t_k`` are published;
1. *output*  -- non-feedthrough components report their state at ``t_k``
   (replayed components publish their recorded messages here);
2. *step*    -- feedthrough components run in topological order (asynchronous
   ones are handed to a background thread);
3. *update*  -- non-feedthrough components advance to ``t_{k+1}``.

Phase 3 of all EnergyPlus components runs concurrently (each EnergyPlus
instance lives in its own worker process), which is where the parallel
speed-up of multi-building co-simulations comes from.

Inputs are aligned by SMMM according to the time meaning of the source
(``kind`` of the output port) and what the input asks for (``read``), see
:mod:`pycosim.core.messages`.
"""
from __future__ import annotations

import concurrent.futures as cf
import fnmatch
import logging
import math
import platform
import sys
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Generator, Iterable, List, Optional, Sequence, Tuple, Union

import pandas as pd

from .._version import __version__
from .bus import DataBus
from .component import Component, Inputs, PortRef, coerce_outputs, iter_refs, normalize_step_size
from .context import _ASYNC, Context
from .errors import ComponentError, ConfigurationError, PyCoSimError, SynchronizationError
from .graph import Connection, build_execution_order, resolve_reduce
from .messages import MISSING, QUALITY, Message, Signal, _num
from .observers import Observer, ProgressBar
from .results import Results
from .timeutils import DatetimeLike, DurationLike, format_duration, parse_datetime, parse_duration

__all__ = ["CoSimulation"]

logger = logging.getLogger("pycosim")
_MISSING = object()
_GLOB = set("*?[")


class _AsyncState:
    __slots__ = ("delay_steps", "deadline", "policy", "pool", "pending", "late", "delivered")

    def __init__(self, delay_steps: int, deadline: Optional[float], policy: str):
        self.delay_steps = delay_steps
        self.deadline = deadline
        self.policy = policy
        self.pool: Optional[cf.ThreadPoolExecutor] = None
        self.pending: Deque[Tuple[int, cf.Future, float, int]] = deque()
        self.late = 0
        self.delivered = 0


class CoSimulation:
    """Orchestrates a set of components that exchange messages on a common clock.

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
    record : bool or list of str, optional
        Keep every message published by these components (``True`` = all) in
        ``Results.messages`` and ``messages.jsonl``, so that they can be
        replayed later with :meth:`replay`.
    trace : bool
        Record which messages every component read at every step (provenance);
        see :meth:`Results.lineage`. Implies ``record=True``.
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
        record: Union[bool, Iterable[str], None] = None,
        trace: bool = False,
    ) -> None:
        self.name = name
        self.output_dir = Path(output_dir)
        if seed is None:  # draw a seed but record it, so the run can be reproduced afterwards
            import numpy as np

            seed = int(np.random.SeedSequence().entropy % (2**32))
        self.seed = seed
        self.save = save
        self.progress = progress
        self.trace = bool(trace)
        if record is True or self.trace:
            self.record: Union[bool, List[str]] = True
        elif record:
            self.record = [record] if isinstance(record, str) else list(record)
        else:
            self.record = False
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
        self._replays: Dict[str, Any] = {}
        self.bus: Optional[DataBus] = None
        self.k = -1
        self.status = "created"
        self._timing: Dict[str, List[float]] = {}
        self._failed_inputs: Optional[Dict[str, Any]] = None
        self._completed = 0
        self._async: Dict[str, _AsyncState] = {}
        self._trace_rows: List[Tuple] = []
        self._align: Dict[Tuple[str, str], List[Any]] = {}
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
        read: Optional[str] = None,
    ) -> Connection:
        """Subscribe an input port to one or more outputs.

        Parameters
        ----------
        source : str, PortRef or list of them
            ``"weather.T_out"``, ``component["T_out"]``, a topic (``"grid/price"``),
            a glob pattern (``"*.P_elec"`` -- every component's ``P_elec``) or a
            list of these for fan-in. Patterns and topics are resolved when the
            simulation is initialized, so components added later still match.
        target : str or PortRef
            Input port, e.g. ``"office.T_out"``.
        delay : bool
            Read the source value of the previous step (breaks algebraic loops).
        transform : callable, optional
            Applied to the value (unit conversions, scaling, ...).
        reduce : {"sum", "mean", "max", "min", "list"} or callable
            Required when several sources feed one input.
        read : {"latest", "at", "past", "ahead"}, optional
            How the target reads the sources (default: the input port's
            ``read``, else ``"latest"``). See :mod:`pycosim.core.messages`.
        """
        if self.status != "created":
            raise ConfigurationError("Connections must be made before the simulation is initialized")
        from .messages import READS

        if read is not None and read not in READS:
            raise ConfigurationError(f"read must be one of {READS} (got {read!r})")
        sources, patterns = _split_sources(source)
        tgt = PortRef.parse(target)
        if len(sources) + len(patterns) > 1 and reduce is None:
            raise ConfigurationError(
                f"{len(sources) + len(patterns)} sources connected to {tgt}: pass reduce='sum' "
                "(or 'mean', 'max', 'min', 'list', a callable)"
            )
        if any(str(c.target) == str(tgt) for c in self._connections):
            raise ConfigurationError(
                f"Input {tgt} is already connected. Connect several sources at once with a list and reduce=..."
            )
        conn = Connection(sources, tgt, bool(delay), transform, resolve_reduce(reduce), read=read,
                          patterns=patterns)
        self._connections.append(conn)
        return conn

    def subscribe(self, target: Any, source: Any, **kwargs: Any) -> Connection:
        """Same as :meth:`connect` with the arguments in reading order: ``subscribe(input, outputs)``."""
        return self.connect(source, target, **kwargs)

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

    def replay(self, name: str, source: Any) -> Component:
        """Replace component ``name`` by the messages it published in an earlier, recorded run.

        ``source`` is a run folder (containing ``messages.jsonl``), that file,
        or a :class:`Results` object of a run made with ``record=[name, ...]``.
        The replacement is applied when the simulation is initialized.
        """
        if self.status != "created":
            raise ConfigurationError("replay() must be called before the simulation is initialized")
        if name not in self._components:
            raise KeyError(f"No component named {name!r}")
        from .replay import load_messages, load_recording_meta

        self._replays[name] = (load_messages(source, name), load_recording_meta(source))
        return self._components[name]

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
            order = build_execution_order(self.components, self._connections, self._needs_now_or_none())
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

    def _output_catalogue(self) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """All output refs (``comp.port`` -> Port) and topic aliases (topic -> ref)."""
        refs: Dict[str, Any] = {}
        topics: Dict[str, str] = {}
        for comp in self._components.values():
            for port in comp.output_ports.values():
                ref = f"{comp.name}.{port.name}"
                refs[ref] = port
                if port.topic:
                    if port.topic in topics and topics[port.topic] != ref:
                        raise ConfigurationError(
                            f"Topic {port.topic!r} is published by both {topics[port.topic]} and {ref}")
                    topics[port.topic] = ref
        return refs, topics

    def _resolve_patterns(self) -> None:
        refs, topics = self._output_catalogue()
        for conn in self._connections:
            resolved: List[PortRef] = []
            for src in conn.sources:  # exact "comp.port" names; fall back to topics with dots
                name = str(src)
                if name not in refs and name in topics:
                    resolved.append(PortRef.parse(topics[name]))
                else:
                    resolved.append(src)
            for pattern in conn.patterns:
                if pattern in refs:
                    hits = [pattern]
                elif pattern in topics:
                    hits = [topics[pattern]]
                else:
                    own = conn.target.component + "."
                    hits = [r for r in refs if fnmatch.fnmatchcase(r, pattern) and not r.startswith(own)]
                    hits += [r for tp, r in topics.items() if fnmatch.fnmatchcase(tp, pattern)
                             and r not in hits and not r.startswith(own)]
                if not hits:
                    raise ConfigurationError(
                        f"Connection to {conn.target}: {pattern!r} matches no output port or topic")
                resolved.extend(PortRef.parse(h) for h in hits)
            seen, unique = set(), []
            for r in resolved:
                if str(r) not in seen:
                    seen.add(str(r))
                    unique.append(r)
            if len(unique) > 1 and conn.reduce is None:
                raise ConfigurationError(
                    f"{conn.patterns or [str(s) for s in conn.sources]} resolve to {len(unique)} outputs "
                    f"({', '.join(map(str, unique))}) connected to {conn.target}: pass reduce='sum' (or another reducer)")
            conn.sources = unique

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
            read = self._read_mode(conn)
            if conn.delay and read != "latest":
                raise ConfigurationError(
                    f"Connection {conn}: delay=True only applies to read='latest' (a 'past' read already "
                    "uses the previous step and never creates an algebraic loop)")

    def _read_mode(self, conn: Connection) -> str:
        if conn.read:
            return conn.read
        comp = self._components.get(conn.target.component)
        port = comp.input_ports.get(conn.target.port) if comp is not None else None
        return (port.read if port is not None and port.read else "latest")

    def _source_port(self, ref: PortRef):
        comp = self._components.get(ref.component)
        return comp.output_ports.get(ref.port) if comp is not None else None

    def _needs_now(self, conn: Connection, src: PortRef) -> bool:
        """Does this link need the value the source publishes in the *current* step?"""
        read = self._read_mode(conn)
        if read != "past":
            return True
        port = self._source_port(src)
        if port is None:
            return True
        if port.kind == "instant":
            return True  # the mean over [t-h, t) interpolates up to the sample at t
        if port.kind in ("average", "amount"):
            return (port.interval or "past") == "past"
        return False  # hold / event / untyped values published at t only apply from t on

    def _needs_now_or_none(self):
        try:
            return self._needs_now
        except Exception:  # pragma: no cover
            return None

    def _apply_replays(self) -> None:
        if not self._replays:
            return
        from .replay import ReplayComponent

        for name, (records, meta) in self._replays.items():
            rec_step, rec_start = meta.get("step_s"), meta.get("start")
            if rec_step is not None and abs(float(rec_step) - self.dt) > 1e-9:
                raise ConfigurationError(
                    f"replay of {name!r}: the recording used a master step of {float(rec_step):g}s, this "
                    f"simulation uses {self.dt:g}s; replay needs the same step")
            if rec_start is not None and str(rec_start) != str(self.start):
                raise ConfigurationError(
                    f"replay of {name!r}: the recording started at {rec_start}, this simulation at {self.start}; "
                    "replay needs the same start")
            original = self._components[name]
            self._components[name] = ReplayComponent(original, records)
            logger.info("Replaying '%s' (%s) from %d recorded messages", name, type(original).__name__,
                        self._components[name].n_messages)

    def initialize(self) -> None:
        """Validate the set-up, resolve the clock and call ``setup`` on all components."""
        if self.status != "created":
            raise ConfigurationError(f"Cannot initialize a simulation in state {self.status!r}")
        if not self._components:
            raise ConfigurationError("No components were added")
        self._resolve_clock()
        self._apply_replays()
        self._resolve_patterns()
        self._validate_connections()
        comps = self.components
        self._order = build_execution_order(comps, self._connections, self._needs_now)
        self._moore = [c for c in comps if not c.direct_feedthrough]
        self._multiple = {c.name: normalize_step_size(c, self.dt) for c in comps}
        self._setup_async(comps)
        self.bus = DataBus(self.times, self.dt)
        self._out_refs: Dict[str, Dict[str, str]] = {}
        self._validators: Dict[str, Dict[str, Callable[[Any], Tuple[Any, int]]]] = {}
        for comp in comps:
            refs = {}
            step_s = self.dt * self._multiple[comp.name]
            for port in comp.output_ports.values():
                ref = f"{comp.name}.{port.name}"
                self.bus.declare(ref, port.default, port.unit, port.kind, port.interval, step_s, port.topic)
                refs[port.name] = ref
                if port.bounds is not None:
                    self._validators.setdefault(comp.name, {})[port.name] = _make_validator(comp.name, port)
            self._out_refs[comp.name] = refs
        self.bus.track_previous(str(s) for c in self._connections if c.delay for s in c.sources)
        # input plans: (port, sources, delay, reduce, transform, default, read, signals)
        by_target = {str(c.target): c for c in self._connections}
        self._plans: Dict[str, List[Tuple]] = {}
        self._align = {}
        for comp in comps:
            plan = []
            for port in comp.input_ports.values():
                conn = by_target.get(f"{comp.name}.{port.name}")
                if conn is None:
                    plan.append((port.name, None, False, None, None, port.default, "latest", None))
                    continue
                read = self._read_mode(conn)
                srcs = [str(s) for s in conn.sources]
                sigs = [self.bus.signals[s] for s in srcs]
                plan.append((port.name, srcs, conn.delay, conn.reduce, conn.transform, port.default, read, sigs))
                if read != "latest":
                    self._align[(comp.name, port.name)] = [read, srcs, 0, 0, 1.0]
            self._plans[comp.name] = plan
        self._timing = {c.name: [0, 0.0] for c in comps}
        self._trace_rows = []
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
                    _safe(comp.abort)  # release whatever a half-finished setup started (processes, files)
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
            self.status = "running"
            self._setup_time = time.perf_counter() - self._wall_start
            for obs in self._observers:
                obs.on_start(self)
        except BaseException as exc:
            for comp in reversed(set_up):
                _safe(comp.abort)
            self._shutdown_async()
            self.status = "failed"
            self._write_error_report(exc)
            raise

    def _setup_async(self, comps: Sequence[Component]) -> None:
        self._async = {}
        for comp in comps:
            if not getattr(comp, "asynchronous", False):
                continue
            if not comp.direct_feedthrough:
                raise ConfigurationError(f"{comp.name}: only feedthrough components (step()) can be asynchronous")
            if getattr(comp, "_pc_external_agent", False):
                raise ConfigurationError(f"{comp.name}: an ExternalAgent cannot be asynchronous")
            delay = comp.decision_delay
            delay_s = self.dt if delay is None else parse_duration(delay)
            ratio = delay_s / self.dt
            steps = int(round(ratio))
            if steps < 1 or abs(ratio - steps) > 1e-9:
                raise ConfigurationError(
                    f"{comp.name}: decision delay {delay_s:g}s must be a positive multiple of the master step "
                    f"{self.dt:g}s")
            self._async[comp.name] = _AsyncState(steps, comp.deadline, comp.on_deadline)

    # ================================================================ stepping
    def _gather(self, comp: Component, k: Optional[int] = None) -> Inputs:
        bus = self.bus
        latest = bus._latest
        previous = bus._snapshot
        values = Inputs()
        trace = self._trace_rows if self.trace else None
        if k is None:
            k = self.k
        for name, sources, delay, reduce, transform, default, read, sigs in self._plans[comp.name]:
            if sources is None:
                values[name] = default
                continue
            if read == "latest":
                store = previous if delay else latest
                if len(sources) == 1:
                    value = store.get(sources[0], _MISSING)
                    if value is _MISSING or value is None:  # no value yet / "no value" -> the input's default
                        value = default
                else:
                    collected = [store.get(s) for s in sources]
                    value = default if all(v is None for v in collected) else reduce(collected)
                if trace is not None:
                    for sig in sigs:
                        i = sig.index_at_step(k - 1) if delay else sig.latest_index()
                        trace.append((k, comp.name, name, sig.ref, i, i))
            else:
                value = self._aligned(comp, name, read, sigs, default, reduce, k, trace)
            if transform is not None and value is not None:
                value = transform(value)
            values[name] = value
        return values

    def _aligned(self, comp, name, read, sigs, default, reduce, k, trace):
        s = k * self.dt
        h = self.dt * self._multiple[comp.name]
        collected = []
        worst = 1.0
        for sig in sigs:
            if read == "at":
                v, cov, ids = sig.at(s)
            elif read == "past":
                v, cov, ids = sig.window(s - h, s)
            else:  # ahead
                v, cov, ids = sig.window(s, s + h)
            worst = min(worst, cov)
            collected.append(None if v is MISSING else v)
            if trace is not None:
                trace.append((k, comp.name, name, sig.ref, ids[0] if ids else -1, ids[-1] if ids else -1))
        stats = self._align[(comp.name, name)]
        stats[2] += 1
        if worst < 1.0 - 1e-9:
            stats[3] += 1
            stats[4] = min(stats[4], worst)
        if len(collected) == 1:
            value = collected[0]
            return default if value is None else value
        return default if all(v is None for v in collected) else reduce(collected)

    def _publish(self, comp: Component, result: Any, phase: str = "step", t: Optional[datetime] = None,
                 origin: Optional[int] = None) -> None:
        try:
            values = coerce_outputs(comp, result)
        except PyCoSimError as exc:
            raise ComponentError(comp.name, phase, t, exc) from exc
        if not values:
            return
        refs = self._out_refs[comp.name]
        publish = self.bus.publish
        checks = self._validators.get(comp.name)
        for port, value in values.items():
            start = end = None
            quality = 0
            msg_origin = origin
            if isinstance(value, Message):
                start, end = value.start, value.end
                if value.quality is not None:
                    quality = value.quality if isinstance(value.quality, int) else QUALITY.get(str(value.quality), 0)
                if value.origin is not None:
                    msg_origin = value.origin
                value = value.value
            if checks is not None and port in checks:
                try:
                    value, q = checks[port](value)
                except PyCoSimError as exc:
                    raise ComponentError(comp.name, phase, t, exc) from exc
                quality = max(quality, q)
            publish(refs[port], value, start, end, quality, msg_origin)

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

    # ------------------------------------------------------------ asynchrony
    def _async_call(self, comp: Component, t: datetime, k: int, inputs: Inputs):
        _ASYNC.k = k
        timing = self._timing[comp.name]
        t0 = time.perf_counter()
        try:
            return comp.step(t, inputs)
        finally:
            _ASYNC.k = None
            timing[0] += 1
            timing[1] += time.perf_counter() - t0

    def _submit(self, comp: Component, t: datetime, k: int, inputs: Inputs) -> None:
        state = self._async[comp.name]
        if state.pool is None:
            state.pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"pycosim-{comp.name}")
        future = state.pool.submit(self._async_call, comp, t, k, inputs)
        state.pending.append((k + state.delay_steps, future, time.perf_counter(), k))

    def _deliver(self, k: int) -> None:
        """Publish the answers of asynchronous components that are due at step ``k``."""
        t = self.times[k]
        for name, state in self._async.items():
            comp = self._components[name]
            while state.pending and state.pending[0][0] <= k:
                _due, future, submitted, k_in = state.pending.popleft()
                timeout = None
                if state.deadline is not None:
                    timeout = max(0.0, state.deadline - (time.perf_counter() - submitted))
                try:
                    result = future.result(timeout=timeout)
                except (cf.TimeoutError, cf.CancelledError) as exc:
                    state.late += 1
                    abandoned = isinstance(exc, cf.CancelledError)
                    if state.policy == "error":
                        raise ComponentError(name, "step", self.times[k_in], SynchronizationError(
                            f"no answer within the deadline of {state.deadline:g}s")) from None
                    why = ("queued behind a call that missed its deadline" if abandoned
                           else f"missed its {state.deadline:g}s deadline")
                    self.bus.log_event(t, name, "late", f"answer for {self.times[k_in]} {why}; previous outputs kept")
                    # keep the previous outputs, flagged late
                    held = {p: Message(self.bus.latest(r), quality=QUALITY["late"])
                            for p, r in self._out_refs[name].items()}
                    self._publish(comp, held, "deliver", t, origin=k_in)
                    if not abandoned and state.pool is not None:
                        # abandon the hung call; calls queued behind it are cancelled and reported late in turn
                        state.pool.shutdown(wait=False, cancel_futures=True)
                        state.pool = None
                    continue
                except PyCoSimError as exc:
                    raise ComponentError(name, "step", self.times[k_in], exc) from exc
                except Exception as exc:
                    raise ComponentError(name, "step", self.times[k_in], exc) from exc
                state.delivered += 1
                self._publish(comp, result, "deliver", t, origin=k_in)

    def _shutdown_async(self) -> None:
        for state in self._async.values():
            if state.pool is not None:
                state.pool.shutdown(wait=False, cancel_futures=True)
                state.pool = None
            state.pending.clear()

    # ------------------------------------------------------------- the step
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
        # Phase 0: asynchronous answers due now.
        if self._async:
            self._deliver(k)
        due_moore = [c for c in self._moore if k % mult[c.name] == 0]
        # Phase 1: state-based components report their outputs at t.
        for comp in due_moore:
            m = mult[comp.name]
            self._publish(comp, self._call(comp, "output", t, comp.output, t), "output", t,
                          origin=(k - m) if k >= m else None)
        # Phase 2: feedthrough components in topological order.
        for comp in self._order:
            if k % mult[comp.name]:
                continue
            inputs = self._gather(comp, k)
            self._failed_inputs = inputs
            if comp.name in self._async:
                self._submit(comp, t, k, inputs)
                continue
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
            inputs = self._gather(comp, k)
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
        self._shutdown_async()
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
        self._shutdown_async()
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

    def _recorded_refs(self) -> List[str]:
        if not self.record or self.bus is None:
            return []
        if self.record is True:
            return list(self.bus.signals)
        names = set(self.record)
        unknown = names - set(self._components)
        if unknown:
            logger.warning("record: unknown components %s", sorted(unknown))
        return [r for r in self.bus.signals if r.split(".", 1)[0] in names]

    def _build_results(self, error: Optional[str]) -> Results:
        bus = self.bus
        df = bus.to_frame() if bus is not None else pd.DataFrame()
        events = pd.DataFrame(bus.events if bus is not None else [],
                              columns=None if (bus and bus.events) else ["time", "source", "kind", "message"])
        timing = pd.DataFrame(
            [
                {"component": n, "calls": int(c), "total_s": s, "mean_ms": (1000 * s / c) if c else 0.0}
                for n, (c, s) in self._timing.items()
            ]
        )
        wall = time.perf_counter() - getattr(self, "_wall_start", time.perf_counter())
        ports = {}
        if bus is not None:
            for ref, sig in bus.signals.items():
                if sig.kind:
                    ports[ref] = {"kind": sig.kind, "interval": sig.interval if sig.kind in ("average", "amount")
                                  else None}
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
            "port_kinds": ports,
            "topics": dict(bus.topics) if bus is not None else {},
            "asynchronous": {n: {"delay_steps": s.delay_steps, "deadline_s": s.deadline, "delivered": s.delivered,
                                 "late": s.late} for n, s in self._async.items()},
            "replayed": sorted(self._replays),
            "recorded": self.record if isinstance(self.record, list) else bool(self.record),
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
        units = dict(bus.units) if bus is not None else {}
        alignment = pd.DataFrame(
            [{"input": f"{c}.{p}", "read": v[0], "sources": ", ".join(v[1]), "reads": v[2],
              "extrapolated_reads": v[3], "min_coverage": v[4]} for (c, p), v in self._align.items()],
            columns=["input", "read", "sources", "reads", "extrapolated_reads", "min_coverage"])
        quality = pd.DataFrame(bus.quality_counts() if bus is not None else [],
                               columns=["ref", "quality", "messages"])
        recorded = self._recorded_refs()
        messages = bus.messages_frame(recorded) if recorded else None
        raw = None
        if recorded:
            raw = []
            for ref in recorded:
                sig = bus.signals[ref]
                for i in range(len(sig.ks)):
                    raw.append({"ref": ref, "i": i, "step": sig.ks[i], "value": sig.vs[i], "start_s": sig.a[i],
                                "end_s": sig.b[i], "quality": sig.q[i], "origin": sig.origin[i]})
        trace = None
        if self.trace:
            trace = pd.DataFrame(self._trace_rows,
                                 columns=["step", "consumer", "input", "source", "first_index", "last_index"])
        res = Results(df, events, timing, meta, error, units, messages=messages, quality=quality,
                      alignment=alignment, trace=trace)
        res._raw_messages = raw
        return res


def _split_sources(source: Any) -> Tuple[List[PortRef], List[str]]:
    """Separate exact ``component.port`` references from topics and glob patterns."""
    if isinstance(source, (str, PortRef)):
        items = [source]
    elif isinstance(source, tuple) and len(source) == 2 and (
            isinstance(source[0], Component) or (isinstance(source[0], str) and "." not in source[0]
                                                 and not set(source[0]) & _GLOB
                                                 and isinstance(source[1], str) and "." not in source[1]
                                                 and "/" not in source[0])):
        return iter_refs(source), []
    else:
        items = list(source)
    refs: List[PortRef] = []
    patterns: List[str] = []
    for item in items:
        if isinstance(item, str) and (set(item) & _GLOB or "." not in item):
            patterns.append(item)
        else:
            refs.append(PortRef.parse(item))
    return refs, patterns


def _make_validator(comp_name: str, port) -> Callable[[Any], Tuple[Any, int]]:
    lo, hi = port.bounds
    policy = port.on_invalid
    state = {"last": port.default}

    def check(value: Any) -> Tuple[Any, int]:
        if value is None:
            return None, 0
        f = _num(value)
        if f is None:  # non-numeric or non-finite
            if policy == "error":
                raise ConfigurationError(f"{comp_name}.{port.name}: invalid value {value!r}")
            fallback = port.default if policy == "default" else state["last"]
            return fallback, QUALITY["fallback"]
        if (lo is not None and f < lo) or (hi is not None and f > hi):
            if policy == "error":
                raise ConfigurationError(f"{comp_name}.{port.name}: value {value!r} outside {tuple(port.bounds)}")
            if policy == "clip":
                v = min(max(f, lo if lo is not None else f), hi if hi is not None else f)
                state["last"] = v
                return v, QUALITY["clipped"]
            fallback = port.default if policy == "default" else state["last"]
            return fallback, QUALITY["fallback"]
        state["last"] = value
        return value, 0

    return check


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
