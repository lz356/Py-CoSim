"""Exception hierarchy used across Py-CoSim.

Every error raised by the framework derives from :class:`PyCoSimError`, so
user code can catch framework problems with a single ``except`` clause while
still letting genuine bugs in component code surface with their original
traceback (wrapped in :class:`ComponentError`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence


class PyCoSimError(Exception):
    """Base class for all Py-CoSim errors."""


class ConfigurationError(PyCoSimError):
    """The co-simulation is set up inconsistently (bad port, step size, ...)."""


class AlgebraicLoopError(ConfigurationError):
    """Components form a cycle of direct-feedthrough connections.

    A cycle such as ``A -> B -> A`` in which every component's outputs depend
    on its inputs *at the same time step* has no well-defined execution order.
    Break it by marking one connection as delayed (``delay=True``) or by
    declaring one component as ``direct_feedthrough = False``.
    """

    def __init__(self, cycle: Sequence[str]):
        self.cycle = list(cycle)
        path = " -> ".join(self.cycle + self.cycle[:1])
        super().__init__(
            "Algebraic loop detected between direct-feedthrough components: "
            f"{path}.\nBreak the loop by passing delay=True to one of the "
            "connections in this cycle (the target then reads the value from "
            "the previous time step), or declare one of the components with "
            "direct_feedthrough = False if its outputs only depend on its state."
        )


class SynchronizationError(PyCoSimError):
    """A component's internal clock disagrees with the master clock."""


class HandleNotFoundError(ConfigurationError):
    """EnergyPlus could not resolve one or more sensor/actuator handles."""

    def __init__(self, message: str, missing: Optional[list] = None):
        super().__init__(message)
        self.missing = missing or []


class ComponentError(PyCoSimError):
    """An exception raised inside a component, enriched with context.

    Attributes
    ----------
    component : str
        Name of the failing component.
    time : datetime or None
        Simulation time at which the failure happened.
    phase : str
        Lifecycle phase (``setup``, ``output``, ``step``, ``update`` ...).
    """

    def __init__(self, component: str, phase: str, time: Optional[datetime], original: BaseException):
        self.component = component
        self.phase = phase
        self.time = time
        self.original = original
        when = f" at {time:%Y-%m-%d %H:%M:%S}" if time is not None else ""
        super().__init__(
            f"Component '{component}' failed during '{phase}'{when}: "
            f"{type(original).__name__}: {original}"
        )
