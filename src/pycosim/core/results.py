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
    messages : pandas.DataFrame or None
        Every message of the recorded components (``record=...``): value,
        validity interval, quality flag and originating step.
    quality : pandas.DataFrame
        Number of messages per port that were clipped, replaced by a fallback,
        late or replayed.
    alignment : pandas.DataFrame
        For every input read with ``at``/``past``/``ahead``: how often SMMM
        had to extrapolate because no message covered the whole window yet.
    trace : pandas.DataFrame or None
        Which messages each component read at each step (``trace=True``).
    """

    def __init__(
        self,
        df: pd.DataFrame,
        events: pd.DataFrame,
        timing: pd.DataFrame,
        meta: Dict[str, Any],
        error: Optional[str] = None,
        units: Optional[Dict[str, Optional[str]]] = None,
        *,
        messages: Optional[pd.DataFrame] = None,
        quality: Optional[pd.DataFrame] = None,
        alignment: Optional[pd.DataFrame] = None,
        trace: Optional[pd.DataFrame] = None,
    ):
        self.df = df
        self.events = events
        self.timing = timing
        self.meta = meta
        self.error = error
        self.units = units or {}
        self.messages = messages
        self.quality = quality if quality is not None else pd.DataFrame(columns=["ref", "quality", "messages"])
        self.alignment = alignment if alignment is not None else pd.DataFrame()
        self.trace = trace
        self._raw_messages: Optional[List[Dict[str, Any]]] = None

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

    # ------------------------------------------------------------ provenance
    def lineage(self, ref: str, when: Any = None, depth: int = 4) -> pd.DataFrame:
        """Trace a message back through the messages that produced it.

        Needs a run made with ``trace=True``. Returns one row per link: the
        message (``ref`` and its index), the input of its producer that read
        another message, and that message. ``when`` selects the last message
        of ``ref`` published at or before that time (default: the last one).
        """
        if self.trace is None or self.messages is None:
            raise ValueError("lineage() needs a run made with CoSimulation(..., trace=True)")
        msgs = self.messages
        own = msgs[msgs["ref"] == ref]
        if own.empty:
            raise KeyError(f"No messages recorded for {ref!r}")
        if when is not None:
            own = own[own["time"] <= pd.Timestamp(when)]
            if own.empty:
                raise KeyError(f"No message of {ref!r} at or before {when}")
        start_idx = int(own["index"].iloc[-1])
        lookup = msgs.set_index(["ref", "index"]).sort_index()
        trace = self.trace
        rows: List[Dict[str, Any]] = []
        seen = set()
        frontier = [(ref, start_idx, 0)]
        while frontier:
            r, i, level = frontier.pop(0)
            if (r, i) in seen or level >= depth:
                continue
            seen.add((r, i))
            try:
                row = lookup.loc[(r, i)]
            except KeyError:
                continue
            origin, t_msg, value = int(row["origin_step"]), row["time"], row["value"]
            producer = r.split(".", 1)[0]
            reads = trace[(trace["consumer"] == producer) & (trace["step"] == origin)]
            for read in reads.itertuples(index=False):
                first, last = int(read.first_index), int(read.last_index)
                if first < 0:
                    continue
                for j in range(first, last + 1):
                    try:
                        src = lookup.loc[(read.source, j)]
                    except KeyError:
                        continue
                    rows.append({"level": level, "message": r, "index": i, "time": t_msg, "value": value,
                                 "via_input": f"{producer}.{read.input}", "source": read.source, "source_index": j,
                                 "source_time": src["time"], "source_value": src["value"]})
                    frontier.append((read.source, j, level + 1))
        return pd.DataFrame(rows, columns=["level", "message", "index", "time", "value", "via_input", "source",
                                           "source_index", "source_time", "source_value"])

    # ------------------------------------------------------------------- I/O
    def save(self, folder: Union[str, Path]) -> Path:
        """Write ``timeseries.csv``, ``events.csv``, ``timing.csv``, ``meta.json`` and the SMMM tables."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        self.df.to_csv(folder / "timeseries.csv")
        self.events.to_csv(folder / "events.csv", index=False)
        self.timing.to_csv(folder / "timing.csv", index=False)
        with open(folder / "meta.json", "w", encoding="utf-8") as fh:
            json.dump({**self.meta, "units": self.units}, fh, indent=2, default=str)
        if self.error:
            (folder / "error_report.txt").write_text(self.error, encoding="utf-8")
        if self.quality is not None and len(self.quality):
            self.quality.to_csv(folder / "quality.csv", index=False)
        if self.alignment is not None and len(self.alignment):
            self.alignment.to_csv(folder / "alignment.csv", index=False)
        if self.trace is not None:
            self.trace.to_csv(folder / "trace.csv", index=False)
        if self._raw_messages is not None:
            from .replay import _jsonable

            with open(folder / "messages.jsonl", "w", encoding="utf-8") as fh:
                for rec in self._raw_messages:
                    fh.write(json.dumps({**rec, "value": _jsonable(rec["value"])}))
                    fh.write("\n")
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
        quality = pd.read_csv(folder / "quality.csv") if (folder / "quality.csv").exists() else None
        alignment = pd.read_csv(folder / "alignment.csv") if (folder / "alignment.csv").exists() else None
        trace = pd.read_csv(folder / "trace.csv") if (folder / "trace.csv").exists() else None
        res = cls(df, events, timing, meta, err, units, quality=quality, alignment=alignment, trace=trace)
        if (folder / "messages.jsonl").exists():
            with open(folder / "messages.jsonl", "r", encoding="utf-8") as fh:
                raw = [json.loads(line) for line in fh if line.strip()]
            res._raw_messages = raw
            res.messages = _messages_frame(raw, meta)
        return res

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


def _messages_frame(raw: List[Dict[str, Any]], meta: Dict[str, Any]) -> pd.DataFrame:
    """Rebuild the messages table from ``messages.jsonl`` records."""
    from .messages import QUALITY_NAMES

    start = pd.Timestamp(meta.get("start")) if meta.get("start") else pd.Timestamp(0)
    step_s = float(meta.get("step_s") or 1.0)
    rows = []
    for r in raw:
        end = r.get("end_s")
        rows.append({
            "ref": r["ref"], "index": r["i"], "step": r["step"],
            "time": start + pd.Timedelta(seconds=r["step"] * step_s), "value": r["value"],
            "start": start + pd.Timedelta(seconds=r["start_s"]),
            "end": None if end is None else start + pd.Timedelta(seconds=end),
            "quality": QUALITY_NAMES.get(r.get("quality", 0), str(r.get("quality"))),
            "origin_step": r.get("origin", r["step"]),
        })
    return pd.DataFrame(rows, columns=["ref", "index", "step", "time", "value", "start", "end", "quality",
                                       "origin_step"])
