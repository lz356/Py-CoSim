"""The data bus: a time-stamped store of every value exchanged in a co-simulation.

This is the successor of the "Simulation Management and Messaging Module"
(SMMM) of Py-CoSim 0.x, which exchanged data through one CSV file per
component and time step. The bus keeps everything in memory, indexed by the
master step, so that

* components can read the latest value of *any* port (not only connected ones),
* components can query the history of a port (e.g. lagged features for ML),
* the full exchange can be exported as one tidy table after (or during) the run,
* a structured event log records warnings, LLM prompts, errors, etc.
"""
from __future__ import annotations

import numbers
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

__all__ = ["DataBus"]

_MISSING = object()


class DataBus:
    """In-memory publish/subscribe store indexed by master step."""

    def __init__(self, times: Sequence[datetime]):
        self.times = list(times)
        self.k = -1
        self._latest: Dict[str, Any] = {}
        self._initial: Dict[str, Any] = {}
        self._series: Dict[str, Tuple[List[int], List[Any]]] = {}
        self._snapshot: Dict[str, Any] = {}
        self._snapshot_refs: Optional[set] = None
        self.events: List[Dict[str, Any]] = []
        self.units: Dict[str, Optional[str]] = {}

    # ------------------------------------------------------------ declaration
    def declare(self, ref: str, initial: Any = None, unit: Optional[str] = None) -> None:
        self._series[ref] = ([], [])
        self._initial[ref] = initial
        self.units[ref] = unit
        if initial is not None:
            self._latest[ref] = initial

    def set_initial(self, ref: str, value: Any) -> None:
        """Set the value a port has before its first publication (e.g. from ``initial_outputs()``)."""
        self._initial[ref] = value
        if value is not None:
            self._latest[ref] = value

    def track_previous(self, refs: Iterable[str]) -> None:
        """Only keep previous-step snapshots for refs read through delayed connections."""
        self._snapshot_refs = set(refs)

    @property
    def refs(self) -> List[str]:
        return list(self._series)

    # ---------------------------------------------------------------- runtime
    def begin_step(self, k: int) -> None:
        self.k = k
        if self._snapshot_refs is None:
            self._snapshot = dict(self._latest)
        elif self._snapshot_refs:
            latest = self._latest
            self._snapshot = {r: latest[r] for r in self._snapshot_refs if r in latest}

    def publish(self, ref: str, value: Any) -> None:
        try:
            ks, vs = self._series[ref]
        except KeyError:
            raise KeyError(f"Unknown port {ref!r} published to the data bus") from None
        self._latest[ref] = value
        if ks and ks[-1] == self.k:
            vs[-1] = value
        else:
            ks.append(self.k)
            vs.append(value)

    def latest(self, ref: str, default: Any = None) -> Any:
        return self._latest.get(ref, default)

    def previous(self, ref: str, default: Any = None) -> Any:
        """Value of ``ref`` at the end of the previous master step."""
        return self._snapshot.get(ref, default)

    def has_value(self, ref: str) -> bool:
        return ref in self._latest

    def history(self, ref: str, n: Optional[int] = None):
        """Published values of ``ref`` as a pandas Series indexed by time.

        Only the steps at which the producing component actually published are
        included (i.e. the series is at the producer's own rate).
        """
        import pandas as pd

        ks, vs = self._series[ref]
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
        for ref in refs if refs is not None else self._series:
            ks, vs = self._series[ref]
            columns[ref] = _forward_fill(ks, vs, n, self._initial.get(ref))
        return pd.DataFrame(columns, index=index)


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
