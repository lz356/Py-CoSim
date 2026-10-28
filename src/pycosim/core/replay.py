"""Record/replay of messages: repeat a run with non-deterministic components exactly.

A co-simulation created with ``CoSimulation(..., record=["llm"])`` stores
every message the listed components publish in ``messages.jsonl``. A later
simulation can call ``sim.replay("llm", "runs/first_run")``: the component is
replaced by a :class:`ReplayComponent` that publishes the recorded messages at
the recorded steps -- with the same values, validity intervals and origin --
instead of calling the original (an LLM, a web API, a stochastic model).

Because every other component sees exactly the same messages, a replay of all
non-deterministic components reproduces the original run bit for bit. Replaying
the decisions into a *modified* system (another building, another tariff) gives
a counterfactual in which the AI's randomness is held fixed.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .component import Component
from .errors import ConfigurationError
from .messages import QUALITY, Message

__all__ = ["ReplayComponent", "load_messages", "load_recording_meta", "write_messages"]


def _jsonable(value: Any) -> Any:
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def write_messages(path: Union[str, Path], bus, refs) -> Path:
    """Write the messages of ``refs`` as JSON lines (exact float round trip)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for ref in refs:
            sig = bus.signals[ref]
            for i in range(len(sig.ks)):
                fh.write(json.dumps({
                    "ref": ref, "i": i, "step": sig.ks[i], "value": _jsonable(sig.vs[i]),
                    "start_s": sig.a[i], "end_s": sig.b[i], "quality": sig.q[i], "origin": sig.origin[i],
                }) + "\n")
    return path


def load_messages(source: Any, component: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read recorded messages from a run folder, a ``messages.jsonl`` file or a :class:`Results`."""
    records: List[Dict[str, Any]] = []
    raw = getattr(source, "_raw_messages", None)
    if raw is not None:
        records = list(raw)
    else:
        path = Path(source)
        if path.is_dir():
            path = path / "messages.jsonl"
        if not path.exists():
            raise ConfigurationError(
                f"No recorded messages at {path}. Record them first with CoSimulation(..., record=[...]).")
        with open(path, "r", encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
    if component is not None:
        prefix = component + "."
        records = [r for r in records if r["ref"].startswith(prefix)]
    return records


def load_recording_meta(source: Any) -> Dict[str, Any]:
    """Clock of the recorded run (``start``, ``step_s``), if it can be found."""
    meta = getattr(source, "meta", None)
    if isinstance(meta, dict):
        return meta
    path = Path(source)
    folder = path if path.is_dir() else path.parent
    if (folder / "meta.json").exists():
        try:
            return json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


class ReplayComponent(Component):
    """Publishes recorded messages in place of the original component.

    It keeps the original's name and ports (so all connections stay valid),
    ignores its inputs, and publishes at every step at which the original
    published, with the recorded validity, origin and the quality flag
    ``replayed``. It always publishes at the beginning of a step (it behaves
    like a state-based component), which is equivalent for every consumer and
    means a replayed component can never be part of an algebraic loop.
    """

    def __init__(self, original: Component, records: List[Dict[str, Any]], expected_dt: Optional[float] = None):
        super().__init__(original.name)
        self.original_type = type(original).__name__
        self.input_ports = {k: copy.copy(v) for k, v in original.input_ports.items()}
        self.output_ports = {k: copy.copy(v) for k, v in original.output_ports.items()}
        self.original_feedthrough = bool(original.direct_feedthrough)
        self.direct_feedthrough = False
        self.step_size = None  # check every master step; publish only where the original did
        self.asynchronous = False
        prefix = original.name + "."
        by_step: Dict[int, List[Tuple[str, Any, float, Optional[float], int]]] = {}
        unknown = set()
        for r in records:
            port = r["ref"][len(prefix):]
            if port not in self.output_ports:
                unknown.add(port)
                continue
            by_step.setdefault(int(r["step"]), []).append(
                (port, r["value"], float(r["start_s"]), r.get("end_s"), int(r.get("origin", r["step"]))))
        if unknown:
            raise ConfigurationError(f"replay of {original.name!r}: recorded ports {sorted(unknown)} do not exist")
        if not by_step:
            raise ConfigurationError(f"No recorded messages for component {original.name!r}")
        self._by_step = by_step
        self.n_messages = sum(len(v) for v in by_step.values())

    def describe(self) -> str:
        return super().describe().replace("ReplayComponent", f"ReplayComponent of {self.original_type}", 1)

    def _emit(self):
        recs = self._by_step.get(self.ctx.step_index)
        if not recs:
            return None
        out = {}
        for port, value, start, end, origin in recs:
            out[port] = Message(value, start=start, end=end, quality=QUALITY["replayed"], origin=origin)
        return out

    def output(self, t):
        return self._emit()

    def update(self, t, inputs) -> None:
        return None

    def run_metadata(self) -> Dict[str, Any]:
        return {"replay_of": self.original_type, "messages": self.n_messages}
