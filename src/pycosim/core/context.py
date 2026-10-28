"""Per-component runtime context handed to :meth:`Component.setup`."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from .master import CoSimulation

__all__ = ["Context"]

#: Set inside the worker thread of an asynchronous component: the master step whose state it may see.
_ASYNC = threading.local()


def _async_step():
    return getattr(_ASYNC, "k", None)


class Context:
    """Everything a component may need from the co-simulation at runtime.

    Attributes
    ----------
    start, end : datetime
        Simulation window (``end`` is exclusive).
    dt : float
        Master step in seconds.
    step_size : float
        This component's own step in seconds (a multiple of ``dt``).
    rng : numpy.random.Generator
        Random generator seeded from the simulation seed and the component name,
        so stochastic components are reproducible and independent of each other.
    logger : logging.Logger
        Logger named ``pycosim.<component>``.
    """

    def __init__(self, sim: "CoSimulation", component_name: str, step_multiple: int, seed: Optional[int]):
        self._sim = sim
        self.component = component_name
        self.start: datetime = sim.start
        self.end: datetime = sim.end
        self.dt: float = sim.dt
        self.step_multiple = step_multiple
        self.step_size: float = sim.dt * step_multiple
        self.n_steps: int = sim.n_steps
        seq = np.random.SeedSequence([seed if seed is not None else 0, _stable_hash(component_name)])
        self.rng = np.random.default_rng(seq)
        self.logger = logging.getLogger(f"pycosim.{component_name}")
        self._output_dir: Optional[Path] = None

    # ------------------------------------------------------------------ clock
    @property
    def now(self) -> datetime:
        """Current master time (for an asynchronous component: the time its inputs were taken)."""
        k = _async_step()
        return self._sim.times[k] if k is not None else self._sim.now

    @property
    def step_index(self) -> int:
        """Index of the current master step (0-based)."""
        k = _async_step()
        return k if k is not None else self._sim.k

    @property
    def step_timedelta(self) -> timedelta:
        return timedelta(seconds=self.step_size)

    # --------------------------------------------------------------- data bus
    def latest(self, ref: str, default: Any = None) -> Any:
        """Latest value of any port, e.g. ``ctx.latest("office.T_zone")``."""
        k = _async_step()
        if k is not None:
            return self._sim.bus.value_at_step(ref, k, default)
        return self._sim.bus.latest(ref, default)

    def history(self, ref: str, n: Optional[int] = None):
        """History of any port as a pandas Series (last ``n`` publications)."""
        return self._sim.bus.history(ref, n, upto_step=_async_step())

    def at(self, ref: str, when: Any = None) -> Any:
        """Value of any port in effect at ``when`` (default: now), aligned by the port's time meaning."""
        return self._sim.bus.at(ref, self.now if when is None else when, upto_step=_async_step())

    def window(self, ref: str, start: Any, end: Any = None) -> Any:
        """Aggregate of any port over ``[start, end)`` (default end: now): mean, total or event list."""
        return self._sim.bus.window(ref, start, self.now if end is None else end, upto_step=_async_step())

    # ---------------------------------------------------------------- logging
    def log(self, message: str, level: int = logging.INFO, kind: str = "info", **data: Any) -> None:
        """Write to the Python log *and* to the co-simulation event log."""
        self.logger.log(level, message)
        k = _async_step()
        when = self._sim.times[k] if k is not None else (self._sim.now if self._sim.k >= 0 else None)
        self._sim.bus.log_event(when, self.component, kind, message, **data)

    # ------------------------------------------------------------------ files
    @property
    def output_dir(self) -> Path:
        """Folder reserved for this component's files (created on first use)."""
        if self._output_dir is None:
            self._output_dir = self._sim.output_path / self.component
            self._output_dir.mkdir(parents=True, exist_ok=True)
        return self._output_dir


def _stable_hash(text: str) -> int:
    import zlib

    return zlib.crc32(text.encode("utf-8"))
