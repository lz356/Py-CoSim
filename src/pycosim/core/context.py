"""Per-component runtime context handed to :meth:`Component.setup`."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from .master import CoSimulation

__all__ = ["Context"]


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
        """Current master time."""
        return self._sim.now

    @property
    def step_index(self) -> int:
        """Index of the current master step (0-based)."""
        return self._sim.k

    @property
    def step_timedelta(self) -> timedelta:
        return timedelta(seconds=self.step_size)

    # --------------------------------------------------------------- data bus
    def latest(self, ref: str, default: Any = None) -> Any:
        """Latest value of any port, e.g. ``ctx.latest("office.T_zone")``."""
        return self._sim.bus.latest(ref, default)

    def history(self, ref: str, n: Optional[int] = None):
        """History of any port as a pandas Series (last ``n`` publications)."""
        return self._sim.bus.history(ref, n)

    # ---------------------------------------------------------------- logging
    def log(self, message: str, level: int = logging.INFO, kind: str = "info", **data: Any) -> None:
        """Write to the Python log *and* to the co-simulation event log."""
        self.logger.log(level, message)
        self._sim.bus.log_event(self._sim.now if self._sim.k >= 0 else None, self.component, kind, message, **data)

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
