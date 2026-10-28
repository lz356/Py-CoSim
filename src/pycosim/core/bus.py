"""The messaging part of TIDE: a time-aware, in-memory store of every message.

TIDE (Time-aware Interval Data Exchange) is the hub through which all
components exchange data. This module keeps every *message* in memory (see
:mod:`pycosim.core.messages`): each published value keeps its validity
interval, its quality flag and the step that produced it, so that

* components can read the latest value of *any* port (not only connected ones),
* consumers can ask for a value *over their own time step* and get it aligned
  (time-weighted means, totals, sample-and-hold) whatever rate the producer
  runs at,
* components can query the history of a port (e.g. lagged features for ML),
* the full exchange can be exported as a tidy table, recorded and replayed,
* a structured event log records warnings, LLM prompts, errors, etc.

The management part of TIDE -- the clock, the per-step barrier and the
execution order -- is :class:`pycosim.CoSimulation`.
"""
from __future__ import annotations

import fnmatch
import numbers
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .messages import MISSING, QUALITY_NAMES, Signal

__all__ = ["DataBus"]

_MISSING = MISSING


class DataBus:
    """In-memory publish/subscribe store of messages, indexed by master step and time."""

    def __init__(self, times: Sequence[datetime], dt: Optional[float] = None):
        self.times = list(times)
        if dt is None:
            dt = (self.times[1] - self.times[0]).total_seconds() if len(self.times) > 1 else 1.0
        self.dt = float(dt)
        self.start = self.times[0] if self.times else None
        self.k = -1
        self._latest: Dict[str, Any] = {}
        self._initial: Dict[str, Any] = {}
        self.signals: Dict[str, Signal] = {}
        self._snapshot: Dict[str, Any] = {}
        self._snapshot_refs: Optional[set] = None
        self.events: List[Dict[str, Any]] = []
        self.units: Dict[str, Optional[str]] = {}
        self.topics: Dict[str, str] = {}  # topic alias -> "component.port"

    # ------------------------------------------------------------ declaration
    def declare(self, ref: str, initial: Any = None, unit: Optional[str] = None, kind: Optional[str] = None,
                interval: Optional[str] = None, step_s: Optional[float] = None, topic: Optional[str] = None) -> None:
        self.signals[ref] = Signal(ref, kind, interval, step_s if step_s is not None else self.dt, initial)
        self._initial[ref] = initial
        self.units[ref] = unit
        if initial is not None:
            self._latest[ref] = initial
        if topic:
            if topic in self.topics and self.topics[topic] != ref:
                raise ValueError(f"topic {topic!r} is published by both {self.topics[topic]} and {ref}")
            self.topics[topic] = ref

    def set_initial(self, ref: str, value: Any) -> None:
        """Set the value a port has before its first publication (e.g. from ``initial_outputs()``)."""
        self._initial[ref] = value
        self.signals[ref].initial = value
        if value is not None:
            self._latest[ref] = value

    def track_previous(self, refs: Iterable[str]) -> None:
        """Only keep previous-step snapshots for refs read through delayed connections."""
        self._snapshot_refs = set(refs)

    @property
    def refs(self) -> List[str]:
        return list(self.signals)

    def resolve(self, pattern: str) -> List[str]:
        """Port references (``component.port``) matching a name, topic or glob pattern."""
        if pattern in self.signals:
            return [pattern]
        if pattern in self.topics:
            return [self.topics[pattern]]
        hits = [r for r in self.signals if fnmatch.fnmatchcase(r, pattern)]
        hits += [ref for topic, ref in self.topics.items() if fnmatch.fnmatchcase(topic, pattern) and ref not in hits]
        return hits

    # ---------------------------------------------------------------- time
    def seconds(self, when: Any) -> float:
        """Seconds since the simulation start for a datetime (floats pass through)."""
        if isinstance(when, datetime):
            return (when - self.start).total_seconds()
        return float(when)

    def time_at(self, s: float) -> datetime:
        from datetime import timedelta

        return self.start + timedelta(seconds=s)

    # ---------------------------------------------------------------- runtime
    def begin_step(self, k: int) -> None:
        self.k = k
        if self._snapshot_refs is None:
            self._snapshot = dict(self._latest)
        elif self._snapshot_refs:
            latest = self._latest
            self._snapshot = {r: latest[r] for r in self._snapshot_refs if r in latest}

    def publish(self, ref: str, value: Any, start: Any = None, end: Any = None, quality: int = 0,
                origin: Optional[int] = None) -> int:
        """Store a message on ``ref``; returns its index in the port's message list."""
        try:
            sig = self.signals[ref]
        except KeyError:
            raise KeyError(f"Unknown port {ref!r} published to the data bus") from None
        if start is None:
            a, b = sig.default_span(self.k * self.dt)
        else:
            a = self.seconds(start)
            b = self.seconds(end) if end is not None else (
                a + sig.step_s if sig.kind in ("average", "amount") else None)
        self._latest[ref] = value
        return sig.append(self.k, value, a, b, quality, origin)

    def latest(self, ref: str, default: Any = None) -> Any:
        return self._latest.get(ref, default)

    def previous(self, ref: str, default: Any = None) -> Any:
        """Value of ``ref`` at the end of the previous master step."""
        return self._snapshot.get(ref, default)

    def has_value(self, ref: str) -> bool:
        return ref in self._latest

    def value_at_step(self, ref: str, k: int, default: Any = None) -> Any:
        """Latest value of ``ref`` as it was at the end of master step ``k``."""
        sig = self.signals[ref]
        i = sig.index_at_step(k)
        if i < 0:
            init = self._initial.get(ref)
            return default if init is None else init
        return sig.vs[i]

    def at(self, ref: str, when: Any, upto_step: Optional[int] = None) -> Any:
        """Value of ``ref`` in effect at ``when`` (datetime or seconds since start), aligned by its kind."""
        sig = self.signals[ref]
        upto = sig.index_at_step(upto_step) if upto_step is not None else None
        value, _cov, _ids = sig.at(self.seconds(when), upto)
        return None if value is _MISSING else value

    def window(self, ref: str, start: Any, end: Any, upto_step: Optional[int] = None) -> Any:
        """Aggregate of ``ref`` over ``[start, end)`` (mean, total or list depending on its kind)."""
        sig = self.signals[ref]
        upto = sig.index_at_step(upto_step) if upto_step is not None else None
        value, _cov, _ids = sig.window(self.seconds(start), self.seconds(end), upto)
        return None if value is _MISSING else value

    def history(self, ref: str, n: Optional[int] = None, upto_step: Optional[int] = None):
        """Published values of ``ref`` as a pandas Series indexed by time.

        Only the steps at which the producing component actually published are
        included (i.e. the series is at the producer's own rate).
        """
        import pandas as pd

        sig = self.signals[ref]
        ks, vs = sig.ks, sig.vs
        if upto_step is not None:
            i = sig.index_at_step(upto_step) + 1
            ks, vs = ks[:i], vs[:i]
        if n is not None:
            if n <= 0:
                ks, vs = [], []
            else:
                ks, vs = ks[-n:], vs[-n:]
        index = pd.DatetimeIndex([self.times[k] for k in ks], name="time")
        return pd.Series(list(vs), index=index, name=ref)

    def log_event(self, time: Optional[datetime], source: str, kind: str, message: str, **data: Any) -> None:
        self.events.append({"time": time, "source": source, "kind": kind, "message": message, **data})

    # ----------------------------------------------------------------- export
    def to_frame(self, refs: Optional[Iterable[str]] = None, upto: Optional[int] = None):
        """Return a DataFrame on the master time grid (zero-order hold between publications)."""
        import pandas as pd

        n = (self.k + 1) if upto is None else upto
        n = max(n, 0)
        index = pd.DatetimeIndex(self.times[:n], name="time")
        columns: Dict[str, Any] = {}
        for ref in refs if refs is not None else self.signals:
            sig = self.signals[ref]
            columns[ref] = _forward_fill(sig.ks, sig.vs, n, self._initial.get(ref))
        return pd.DataFrame(columns, index=index)

    def messages_frame(self, refs: Optional[Iterable[str]] = None):
        """All messages as a long table (one row per message)."""
        import pandas as pd

        rows = []
        start = self.start
        from datetime import timedelta

        for ref in refs if refs is not None else self.signals:
            sig = self.signals[ref]
            for i in range(len(sig.ks)):
                b = sig.b[i]
                rows.append({
                    "ref": ref, "index": i, "step": sig.ks[i], "time": self.times[sig.ks[i]],
                    "value": sig.vs[i],
                    "start": start + timedelta(seconds=sig.a[i]),
                    "end": None if b is None else start + timedelta(seconds=b),
                    "quality": QUALITY_NAMES.get(sig.q[i], str(sig.q[i])),
                    "origin_step": sig.origin[i],
                })
        cols = ["ref", "index", "step", "time", "value", "start", "end", "quality", "origin_step"]
        return pd.DataFrame(rows, columns=cols)

    def quality_counts(self) -> List[Dict[str, Any]]:
        """Number of messages per port and quality flag (only ports with non-ok messages)."""
        out = []
        for ref, sig in self.signals.items():
            if not any(sig.q):
                continue
            counts: Dict[int, int] = {}
            for q in sig.q:
                counts[q] = counts.get(q, 0) + 1
            for q, c in sorted(counts.items()):
                out.append({"ref": ref, "quality": QUALITY_NAMES.get(q, str(q)), "messages": c})
        return out


def _is_number(v: Any) -> bool:
    return v is None or (isinstance(v, (numbers.Number, np.bool_)) and not isinstance(v, complex))


def _forward_fill(ks: List[int], vs: List[Any], n: int, initial: Any):
    numeric = all(_is_number(v) for v in vs) and _is_number(initial)
    if numeric:
        fill = np.nan if initial is None else float(initial)
        if not ks:
            return np.full(n, fill)
        idx = np.asarray(ks, dtype=int)
        vals = np.array([np.nan if v is None else float(v) for v in vs])
        keep = idx < n
        idx, vals = idx[keep], vals[keep]
        out = np.full(n, np.nan)
        out[idx] = vals
        # index of the most recent publication at or before each step (-1 = none yet)
        marker = np.full(n, -1)
        marker[idx] = idx
        last = np.maximum.accumulate(marker)
        return np.where(last >= 0, out[np.clip(last, 0, None)], fill)
    out_obj: List[Any] = [initial] * n
    current = initial
    j = 0
    for i in range(n):
        while j < len(ks) and ks[j] == i:
            current = vs[j]
            j += 1
        out_obj[i] = current
    return out_obj
