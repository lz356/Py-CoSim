"""Results container returned by :meth:`CoSimulation.run`."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import pandas as pd

__all__ = ["Results"]


class Results:
    """Time series, event log, timing statistics and metadata of a run.

    Attributes
    ----------
    df : pandas.DataFrame
        One column per output port (``"component.port"``) on the master time
        grid. Values of slower components are held between their updates.
    events : pandas.DataFrame
        Structured log (warnings, messages emitted by components, errors).
    timing : pandas.DataFrame
        Wall-clock time spent in each component.
    meta : dict
        Start/end, step, wall time, versions, connections, ...
    error : str or None
        Text of the error report if the run failed.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        events: pd.DataFrame,
        timing: pd.DataFrame,
        meta: Dict[str, Any],
        error: Optional[str] = None,
        units: Optional[Dict[str, Optional[str]]] = None,
    ):
        self.df = df
        self.events = events
        self.timing = timing
        self.meta = meta
        self.error = error
        self.units = units or {}

    # ------------------------------------------------------------- accessors
    def __getitem__(self, key: Union[str, Sequence[str]]):
        return self.df[key]

    def __contains__(self, key: str) -> bool:
        return key in self.df.columns

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def wall_time(self) -> float:
        return float(self.meta.get("wall_time_s", float("nan")))

    def component(self, name: str) -> pd.DataFrame:
        """All outputs of one component, with the port names as columns."""
        prefix = name + "."
        cols = [c for c in self.df.columns if c.startswith(prefix)]
        if not cols:
            raise KeyError(f"No outputs recorded for component {name!r}")
        out = self.df[cols].copy()
        out.columns = [c[len(prefix):] for c in cols]
        return out

    def resample(self, rule: str = "1h", how: str = "mean") -> pd.DataFrame:
        """Resample numeric columns (e.g. hourly means)."""
        numeric = self.df.select_dtypes("number")
        return getattr(numeric.resample(rule), how)()

    def summary(self) -> str:
        m = self.meta
        lines = [
            f"Py-CoSim run '{m.get('name')}'",
            f"  period : {m.get('start')} -> {m.get('end')} (step {m.get('step')}, {m.get('n_steps')} steps)",
            f"  status : {'OK' if self.ok else 'FAILED'}",
            f"  wall   : {self.wall_time:.2f} s",
            f"  outputs: {len(self.df.columns)} series",
        ]
        if len(self.timing):
            top = self.timing.sort_values("total_s", ascending=False).head(5)
            lines.append("  slowest components: " + ", ".join(f"{r.component} ({r.total_s:.2f}s)" for r in top.itertuples()))
        return "\n".join(lines)

    def __repr__(self) -> str:
        return self.summary()

    # ------------------------------------------------------------------- I/O
    def save(self, folder: Union[str, Path]) -> Path:
        """Write ``timeseries.csv``, ``events.csv``, ``timing.csv`` and ``meta.json``."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        self.df.to_csv(folder / "timeseries.csv")
        self.events.to_csv(folder / "events.csv", index=False)
        self.timing.to_csv(folder / "timing.csv", index=False)
        with open(folder / "meta.json", "w", encoding="utf-8") as fh:
            json.dump({**self.meta, "units": self.units}, fh, indent=2, default=str)
        if self.error:
            (folder / "error_report.txt").write_text(self.error, encoding="utf-8")
        return folder

    @classmethod
    def load(cls, folder: Union[str, Path]) -> "Results":
        """Load results previously written with :meth:`save`."""
        folder = Path(folder)
        df = pd.read_csv(folder / "timeseries.csv", index_col=0, parse_dates=True)
        events = pd.read_csv(folder / "events.csv") if (folder / "events.csv").exists() else pd.DataFrame()
        timing = pd.read_csv(folder / "timing.csv") if (folder / "timing.csv").exists() else pd.DataFrame()
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8")) if (folder / "meta.json").exists() else {}
        err = (folder / "error_report.txt").read_text(encoding="utf-8") if (folder / "error_report.txt").exists() else None
        units = meta.pop("units", {}) or {}
        return cls(df, events, timing, meta, err, units)

    # -------------------------------------------------------------- plotting
    def plot(
        self,
        *groups: Union[str, Iterable[str]],
        title: Optional[str] = None,
        figsize=None,
        resample: Optional[str] = None,
    ):
        """Quick multi-panel plot. Each positional argument is one panel.

        Example: ``res.plot(["office.T_zone", "ctrl.clg_sp"], "office.P_elec")``
        """
        import matplotlib.pyplot as plt

        panels: List[List[str]] = []
        for g in groups or (list(self.df.select_dtypes("number").columns[:6]),):
            panels.append([g] if isinstance(g, str) else list(g))
        data = self.df if resample is None else self.resample(resample)
        fig, axes = plt.subplots(len(panels), 1, sharex=True, figsize=figsize or (10, 2.6 * len(panels)), squeeze=False)
        for ax, cols in zip(axes[:, 0], panels):
            for col in cols:
                unit = self.units.get(col)
                ax.plot(data.index, data[col], label=f"{col}{f' [{unit}]' if unit else ''}", lw=1.2)
            ax.legend(loc="upper left", fontsize=8, frameon=False)
            ax.grid(alpha=0.3)
        if title:
            axes[0, 0].set_title(title)
        fig.autofmt_xdate()
        fig.tight_layout()
        return fig
