"""Observers watch a running co-simulation without taking part in it.

They are called by the master after every step and are the place for
progress bars, live ("online") plots and streaming recorders -- everything
that the 0.x "Online Visualization Module" did, but decoupled from the
building classes and safe to use with any component.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, List, Optional, Sequence, Union

if TYPE_CHECKING:  # pragma: no cover
    from .master import CoSimulation
    from .results import Results

__all__ = ["Observer", "ProgressBar", "LivePlot", "CSVStreamRecorder", "CallbackObserver"]


class Observer:
    """Base class. Override any of the three hooks."""

    def on_start(self, sim: "CoSimulation") -> None:
        """Called after all components were set up, before the first step."""

    def on_step(self, sim: "CoSimulation", k: int) -> None:
        """Called after master step ``k`` completed."""

    def on_end(self, sim: "CoSimulation", results: Optional["Results"]) -> None:
        """Called once at the end (also after failures, with partial results)."""


class CallbackObserver(Observer):
    """Wrap a function ``fn(sim, k)`` as an observer."""

    def __init__(self, fn: Callable[["CoSimulation", int], None], every: int = 1):
        self.fn = fn
        self.every = max(1, int(every))

    def on_step(self, sim, k):
        if k % self.every == 0:
            self.fn(sim, k)


class ProgressBar(Observer):
    """Text progress bar with simulated time and ETA (no extra dependencies)."""

    def __init__(self, stream=None, width: int = 30, min_interval: float = 0.5):
        self.stream = stream or sys.stderr
        self.width = width
        self.min_interval = min_interval
        self._t0 = 0.0
        self._last = 0.0

    def on_start(self, sim):
        self._t0 = self._last = time.perf_counter()

    def on_step(self, sim, k):
        now = time.perf_counter()
        done = k + 1
        if done < sim.n_steps and now - self._last < self.min_interval:
            return
        self._last = now
        frac = done / sim.n_steps
        elapsed = now - self._t0
        eta = elapsed / frac - elapsed if frac > 0 else 0.0
        filled = int(self.width * frac)
        bar = "#" * filled + "-" * (self.width - filled)
        self.stream.write(
            f"\r[{bar}] {frac:6.1%} {sim.now:%Y-%m-%d %H:%M} | {elapsed:6.1f}s elapsed, ETA {eta:5.1f}s"
        )
        if done >= sim.n_steps:
            self.stream.write("\n")
        self.stream.flush()

    def on_end(self, sim, results):
        if sim.k + 1 < sim.n_steps:
            self.stream.write("\n")
            self.stream.flush()


class LivePlot(Observer):
    """Online visualization: redraws selected series while the simulation runs.

    Parameters
    ----------
    panels : list
        Each element is a port reference or a list of references drawn in the
        same panel, e.g. ``[["office.T_zone", "ctrl.clg_sp"], "office.P_elec"]``.
    every : int
        Redraw every ``every`` master steps (redrawing is the slow part).
    window : int, optional
        Only show the last ``window`` steps (a scrolling plot).

    Works in plain scripts (an interactive window opens) and in Jupyter with
    ``%matplotlib widget``.
    """

    def __init__(self, panels: Sequence[Union[str, Sequence[str]]], every: int = 24, window: Optional[int] = None,
                 title: str = "Py-CoSim live view"):
        self.panels: List[List[str]] = [[p] if isinstance(p, str) else list(p) for p in panels]
        self.every = max(1, int(every))
        self.window = window
        self.title = title
        self._fig = None
        self._lines = []

    def on_start(self, sim):
        import matplotlib.pyplot as plt

        plt.ion()
        self._fig, axes = plt.subplots(len(self.panels), 1, sharex=True, figsize=(9, 2.5 * len(self.panels)), squeeze=False)
        self._axes = axes[:, 0]
        self._lines = []
        for ax, refs in zip(self._axes, self.panels):
            lines = []
            for ref in refs:
                unit = sim.bus.units.get(ref)
                (line,) = ax.plot([], [], label=f"{ref}{f' [{unit}]' if unit else ''}")
                lines.append(line)
            ax.legend(loc="upper left", fontsize=8, frameon=False)
            ax.grid(alpha=0.3)
            self._lines.append(lines)
        self._axes[0].set_title(self.title)
        self._fig.tight_layout()

    def _redraw(self, sim):
        import matplotlib.pyplot as plt

        upto = sim.k + 1
        start = 0 if self.window is None else max(0, upto - self.window)
        refs = [r for refs in self.panels for r in refs]
        frame = sim.bus.to_frame(refs, upto=upto).iloc[start:]
        for ax, refs, lines in zip(self._axes, self.panels, self._lines):
            for ref, line in zip(refs, lines):
                line.set_data(frame.index, frame[ref].to_numpy())
            ax.relim()
            ax.autoscale_view()
        self._fig.canvas.draw_idle()
        plt.pause(0.001)

    def on_step(self, sim, k):
        if self._fig is not None and (k % self.every == 0):
            self._redraw(sim)

    def on_end(self, sim, results):
        if self._fig is not None and sim.k >= 0:
            self._redraw(sim)


class CSVStreamRecorder(Observer):
    """Append selected values to a CSV file while the simulation runs.

    Useful to monitor very long runs from another program (or a spreadsheet)
    and to keep data if the process is killed.
    """

    def __init__(self, path: Union[str, Path], refs: Optional[Iterable[str]] = None, every: int = 1):
        self.path = Path(path)
        self.refs = list(refs) if refs is not None else None
        self.every = max(1, int(every))
        self._fh = None
        self._writer = None

    def on_start(self, sim):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.refs is None:
            self.refs = sim.bus.refs
        self._fh = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(["time"] + self.refs)

    def on_step(self, sim, k):
        if self._writer is None or k % self.every:
            return
        self._writer.writerow([sim.now.isoformat()] + [sim.bus.latest(r) for r in self.refs])
        if k % (self.every * 100) == 0:
            self._fh.flush()

    def on_end(self, sim, results):
        if self._fh is not None:
            self._fh.close()
            self._fh = None
