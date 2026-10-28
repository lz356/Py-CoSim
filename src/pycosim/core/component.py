"""The :class:`Component` abstraction -- the single building block of Py-CoSim.

A component is a discrete-time system with named *input* and *output* ports.
The master algorithm (:class:`pycosim.CoSimulation`) calls it on a fixed time
grid. Two kinds of components exist:

``direct_feedthrough = True`` (default, "Mealy" type)
    Outputs at time ``t`` may depend on inputs at the same time ``t``.
    Implement :meth:`Component.step`, which receives the inputs and returns the
    outputs. Controllers, tariffs, data readers, ML models and LLM agents are
    typically of this kind.

``direct_feedthrough = False`` ("Moore" / state-space type)
    Outputs at ``t`` depend only on the internal state. Implement
    :meth:`Component.output` (report the state at ``t``) and
    :meth:`Component.update` (advance the state from ``t`` to ``t + dt`` using
    the inputs). Physical simulators such as EnergyPlus or an RC thermal model
    belong here. Because their outputs are known before their inputs, they
    break feedback loops naturally (e.g. building -> controller -> building).

The simplest way to write a component is a plain function::

    @pycosim.component(outputs="clg_sp")
    def precool(t, price=0.1):
        return 23.0 if price < 0.15 else 26.0
"""
from __future__ import annotations

import copy as _copy
import inspect
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from .errors import ConfigurationError
from .messages import Message
from .timeutils import DurationLike, parse_duration

__all__ = ["Port", "PortRef", "Inputs", "Component", "FunctionComponent", "component", "HOLD"]


class _Hold:
    """Sentinel: return ``pycosim.HOLD`` from a function component to keep its previous outputs."""

    def __repr__(self) -> str:
        return "pycosim.HOLD"


HOLD = _Hold()

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")


def _check_name(name: str, what: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ConfigurationError(
            f"Invalid {what} name {name!r}: use letters, digits, '_' or '-', starting with a letter or '_' "
            "(dots are reserved for 'component.port' references)."
        )
    return name


def _snake_case(text: str) -> str:
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text).lower()


@dataclass
class Port:
    """Metadata of an input or output port.

    Parameters
    ----------
    name : str
        Port name, unique within its component.
    default : Any
        For inputs: value used when the port is not connected or the source has
        not produced a value yet. For outputs: the *initial* value, visible to
        delayed connections before the component first runs.
    unit : str, optional
        Free-text unit used in documentation and result metadata (e.g. ``"W"``).
    description : str, optional
        Human-readable description.
    kind : {"instant", "average", "amount", "hold", "event"}, optional
        *Outputs:* what a published value means in time (see
        :mod:`pycosim.core.messages`). ``None`` (untyped) behaves like ``"hold"``.
    interval : {"past", "ahead"}, optional
        *Outputs* of kind ``average``/``amount``: whether a value published at
        ``t`` covers the producer's step that just ended (``[t - h, t)``, the
        default, as EnergyPlus reports) or the one that starts (``[t, t + h)``).
    read : {"latest", "at", "past", "ahead"}, optional
        *Inputs:* what the component needs relative to its own step ``[t, t+h)``:
        the most recent message (``latest``, default), the value in effect at
        ``t`` (``at``), or the aggregate over the step that just ended
        (``past``) or starts (``ahead``). SMMM aligns the source accordingly.
    bounds : (low, high), optional
        *Outputs:* valid range. Values outside it are handled by ``on_invalid``
        and the message is flagged.
    on_invalid : {"clip", "last", "default", "error"}
        What SMMM does with an out-of-range or non-finite output (only used when
        ``bounds`` is given): clip it (non-finite values fall back to the last
        valid value), keep the last valid value, use the port's initial value,
        or stop the run.
    topic : str, optional
        *Outputs:* a global name (e.g. ``"grid/price"``) consumers can subscribe
        to without knowing which component publishes it.
    """

    name: str
    default: Any = None
    unit: Optional[str] = None
    description: str = ""
    kind: Optional[str] = None
    interval: Optional[str] = None
    read: Optional[str] = None
    bounds: Optional[Tuple[float, float]] = None
    on_invalid: str = "clip"
    topic: Optional[str] = None

    def __post_init__(self) -> None:
        _check_name(self.name, "port")
        from .messages import INTERVALS, KINDS, READS

        if self.kind is not None and self.kind not in KINDS:
            raise ConfigurationError(f"port {self.name!r}: kind must be one of {KINDS} (got {self.kind!r})")
        if self.interval is not None and self.interval not in INTERVALS:
            raise ConfigurationError(f"port {self.name!r}: interval must be one of {INTERVALS}")
        if self.read is not None and self.read not in READS:
            raise ConfigurationError(f"port {self.name!r}: read must be one of {READS} (got {self.read!r})")
        if self.on_invalid not in ("clip", "last", "default", "error"):
            raise ConfigurationError(f"port {self.name!r}: on_invalid must be 'clip', 'last', 'default' or 'error'")
        if self.bounds is not None:
            lo, hi = self.bounds
            if lo is not None and hi is not None and lo > hi:
                raise ConfigurationError(f"port {self.name!r}: bounds {self.bounds} are inverted")

    def tags(self) -> str:
        """Short description of the time meaning, e.g. ``"average/past"`` or ``"read=past"``."""
        bits = []
        if self.kind:
            bits.append(self.kind + (f"/{self.interval}" if self.kind in ("average", "amount") else ""))
        if self.read and self.read != "latest":
            bits.append(f"read={self.read}")
        if self.bounds is not None:
            bits.append(f"bounds={tuple(self.bounds)}")
        if self.topic:
            bits.append(f"topic={self.topic}")
        return ", ".join(bits)


@dataclass(frozen=True)
class PortRef:
    """Reference to a port of a named component (``"component.port"``)."""

    component: str
    port: str

    def __str__(self) -> str:
        return f"{self.component}.{self.port}"

    @classmethod
    def parse(cls, ref: Any) -> "PortRef":
        if isinstance(ref, PortRef):
            return ref
        if isinstance(ref, tuple) and len(ref) == 2:
            comp, port = ref
            comp_name = comp.name if isinstance(comp, Component) else str(comp)
            return cls(comp_name, str(port))
        if isinstance(ref, str):
            if "." not in ref:
                raise ConfigurationError(f"Port reference {ref!r} must look like 'component.port'")
            comp, port = ref.split(".", 1)
            return cls(comp, port)
        raise ConfigurationError(f"Cannot interpret {ref!r} as a port reference")


class Inputs(dict):
    """Dictionary of input values that also supports attribute access.

    ``inputs["T_zone"]`` and ``inputs.T_zone`` are equivalent.
    """

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError:
            raise AttributeError(
                f"No input named {item!r}. Available inputs: {', '.join(self) or '(none)'}"
            ) from None


PortSpec = Union[Mapping[str, Any], Sequence[Union[str, Port]], str, None]
_PORT_FIELDS = {"default", "unit", "description", "kind", "interval", "read", "bounds", "on_invalid", "topic"}


def _normalize_ports(spec: PortSpec, kind: str) -> Dict[str, Port]:
    """Turn the many accepted declaration styles into ``{name: Port}``."""
    ports: Dict[str, Port] = {}
    if spec is None:
        return ports
    if isinstance(spec, str):
        spec = [spec]
    if isinstance(spec, Mapping):
        for name, value in spec.items():
            if isinstance(value, Port):
                port = _copy.copy(value)
                port.name = name
            elif isinstance(value, Mapping) and set(value) <= _PORT_FIELDS:
                port = Port(name, **value)
            else:
                port = Port(name, default=value)
            ports[name] = port
    else:
        for item in spec:
            port = _copy.copy(item) if isinstance(item, Port) else Port(str(item))
            if port.name in ports:
                raise ConfigurationError(f"Duplicate {kind} port {port.name!r}")
            ports[port.name] = port
    return ports


class Component:
    """Base class for co-simulation components.

    Subclasses declare their ports either as class attributes::

        class Battery(Component):
            inputs = {"P_set": 0.0}                # name -> default value
            outputs = {"P": 0.0, "soc": 0.5}       # name -> initial value

    or dynamically with :meth:`add_input` / :meth:`add_output` (useful when
    the ports depend on constructor arguments).

    Attributes set by the master before :meth:`setup` is called
    ---------------------------------------------------------------
    ctx : :class:`pycosim.Context`
        Access to the clock, the data bus (history of any port), a
        reproducible random generator, logging and the output folder.
    """

    #: Declared input ports (``{name: default}`` or a list of names).
    inputs: ClassVar[PortSpec] = None
    #: Declared output ports (``{name: initial}`` or a list of names).
    outputs: ClassVar[PortSpec] = None
    #: Default step size (seconds or string such as ``"1h"``); ``None`` = every master step.
    step_size: ClassVar[Optional[DurationLike]] = None
    #: Whether outputs at ``t`` may depend on inputs at ``t`` (see module docstring).
    direct_feedthrough: ClassVar[bool] = True
    #: Tie-breaker for the execution order of independent components (lower runs first).
    priority: ClassVar[int] = 0
    #: Run ``step`` in the background and deliver its outputs ``decision_delay`` later (see :meth:`make_async`).
    asynchronous: ClassVar[bool] = False
    #: Simulation time between the inputs an asynchronous component sees and the moment its outputs apply.
    decision_delay: ClassVar[Optional[DurationLike]] = None
    #: Wall-clock seconds an asynchronous call may take before ``on_deadline`` applies (None = wait).
    deadline: ClassVar[Optional[float]] = None
    #: ``"fallback"`` (keep the previous outputs, flag them ``late``) or ``"error"``.
    on_deadline: ClassVar[str] = "fallback"

    def __init__(
        self,
        name: Optional[str] = None,
        *,
        step_size: Optional[DurationLike] = None,
        priority: Optional[int] = None,
    ) -> None:
        self.name = _check_name(name, "component") if name is not None else _snake_case(type(self).__name__)
        self.input_ports: Dict[str, Port] = _normalize_ports(type(self).inputs, "input")
        self.output_ports: Dict[str, Port] = _normalize_ports(type(self).outputs, "output")
        if step_size is not None:
            self.step_size = step_size  # instance attribute shadows the class default
        if priority is not None:
            self.priority = priority
        self.ctx = None  # set by the master
        self._pc_initialized = True

    # ------------------------------------------------------------------ ports
    def add_input(self, name: str, default: Any = None, unit: Optional[str] = None, description: str = "", *,
                  read: Optional[str] = None) -> Port:
        """Declare an input port at runtime (call from ``__init__``)."""
        self._ensure_ports()
        if name in self.input_ports:
            raise ConfigurationError(f"{self.name}: input {name!r} already exists")
        port = Port(name, default, unit, description, read=read)
        self.input_ports[name] = port
        return port

    def add_output(self, name: str, initial: Any = None, unit: Optional[str] = None, description: str = "", *,
                   kind: Optional[str] = None, interval: Optional[str] = None,
                   bounds: Optional[Tuple[float, float]] = None, on_invalid: str = "clip",
                   topic: Optional[str] = None) -> Port:
        """Declare an output port at runtime (call from ``__init__``)."""
        self._ensure_ports()
        if name in self.output_ports:
            raise ConfigurationError(f"{self.name}: output {name!r} already exists")
        port = Port(name, initial, unit, description, kind=kind, interval=interval, bounds=bounds,
                    on_invalid=on_invalid, topic=topic)
        self.output_ports[name] = port
        return port

    def set_port(self, name: str, **attrs: Any) -> Port:
        """Change metadata of an existing port, e.g. ``comp.set_port("P", kind="average", interval="ahead")``."""
        self._ensure_ports()
        port = self.output_ports.get(name) or self.input_ports.get(name)
        if port is None:
            raise ConfigurationError(f"{self.name}: no port named {name!r}")
        unknown = set(attrs) - _PORT_FIELDS
        if unknown:
            raise ConfigurationError(f"{self.name}: unknown port attributes {sorted(unknown)}")
        for key, value in attrs.items():
            setattr(port, key, value)
        port.__post_init__()
        return port

    def make_async(self, delay: Optional[DurationLike] = None, deadline: Optional[float] = None,
                   on_deadline: str = "fallback") -> "Component":
        """Run this (feedthrough) component asynchronously.

        At each of its steps the master hands the inputs to a background thread
        and keeps simulating. The outputs are published ``delay`` later in
        *simulation* time (default: one master step), whatever the call took in
        wall-clock time -- so results do not depend on how fast the computer or
        the remote service is, and several slow components (LLMs, web APIs)
        overlap. If ``deadline`` (wall-clock seconds) is given and missed, the
        previous outputs are kept and flagged ``late`` (``on_deadline="fallback"``)
        or the run stops (``"error"``).
        """
        if on_deadline not in ("fallback", "error"):
            raise ConfigurationError("on_deadline must be 'fallback' or 'error'")
        if deadline is not None and deadline <= 0:
            raise ConfigurationError("deadline must be positive (seconds)")
        self.asynchronous = True
        self.decision_delay = delay
        self.deadline = deadline
        self.on_deadline = on_deadline
        return self

    def __getitem__(self, port: str) -> PortRef:
        """``component["T_zone"]`` returns a :class:`PortRef` usable in ``connect``."""
        self._ensure_ports()
        if port not in self.input_ports and port not in self.output_ports:
            raise KeyError(f"Component {self.name!r} has no port {port!r}")
        return PortRef(self.name, port)

    # -------------------------------------------------------------- lifecycle
    def setup(self, ctx) -> None:
        """Called once before the first step. Allocate resources here."""

    def ready(self) -> None:
        """Called after *all* components ran :meth:`setup`; block here until external resources are ready.

        Splitting slow start-ups into a non-blocking ``setup`` (e.g. launch a
        process) and a blocking ``ready`` (wait for it) lets many such
        components start in parallel -- EnergyPlus uses this for its sizing and
        warm-up periods.
        """

    def step(self, t: datetime, inputs: Inputs) -> Any:
        """Compute outputs at time ``t`` (direct-feedthrough components).

        Return a dict ``{output_name: value}``, a single value (when the
        component has exactly one output), a tuple in output-declaration order,
        or ``None`` to keep the previous outputs.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement step(t, inputs) "
            "(or set direct_feedthrough = False and implement output()/update())"
        )

    def output(self, t: datetime) -> Any:
        """Report outputs at ``t`` from the internal state (non-feedthrough components)."""
        return None

    def update(self, t: datetime, inputs: Inputs) -> None:
        """Advance the internal state from ``t`` to ``t + dt`` (non-feedthrough components)."""

    def finalize(self) -> None:
        """Called once after the last step (also after a failure). Release resources here."""

    def abort(self) -> None:
        """Emergency stop (e.g. kill external processes). Defaults to :meth:`finalize`."""
        self.finalize()

    # ---------------------------------------------------------------- helpers
    @property
    def rng(self):
        """Reproducible :class:`numpy.random.Generator` for this component."""
        if self.ctx is None:
            raise RuntimeError("rng is only available after the component was added to a simulation and set up")
        return self.ctx.rng

    def describe(self) -> str:
        """One-paragraph text description of the component's interface."""
        self._ensure_ports()
        kind = "feedthrough" if self.direct_feedthrough else "state-based (no feedthrough)"
        if self.asynchronous:
            kind += f", asynchronous (delay={self.decision_delay or 'one step'})"
        lines = [f"{self.name} [{type(self).__name__}, {kind}, step={self.step_size or 'master'}]"]
        for title, ports in (("inputs", self.input_ports), ("outputs", self.output_ports)):
            if ports:
                desc = ", ".join(
                    f"{p.name}{f' [{p.unit}]' if p.unit else ''}{f' ({p.tags()})' if p.tags() else ''}"
                    for p in ports.values())
                lines.append(f"  {title}: {desc}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {getattr(self, 'name', '?')!r}>"

    def _ensure_ports(self) -> None:
        """Make components robust to subclasses that forget ``super().__init__()``."""
        if getattr(self, "_pc_initialized", False):
            return
        if not hasattr(self, "name") or self.name is None:
            self.name = _snake_case(type(self).__name__)
        if not hasattr(self, "input_ports"):
            self.input_ports = _normalize_ports(type(self).inputs, "input")
        if not hasattr(self, "output_ports"):
            self.output_ports = _normalize_ports(type(self).outputs, "output")
        if not hasattr(self, "ctx"):
            self.ctx = None
        self._pc_initialized = True


class FunctionComponent(Component):
    """Wrap a plain Python function as a (stateless) component.

    The first parameter of the function receives the current time (a
    :class:`datetime`). The remaining parameters are the inputs; their default
    values become the input defaults. A parameter named ``ctx`` receives the
    :class:`~pycosim.Context` instead.

    Use the :func:`component` decorator rather than instantiating this class
    directly.
    """

    def __init__(
        self,
        func: Callable[..., Any],
        name: Optional[str] = None,
        *,
        inputs: PortSpec = None,
        outputs: PortSpec = None,
        step_size: Optional[DurationLike] = None,
        direct_feedthrough: bool = True,
        priority: int = 0,
    ) -> None:
        if not callable(func):
            raise TypeError("func must be callable")
        super().__init__(name or func.__name__, step_size=step_size, priority=priority)
        self.func = func
        self.direct_feedthrough = direct_feedthrough
        signature = inspect.signature(func)
        params = list(signature.parameters.values())
        positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        self._time_param = positional[0].name if positional and positional[0].name != "ctx" else None
        if self._time_param is not None and positional[0].default is not inspect.Parameter.empty:
            import warnings

            warnings.warn(
                f"The first parameter '{self._time_param}' of {func.__name__}() receives the simulation time, but it "
                f"has a default value as if it were an input. Write e.g. def {func.__name__}(t, "
                f"{self._time_param}=...) if '{self._time_param}' is meant to be an input.", stacklevel=3)
        self._wants_ctx = "ctx" in signature.parameters
        self._var_kw = any(p.kind is p.VAR_KEYWORD for p in params)
        if inputs is None:
            inferred: Dict[str, Any] = {}
            for p in params:
                if p.name in (self._time_param, "ctx") or p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
                    continue
                inferred[p.name] = None if p.default is p.empty else p.default
            self.input_ports = _normalize_ports(inferred, "input")
        else:
            self.input_ports = _normalize_ports(inputs, "input")
            accepted = {p.name for p in params}
            missing = [n for n in self.input_ports if n not in accepted]
            if missing and not self._var_kw:
                raise ConfigurationError(
                    f"Function {func.__name__}() does not accept declared inputs {missing}; "
                    "add them as parameters or add **inputs"
                )
        self.output_ports = _normalize_ports(outputs if outputs is not None else ["out"], "output")
        if not direct_feedthrough:
            raise ConfigurationError(
                "Function components are stateless; use a Component subclass for non-feedthrough behaviour"
            )

    def step(self, t: datetime, inputs: Inputs) -> Any:
        kwargs = dict(inputs)
        if self._wants_ctx:
            kwargs["ctx"] = self.ctx
        result = self.func(t, **kwargs) if self._time_param is not None else self.func(**kwargs)
        if result is None and len(self.output_ports) == 1:
            # For a single-output function, None is a value ("no value" -- e.g. release an EnergyPlus
            # actuator), not "keep the previous output". Return HOLD to keep the previous output.
            return {next(iter(self.output_ports)): None}
        if result is HOLD:
            return None
        return result

    def copy(self, name: str, **overrides: Any) -> "FunctionComponent":
        """Create an independent instance (e.g. one controller per building)."""
        clone = _copy.copy(self)
        clone.name = _check_name(name, "component")
        clone.input_ports = {k: _copy.copy(v) for k, v in self.input_ports.items()}
        clone.output_ports = {k: _copy.copy(v) for k, v in self.output_ports.items()}
        clone.ctx = None
        for key, value in overrides.items():
            setattr(clone, key, value)
        return clone


def component(
    func: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    inputs: PortSpec = None,
    outputs: PortSpec = None,
    step_size: Optional[DurationLike] = None,
    priority: int = 0,
):
    """Decorator turning a function into a :class:`FunctionComponent`.

    Examples
    --------
    >>> @component(outputs="clg_sp", step_size="1h")
    ... def schedule(t):
    ...     return 24.0 if 8 <= t.hour < 18 else 29.0

    >>> @component(outputs=["P_set"])
    ... def peak_shaver(t, net_load=0.0, limit=50e3):
    ...     return -max(net_load - limit, 0.0)
    """

    def wrap(f: Callable[..., Any]) -> FunctionComponent:
        return FunctionComponent(f, name=name, inputs=inputs, outputs=outputs, step_size=step_size, priority=priority)

    if func is not None:
        return wrap(func)
    return wrap


def normalize_step_size(comp: Component, master_dt: float) -> int:
    """Return the component's step size as an integer multiple of the master step."""
    size = getattr(comp, "step_size", None)
    if size is None:
        return 1
    seconds = parse_duration(size)
    ratio = seconds / master_dt
    multiple = int(round(ratio))
    if multiple < 1 or abs(ratio - multiple) > 1e-9:
        raise ConfigurationError(
            f"Component '{comp.name}' has step size {seconds:g}s which is not a positive integer multiple "
            f"of the master step {master_dt:g}s"
        )
    return multiple


def coerce_outputs(comp: Component, result: Any) -> Optional[Dict[str, Any]]:
    """Normalize what a component returned into ``{output_name: value}``.

    Values may be :class:`pycosim.Message` envelopes; they are passed through.
    """
    if result is None:
        return None
    ports = comp.output_ports
    if type(result) is dict:  # fast path for the common case
        for key in result:
            if key not in ports:
                unknown = [k for k in result if k not in ports]
                raise ConfigurationError(
                    f"Component '{comp.name}' returned unknown outputs {unknown}; declared outputs are {list(ports)}"
                )
        return result
    if isinstance(result, Message):
        if len(ports) == 1:
            return {next(iter(ports)): result}
        raise ConfigurationError(
            f"Component '{comp.name}' returned a single Message but declares {len(ports)} outputs; "
            "return a dict {output: Message(...)}")
    if isinstance(result, Mapping):
        unknown = [k for k in result if k not in ports]
        if unknown:
            raise ConfigurationError(
                f"Component '{comp.name}' returned unknown outputs {unknown}; declared outputs are {list(ports)}"
            )
        return dict(result)
    if isinstance(result, (tuple, list)) and len(ports) > 1:
        if len(result) != len(ports):
            raise ConfigurationError(
                f"Component '{comp.name}' returned a sequence of length {len(result)} "
                f"but declares {len(ports)} outputs {list(ports)}"
            )
        return dict(zip(ports, result))
    if len(ports) == 1:
        return {next(iter(ports)): result}
    raise ConfigurationError(
        f"Component '{comp.name}' returned a single value but declares {len(ports)} outputs; "
        "return a dict {output: value} or a tuple in declaration order"
    )


def iter_refs(items: Union[Any, Iterable[Any]]) -> List[PortRef]:
    """Parse one or many port references."""
    if isinstance(items, (str, PortRef)):
        return [PortRef.parse(items)]
    if (
        isinstance(items, tuple)
        and len(items) == 2
        and (isinstance(items[0], Component) or "." not in str(items[0]))
    ):
        return [PortRef.parse(items)]  # (component, "port") pair
    return [PortRef.parse(i) for i in items]


Spec = Tuple[str, ...]
