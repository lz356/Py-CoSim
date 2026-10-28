"""Messages and their time meaning -- the data model of TIDE.

Every value a component publishes is a *message*: a value plus the time span
it is valid for and a quality flag. What the time span means depends on the
*kind* of the output port:

``instant``
    A sample of a continuous quantity at one moment (a zone temperature, an
    outdoor temperature). Between samples the signal is linearly interpolated.
``average``
    The mean of a rate over an interval ``[start, end)`` (electric power
    averaged over a time step, as EnergyPlus reports it).
``amount``
    A total over an interval ``[start, end)`` (energy in J or kWh).
``hold``
    A value that is valid from ``start`` until it is replaced (a set-point, a
    price, a command).
``event``
    Something that happened at one moment (a demand-response notice).

Untyped ports (``kind=None``) behave like ``hold`` -- this is how Py-CoSim
2.0 treated every value.

Consumers say what they need relative to their own step ``[t, t + h)``:

``latest``  the most recent message, whatever it means (fast; the default);
``at``      the value in effect at ``t``;
``past``    the aggregate over the step that just ended, ``[t - h, t)``;
``ahead``   the aggregate over the step that starts, ``[t, t + h)``.

:class:`Signal` stores the messages of one port and answers these queries:
time-weighted means for ``instant``/``average``/``hold`` signals, totals
(proportional splitting) for ``amount`` signals and lists for ``event``
signals. Each answer also reports the *coverage*: the share of the requested
window that published messages actually cover (the rest is extrapolated by
holding the last value).
"""
from __future__ import annotations

import math
import numbers
from bisect import bisect_left, bisect_right, insort
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Tuple, Union

__all__ = [
    "KINDS", "READS", "INTERVALS", "QUALITY", "QUALITY_NAMES",
    "Message", "Signal", "quality_name",
]

KINDS = ("instant", "average", "amount", "hold", "event")
READS = ("latest", "at", "past", "ahead")
INTERVALS = ("past", "ahead")

#: Quality flags carried by every message.
QUALITY = {"ok": 0, "clipped": 1, "fallback": 2, "late": 3, "replayed": 4}
QUALITY_NAMES = {v: k for k, v in QUALITY.items()}

_MISSING = object()


def quality_name(code: int) -> str:
    return QUALITY_NAMES.get(int(code), str(code))


@dataclass
class Message:
    """Explicit message envelope a component may return instead of a bare value.

    ``start``/``end`` are datetimes (or seconds since the simulation start).
    Use it to publish values whose validity differs from the default of the
    port, e.g. a price announced for a future hour::

        return {"price": pc.Message(0.42, start=t + timedelta(hours=2))}
    """

    value: Any
    start: Union[datetime, float, None] = None
    end: Union[datetime, float, None] = None
    quality: Union[str, int, None] = None
    origin: Optional[int] = None  # master step whose inputs produced the value (set by TIDE)


def _num(v: Any) -> Optional[float]:
    """Return ``v`` as a finite float, or None if it is not a usable number."""
    tv = type(v)
    if tv is float:
        return v if v - v == 0.0 else None  # False for inf and nan
    if tv is int:
        return float(v)
    if v is None or isinstance(v, bool):
        return None if v is None else float(v)
    if isinstance(v, numbers.Number) and not isinstance(v, complex):
        f = float(v)
        return f if math.isfinite(f) else None
    try:  # numpy scalars that are not registered numbers
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class Signal:
    """All messages published on one port, with time-aware queries.

    Times are seconds since the start of the co-simulation.
    """

    __slots__ = ("ref", "kind", "interval", "step_s", "initial", "ks", "vs", "fv", "a", "b", "q", "origin",
                 "_order", "_monotonic")

    def __init__(self, ref: str, kind: Optional[str], interval: Optional[str], step_s: float, initial: Any):
        self.ref = ref
        self.kind = kind
        self.interval = interval or "past"
        self.step_s = float(step_s)
        self.initial = initial
        self.ks: List[int] = []          # master step of publication
        self.vs: List[Any] = []          # values
        self.fv: List[Optional[float]] = []  # values as finite floats (None if not numeric)
        self.a: List[float] = []         # start of validity
        self.b: List[Optional[float]] = []  # end of validity (None = until replaced / a point)
        self.q: List[int] = []           # quality codes
        self.origin: List[int] = []      # step whose inputs produced the message (async components)
        self._order: Optional[List[Tuple[float, int]]] = None  # (start, index) when starts are not monotonic
        self._monotonic = True

    # ------------------------------------------------------------ publishing
    def default_span(self, s: float) -> Tuple[float, Optional[float]]:
        """Validity of a message published at time ``s`` without an explicit envelope."""
        if self.kind in ("average", "amount"):
            if self.interval == "ahead":
                return s, s + self.step_s
            return s - self.step_s, s
        return s, None

    def append(self, k: int, value: Any, start: float, end: Optional[float], quality: int = 0,
               origin: Optional[int] = None) -> int:
        """Store a message; a second publication in the same step replaces the first. Returns its index."""
        ks = self.ks
        origin = k if origin is None else origin
        if ks and ks[-1] == k and self.a[-1] == start:
            i = len(ks) - 1
            self.vs[i], self.b[i], self.q[i], self.origin[i] = value, end, quality, origin
            self.fv[i] = _num(value)
            return i
        i = len(ks)
        if self._monotonic and self.a and start < self.a[-1]:
            self._monotonic = False
            self._order = [(s, j) for j, s in enumerate(self.a)]
        ks.append(k)
        self.vs.append(value)
        self.fv.append(_num(value))
        self.a.append(start)
        self.b.append(end)
        self.q.append(quality)
        self.origin.append(origin)
        if not self._monotonic:
            insort(self._order, (start, i))
        return i

    # ---------------------------------------------------------------- lookup
    def __len__(self) -> int:
        return len(self.ks)

    def latest_index(self) -> int:
        return len(self.ks) - 1

    def index_at_step(self, k: int) -> int:
        """Index of the last message published at or before master step ``k`` (-1 if none)."""
        return bisect_right(self.ks, k) - 1

    def _sorted(self) -> Tuple[List[float], List[int]]:
        """Starts in ascending order and the matching message indices."""
        if self._monotonic:
            return self.a, range(len(self.a))  # type: ignore[return-value]
        order = self._order or []
        return [s for s, _ in order], [j for _, j in order]

    def _last_start_le(self, s: float, upto: Optional[int] = None) -> int:
        """Message index with the largest start <= s (latest published on ties); -1 if none."""
        starts, idx = self._sorted()
        pos = bisect_right(starts, s) - 1
        while pos >= 0:
            j = idx[pos]
            if upto is None or j <= upto:
                return j
            pos -= 1
        return -1

    # --------------------------------------------------------------- queries
    def at(self, s: float, upto: Optional[int] = None) -> Tuple[Any, float, List[int]]:
        """Value in effect at time ``s``. Returns ``(value, coverage, message indices)``.

        ``upto`` restricts the query to messages with index <= upto (used for
        asynchronous components that read the state of an earlier step).
        """
        kind = self.kind
        if kind == "event":
            starts, idx = self._sorted()
            lo, hi = bisect_left(starts, s), bisect_right(starts, s)
            hits = [idx[p] for p in range(lo, hi) if upto is None or idx[p] <= upto]
            return [self.vs[j] for j in hits], 1.0, hits
        if kind == "instant":
            return self._instant_at(s, upto)
        if kind in ("average", "amount"):
            j = self._last_start_le(s, upto)
            # the interval containing s, else the last interval before s (held)
            if j >= 0:
                end = self.b[j]
                covered = end is None or s < end
                return self.vs[j], (1.0 if covered else 0.0), [j]
            return self._initial_or_missing()
        j = self._last_start_le(s, upto)
        if j >= 0:
            return self.vs[j], 1.0, [j]
        return self._initial_or_missing(full=True)

    def window(self, w0: float, w1: float, upto: Optional[int] = None) -> Tuple[Any, float, List[int]]:
        """Aggregate over ``[w0, w1)``. Returns ``(value, coverage, message indices)``.

        ``instant``/``average``/``hold`` (and untyped) signals return the
        time-weighted mean, ``amount`` signals the total, ``event`` signals the
        list of values.
        """
        if w1 <= w0:
            return self.at(w0, upto)
        kind = self.kind
        if kind == "event":
            starts, idx = self._sorted()
            lo, hi = bisect_left(starts, w0), bisect_left(starts, w1)
            hits = [idx[p] for p in range(lo, hi) if upto is None or idx[p] <= upto]
            return [self.vs[j] for j in hits], 1.0, hits
        if kind == "instant":
            return self._instant_window(w0, w1, upto)
        if kind in ("average", "amount"):
            return self._interval_window(w0, w1, upto, amount=(kind == "amount"))
        return self._hold_window(w0, w1, upto)

    # ------------------------------------------------------------- internals
    def _initial_or_missing(self, full: bool = False):
        if self.initial is not None:
            return self.initial, (1.0 if full else 0.0), []
        return _MISSING, 0.0, []

    def _instant_at(self, s: float, upto: Optional[int]):
        starts, idx = self._sorted()
        pos = bisect_right(starts, s) - 1
        if upto is not None:
            while pos >= 0 and idx[pos] > upto:
                pos -= 1
        if pos < 0:
            return self._initial_or_missing()
        j = idx[pos]
        if starts[pos] == s:
            return self.vs[j], 1.0, [j]
        nxt = pos + 1
        while nxt < len(starts) and upto is not None and idx[nxt] > upto:
            nxt += 1
        if nxt < len(starts):
            j2 = idx[nxt]
            v1, v2 = self.fv[j], self.fv[j2]
            if v1 is not None and v2 is not None:
                frac = (s - starts[pos]) / (starts[nxt] - starts[pos])
                return v1 + frac * (v2 - v1), 1.0, [j, j2]
            return self.vs[j], 1.0, [j]
        return self.vs[j], 0.0, [j]  # after the last sample: held

    def _instant_window(self, w0: float, w1: float, upto: Optional[int]):
        """Mean of the piecewise-linear interpolant over [w0, w1)."""
        starts, idx = self._sorted()
        if upto is not None:
            pairs = [(s, j) for s, j in zip(starts, idx) if j <= upto]
            starts, idx = [p[0] for p in pairs], [p[1] for p in pairs]
        if not starts:
            return self._initial_or_missing()
        lo = bisect_right(starts, w0)
        hi = bisect_left(starts, w1)
        pts: List[Tuple[float, float]] = []
        used: List[int] = []
        v0, _c, ids0 = self._instant_at_lists(w0, starts, idx)
        v1, _c, ids1 = self._instant_at_lists(w1, starts, idx)
        f0, f1 = _num(v0), _num(v1)
        if f0 is None or f1 is None:
            return v1 if v1 is not _MISSING else v0, 0.0, ids1 or ids0
        pts.append((w0, f0))
        used.extend(ids0)
        for p in range(lo, hi):
            f = self.fv[idx[p]]
            if f is not None and w0 < starts[p] < w1:
                pts.append((starts[p], f))
                used.append(idx[p])
        pts.append((w1, f1))
        used.extend(ids1)
        area = 0.0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            area += 0.5 * (y0 + y1) * (x1 - x0)
        first, last = starts[0], starts[-1]
        covered = max(0.0, min(w1, last) - max(w0, first))
        return area / (w1 - w0), covered / (w1 - w0), sorted(set(used))

    def _instant_at_lists(self, s: float, starts: List[float], idx: List[int]):
        pos = bisect_right(starts, s) - 1
        if pos < 0:
            if self.initial is not None:
                return self.initial, 0.0, []
            return self.vs[idx[0]], 0.0, [idx[0]]  # before the first sample: hold it backwards
        j = idx[pos]
        if starts[pos] == s or pos + 1 >= len(starts):
            return self.vs[j], 1.0, [j]
        j2 = idx[pos + 1]
        v1, v2 = self.fv[j], self.fv[j2]
        if v1 is None or v2 is None:
            return self.vs[j], 1.0, [j]
        frac = (s - starts[pos]) / (starts[pos + 1] - starts[pos])
        return v1 + frac * (v2 - v1), 1.0, [j, j2]

    def _hold_window(self, w0: float, w1: float, upto: Optional[int]):
        """Time-weighted mean of a piecewise-constant signal over [w0, w1).

        Before the first message the signal is undefined (unless the port has
        an initial value); the mean is then taken over the defined part and the
        coverage reports the defined share of the window.
        """
        starts, idx = self._sorted()
        pos = bisect_right(starts, w0) - 1
        if upto is not None:
            while pos >= 0 and idx[pos] > upto:
                pos -= 1
        segments: List[Tuple[float, Any, Optional[int]]] = []  # (from, value, index)
        if pos >= 0:
            segments.append((w0, self.vs[idx[pos]], idx[pos]))
        elif self.initial is not None:
            segments.append((w0, self.initial, None))
        p = pos + 1
        while p < len(starts) and starts[p] < w1:
            j = idx[p]
            p += 1
            if upto is not None and j > upto:
                continue
            x = max(starts[p - 1], w0)
            if segments and segments[-1][0] == x:
                segments[-1] = (x, self.vs[j], j)  # replaced at the same instant
            else:
                segments.append((x, self.vs[j], j))
        if not segments:
            return _MISSING, 0.0, []
        used = [j for _, _, j in segments if j is not None]
        fv = self.fv
        values = [(fv[j] if j is not None else _num(v)) for _, v, j in segments]
        if any(v is None for v in values):
            return segments[-1][1], 1.0, used  # non-numeric: the value in effect at the end of the window
        area = defined = 0.0
        for n, (x, _v, _j) in enumerate(segments):
            x_end = segments[n + 1][0] if n + 1 < len(segments) else w1
            area += values[n] * (x_end - x)
            defined += x_end - x
        if defined <= 0.0:
            return segments[-1][1], 0.0, used
        return area / defined, defined / (w1 - w0), used

    def _interval_window(self, w0: float, w1: float, upto: Optional[int], amount: bool):
        """Aggregate interval messages ([start, end) holding a mean or a total) over [w0, w1).

        Parts of the window no message covers yet are filled by holding the
        most recent rate (or, at the very beginning, the first one / the
        initial value). The coverage reports the share covered by messages.
        """
        starts, idx = self._sorted()
        pos = max(bisect_right(starts, w0) - 1, 0)
        pieces: List[Tuple[float, float, float, int]] = []  # (lo, hi, rate, index)
        prev_rate: Optional[float] = None  # rate of the last message ending at or before w0
        p = pos
        # step back over messages that start before w0 but still overlap it (variable interval lengths)
        while p > 0 and (self.b[idx[p - 1]] or 0.0) > w0:
            p -= 1
        n = len(starts)
        bs, fv, step = self.b, self.fv, self.step_s
        while p < n:
            a = starts[p]
            if a >= w1:
                break
            j = idx[p]
            p += 1
            if upto is not None and j > upto:
                continue
            b = bs[j]
            if b is None:
                b = a + step
            v = fv[j]
            if v is None:
                continue
            rate = v / (b - a) if (amount and b > a) else v
            if b <= w0:
                prev_rate = rate
                continue
            lo = a if a > w0 else w0
            hi = b if b < w1 else w1
            if hi > lo:
                pieces.append((lo, hi, rate, j))
        if not pieces:
            if prev_rate is None:
                init = _num(self.initial)
                if init is None:
                    return _MISSING, 0.0, []
                return (init * (w1 - w0) if amount else init), 0.0, []
            return (prev_rate * (w1 - w0) if amount else prev_rate), 0.0, []
        area = covered = 0.0
        cursor = w0
        fill = prev_rate if prev_rate is not None else (_num(self.initial) if self.initial is not None else None)
        for lo, hi, rate, _j in pieces:
            if lo > cursor:  # gap: hold the rate known before it (or the next one at the very start)
                area += (fill if fill is not None else rate) * (lo - cursor)
            if hi > cursor:
                start = max(lo, cursor)
                area += rate * (hi - start)
                covered += hi - start
                cursor = hi
            fill = rate
        if cursor < w1:
            area += fill * (w1 - cursor)
        width = w1 - w0
        used = [j for _, _, _, j in pieces]
        if amount:
            return area, covered / width, used
        return area / width, covered / width, used


MISSING = _MISSING
