"""Connections between ports and the dependency analysis of the master algorithm.

The master executes, at every time step,

1. ``output()`` of all *non-feedthrough* components (their outputs depend on
   state only, so they are available immediately),
2. ``step()`` of all *feedthrough* components in a topological order of the
   instantaneous data-flow graph,
3. ``update()`` of all non-feedthrough components (advancing them to the next
   step -- for EnergyPlus this happens in parallel in worker processes).

Only connections that are *not* delayed and that point from a feedthrough
component into another feedthrough component impose an ordering constraint.
A cycle among such constraints is an algebraic loop and is reported with the
offending path.
"""
from __future__ import annotations

import heapq
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from .component import Component, PortRef
from .errors import AlgebraicLoopError, ConfigurationError

__all__ = ["Connection", "build_execution_order", "resolve_reduce"]

_REDUCERS: Dict[str, Callable[[Sequence[Any]], Any]] = {
    "sum": lambda xs: sum(x for x in xs if x is not None),
    "mean": lambda xs: statistics.fmean([x for x in xs if x is not None]) if any(x is not None for x in xs) else None,
    "max": lambda xs: max((x for x in xs if x is not None), default=None),
    "min": lambda xs: min((x for x in xs if x is not None), default=None),
    "list": list,
}


def resolve_reduce(reduce: Any) -> Optional[Callable[[Sequence[Any]], Any]]:
    if reduce is None or callable(reduce):
        return reduce
    try:
        return _REDUCERS[str(reduce)]
    except KeyError:
        raise ConfigurationError(f"Unknown reduce {reduce!r}; use one of {sorted(_REDUCERS)} or a callable") from None


@dataclass
class Connection:
    """A data link from one or more output ports to one input port.

    Attributes
    ----------
    sources : list of PortRef
        Output ports feeding the target. More than one source requires
        ``reduce`` (e.g. ``"sum"`` to aggregate building loads).
    target : PortRef
        Input port receiving the value.
    delay : bool
        If True the target reads the value the source had at the end of the
        *previous* master step (a time-shifted, Jacobi-type coupling). Use it to
        break algebraic loops.
    transform : callable, optional
        Applied to the (reduced) value, e.g. ``lambda w: w / 1000`` for W -> kW.
    reduce : callable, optional
        Combines the values of several sources into one.
    """

    sources: List[PortRef]
    target: PortRef
    delay: bool = False
    transform: Optional[Callable[[Any], Any]] = None
    reduce: Optional[Callable[[Sequence[Any]], Any]] = None
    label: str = field(default="", compare=False)
    #: How the target reads the sources (overrides the input port's ``read``).
    read: Optional[str] = None
    #: Source names, topics or glob patterns resolved when the simulation is initialized.
    patterns: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        names = [str(s) for s in self.sources] if self.sources else list(self.patterns)
        if not self.sources and not names:
            names = ["?"]
        src = ", ".join(names)
        if len(names) > 1:
            src = f"[{src}]"
        extra = " (delayed)" if self.delay else ""
        if self.read and self.read != "latest":
            extra += f" (read={self.read})"
        return f"{src} -> {self.target}{extra}"


def build_execution_order(components: Sequence[Component], connections: Sequence[Connection],
                          instantaneous: Optional[Callable[[Connection, PortRef], bool]] = None) -> List[Component]:
    """Topologically sort feedthrough components (Kahn's algorithm).

    Ties are broken by ``(priority, insertion order)`` so the schedule is
    deterministic and controllable. ``instantaneous(conn, source)`` may say
    that a link does not need the source's value of the *current* step (e.g. a
    ``read="past"`` link to a value that only becomes valid at ``t``), in
    which case it imposes no ordering.
    """
    feed = [c for c in components if c.direct_feedthrough]
    index = {c.name: i for i, c in enumerate(components)}
    by_name = {c.name: c for c in feed}
    delayed_out = {c.name for c in feed if getattr(c, "asynchronous", False)}  # outputs appear in a later step
    succ: Dict[str, set] = {c.name: set() for c in feed}
    indeg: Dict[str, int] = {c.name: 0 for c in feed}
    for conn in connections:
        if conn.delay:
            continue
        tgt = conn.target.component
        if tgt not in by_name:
            continue
        for src in conn.sources:
            if src.component not in by_name or src.component in delayed_out:
                continue  # non-feedthrough and asynchronous sources are available before phase 2
            if instantaneous is not None and not instantaneous(conn, src):
                continue
            if src.component == tgt:
                raise AlgebraicLoopError([tgt])
            if tgt not in succ[src.component]:
                succ[src.component].add(tgt)
                indeg[tgt] += 1
    heap = [(by_name[n].priority, index[n], n) for n, d in indeg.items() if d == 0]
    heapq.heapify(heap)
    order: List[Component] = []
    while heap:
        _, _, name = heapq.heappop(heap)
        order.append(by_name[name])
        for nxt in sorted(succ[name], key=lambda n: index[n]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                heapq.heappush(heap, (by_name[nxt].priority, index[nxt], nxt))
    if len(order) != len(feed):
        remaining = {n for n, d in indeg.items() if d > 0}
        raise AlgebraicLoopError(_find_cycle(remaining, succ))
    return order


def _find_cycle(nodes: set, succ: Dict[str, set]) -> List[str]:
    """Return one cycle among ``nodes`` for a helpful error message."""
    color: Dict[str, int] = {}
    stack: List[str] = []

    def dfs(n: str) -> Optional[List[str]]:
        color[n] = 1
        stack.append(n)
        for m in sorted(succ[n]):
            if m not in nodes:
                continue
            if color.get(m) == 1:
                return stack[stack.index(m):]
            if m not in color:
                found = dfs(m)
                if found:
                    return found
        stack.pop()
        color[n] = 2
        return None

    for n in sorted(nodes):
        if n not in color:
            cycle = dfs(n)
            if cycle:
                return cycle
    return sorted(nodes)
