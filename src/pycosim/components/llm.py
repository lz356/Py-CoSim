"""Large-language-model (LLM) supervisory control with safety guardrails.

:class:`LLMSupervisor` turns a natural-language *intent* ("This is a nursing
home; prioritise comfort", "minimise cost during the evening peak") plus live
co-simulation measurements into set-points for other components. Because LLM
output is untrusted, every response passes through a guardrail layer:

1. **parse** -- extract the first JSON object from the text;
2. **validate** -- keep only declared set-points, require finite numbers;
3. **bound** -- clip to ``[min, max]`` of each set-point;
4. **rate-limit** -- limit the change per decision (``max_change``);
5. **fallback** -- on timeout, refusal or invalid output, hold the last valid
   value (or the default), and record why.

Every prompt, raw response, parsed value and applied value is written to the
co-simulation event log, so LLM-in-the-loop experiments are auditable, and
:class:`RecordingBackend` / :class:`ReplayBackend` make them reproducible.

Backends
--------
* :class:`AnthropicBackend` -- Claude models via the official ``anthropic`` SDK.
* :class:`OpenAIBackend` -- OpenAI or any OpenAI-compatible server (vLLM,
  LM Studio, ...), plain HTTP.
* :class:`OllamaBackend` -- local models served by Ollama, plain HTTP.
* :class:`OfflineStandInBackend` -- deterministic keyword rules, **not an
  LLM**; lets the pipeline run offline (tests, CI, demos without API keys).
* :class:`FaultInjectionBackend` -- wraps any backend and injects malformed,
  out-of-range, refused or timed-out responses to test the guardrails.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from ..core.component import Component

__all__ = [
    "LLMSupervisor",
    "LLMBackend",
    "AnthropicBackend",
    "OpenAIBackend",
    "OllamaBackend",
    "OfflineStandInBackend",
    "StochasticStandInBackend",
    "FaultInjectionBackend",
    "RecordingBackend",
    "ReplayBackend",
    "extract_json",
]


class LLMRefusal(RuntimeError):
    """The model declined to answer."""


class ReplayMismatchError(LookupError):
    """A strict replay met a prompt that is not in the recording (the scenario changed)."""


class ReplayedError(RuntimeError):
    """An error recorded by :class:`RecordingBackend`, raised again during replay."""


# ======================================================================= backends
class LLMBackend:
    """Interface: ``complete(system, prompt) -> str``."""

    name = "backend"

    def complete(self, system: str, prompt: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: float) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-configured endpoint
        return json.loads(resp.read().decode("utf-8"))


class AnthropicBackend(LLMBackend):
    """Claude via the official Anthropic Python SDK (``pip install anthropic``).

    Credentials are resolved by the SDK (``ANTHROPIC_API_KEY`` or an
    ``ant auth login`` profile). ``server_fallback=True`` asks the API to
    re-run a refused request on a fallback model (beta feature).
    """

    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5-5", max_tokens: int = 16000, effort: Optional[str] = None,
                 server_fallback: bool = True, client: Any = None):
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError("AnthropicBackend needs the 'anthropic' package: pip install anthropic") from exc
        import anthropic

        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.server_fallback = server_fallback
        self.client = client or anthropic.Anthropic()

    def complete(self, system: str, prompt: str) -> str:
        kwargs: Dict[str, Any] = dict(model=self.model, max_tokens=self.max_tokens, system=system,
                                      messages=[{"role": "user", "content": prompt}])
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.server_fallback:
            response = self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            response = self.client.messages.create(**kwargs)
        self.last_model = getattr(response, "model", self.model)
        if response.stop_reason == "refusal":
            raise LLMRefusal(getattr(response.stop_details, "category", None) or "refusal")
        if response.stop_reason == "max_tokens":
            raise RuntimeError(f"response truncated at max_tokens={self.max_tokens}; increase max_tokens")
        return "".join(block.text for block in response.content if block.type == "text")

    def describe(self) -> str:
        return f"anthropic:{self.model}"


class OpenAIBackend(LLMBackend):
    """OpenAI Chat Completions or any OpenAI-compatible endpoint (plain HTTP).

    The key is read from ``api_key`` or the ``OPENAI_API_KEY`` environment
    variable; set ``base_url`` for self-hosted servers.
    """

    name = "openai"

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1", api_key: Optional[str] = None,
                 timeout: float = 60.0, extra: Optional[Dict[str, Any]] = None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.timeout = timeout
        self.extra = extra or {}

    def complete(self, system: str, prompt: str) -> str:
        payload = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                     {"role": "user", "content": prompt}], **self.extra}
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        out = _post_json(f"{self.base_url}/chat/completions", payload, headers, self.timeout)
        return out["choices"][0]["message"]["content"] or ""

    def describe(self) -> str:
        return f"openai:{self.model}@{self.base_url}"


class OllamaBackend(LLMBackend):
    """Local models served by Ollama (``ollama serve``), plain HTTP."""

    name = "ollama"

    def __init__(self, model: str = "llama3.1", host: str = "http://localhost:11434", timeout: float = 120.0):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def complete(self, system: str, prompt: str) -> str:
        payload = {"model": self.model, "stream": False, "format": "json",
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        out = _post_json(f"{self.host}/api/chat", payload, {}, self.timeout)
        return out["message"]["content"]

    def describe(self) -> str:
        return f"ollama:{self.model}"


class OfflineStandInBackend(LLMBackend):
    """Deterministic keyword rules that answer in the same JSON format as an LLM.

    **This is not a language model.** It exists so that LLM-in-the-loop
    co-simulations (and their guardrails, logging and multi-rate scheduling)
    can be run and tested offline. It reads the intent text and the JSON state
    embedded in the prompt and applies simple, documented rules.
    """

    name = "offline-stand-in"

    PROFILES = {
        "comfort": {"clg_sp": 23.5, "htg_sp": 22.5},
        "office": {"clg_sp": 24.0, "htg_sp": 21.0},
        "economy": {"clg_sp": 26.0, "htg_sp": 19.0},
        "default": {"clg_sp": 24.0, "htg_sp": 21.0},
    }
    KEYWORDS = (  # checked in order: an explicit cost objective wins over a mention of comfort
        ("economy", ("save", "saving", "cost", "cheap", "economy", "carbon", "minimise", "minimize")),
        ("comfort", ("nursing", "elderly", "hospital", "comfort", "senior", "infant")),
        ("office", ("office", "work")),
    )

    def classify(self, intent: str) -> str:
        text = intent.lower()
        for profile, words in self.KEYWORDS:
            if any(w in text for w in words):
                return profile
        return "default"

    def complete(self, system: str, prompt: str) -> str:
        intent = prompt.split("STATE:", 1)[0]
        state = extract_json(prompt.split("STATE:", 1)[1]) if "STATE:" in prompt else {}
        profile = self.classify(intent)
        sp = dict(self.PROFILES[profile])
        occupied = state.get("occupied")
        if occupied is not None and not occupied:
            sp = {"clg_sp": 29.0, "htg_sp": 15.6}
        price = state.get("observations", {}).get("price")
        if profile == "economy" and isinstance(price, (int, float)) and price >= 0.2:
            sp["clg_sp"] += 1.0
        sp["reason"] = f"rule profile '{profile}'" + (" (unoccupied setback)" if occupied is False else "")
        return json.dumps(sp)


class StochasticStandInBackend(LLMBackend):
    """Imitates two properties of hosted LLMs: sampled answers and variable latency.

    **This is not a language model.** It asks ``inner`` (default:
    :class:`OfflineStandInBackend`) for an answer, adds a random offset to
    every numeric set-point (normal with standard deviation ``spread``,
    rounded to 0.5 like a model that prefers round numbers) and waits a
    log-normally distributed time (median ``latency_s``, shape
    ``latency_sigma``) before answering. With ``seed=None`` the draws are not
    reproducible -- like a hosted LLM sampled at a temperature above zero. It
    is meant for testing record/replay, asynchronous execution and
    guardrails without an API key or cost.
    """

    name = "stochastic-stand-in"

    def __init__(self, inner: Optional[LLMBackend] = None, spread: float = 1.0, latency_s: float = 0.0,
                 latency_sigma: float = 0.5, seed: Optional[int] = None):
        import threading

        self.inner = inner if inner is not None else OfflineStandInBackend()
        self.spread = float(spread)
        self.latency_s = float(latency_s)
        self.latency_sigma = float(latency_sigma)
        self.rng = np.random.default_rng(seed)
        self._lock = threading.Lock()

    def complete(self, system: str, prompt: str) -> str:
        with self._lock:
            noise = self.rng.normal(0.0, self.spread, size=8) if self.spread > 0 else np.zeros(8)
            wait = float(self.latency_s * np.exp(self.rng.normal(0.0, self.latency_sigma))) if self.latency_s > 0 else 0.0
        if wait > 0:
            time.sleep(wait)
        data = extract_json(self.inner.complete(system, prompt)) or {}
        i = 0
        for key, value in list(data.items()):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                data[key] = round((float(value) + noise[i % len(noise)]) * 2.0) / 2.0
                i += 1
        if "reason" in data:
            data["reason"] = str(data["reason"]) + " (sampled)"
        return json.dumps(data)

    def describe(self) -> str:
        return (f"stochastic({self.inner.describe()}, spread={self.spread:g}, latency={self.latency_s:g}s)")


class FaultInjectionBackend(LLMBackend):
    """Wrap a backend and inject failures with given probabilities.

    Faults: ``malformed`` (no JSON), ``out_of_range`` (+/-40 C), ``extra_keys``
    (hallucinated fields), ``refusal`` and ``timeout``.
    """

    name = "fault-injection"

    def __init__(self, inner: LLMBackend, p_malformed: float = 0.0, p_out_of_range: float = 0.0,
                 p_extra_keys: float = 0.0, p_refusal: float = 0.0, p_timeout: float = 0.0, seed: int = 0):
        self.inner = inner
        self.p = {"malformed": p_malformed, "out_of_range": p_out_of_range, "extra_keys": p_extra_keys,
                  "refusal": p_refusal, "timeout": p_timeout}
        self.rng = np.random.default_rng(seed)
        self.injected: List[str] = []

    def complete(self, system: str, prompt: str) -> str:
        r = self.rng.random()
        acc = 0.0
        fault = None
        for name, p in self.p.items():
            acc += p
            if r < acc:
                fault = name
                break
        self.injected.append(fault or "none")
        if fault == "timeout":
            raise TimeoutError("injected timeout")
        if fault == "refusal":
            raise LLMRefusal("injected refusal")
        text = self.inner.complete(system, prompt)
        if fault == "malformed":
            return "Sure! I would set the cooling setpoint to about twenty-four degrees."
        if fault in ("out_of_range", "extra_keys"):
            data = extract_json(text) or {}
            if fault == "out_of_range":
                data = {k: (v + 40.0 if isinstance(v, (int, float)) else v) for k, v in data.items()}
            else:
                data.update({"fan_speed": 3, "unlock_doors": True})
            return json.dumps(data)
        return text

    def describe(self) -> str:
        return f"faults({self.inner.describe()})"

    @property
    def last_model(self):
        return getattr(self.inner, "last_model", None)

    def on_timeout(self, system: str, prompt: str, error: str) -> None:
        hook = getattr(self.inner, "on_timeout", None)
        if callable(hook):
            hook(system, prompt, error)


def _key(system: str, prompt: str) -> str:
    return hashlib.sha256((system + "\n---\n" + prompt).encode("utf-8")).hexdigest()


class RecordingBackend(LLMBackend):
    """Wrap a backend and append every exchange to a JSON-lines file."""

    name = "recording"

    def __init__(self, inner: LLMBackend, path: Union[str, Path]):
        self.inner = inner
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._timed_out: set = set()
        self._lock = __import__("threading").Lock()

    def _write(self, record: Dict[str, Any]) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def on_timeout(self, system: str, prompt: str, error: str) -> None:
        """Called by LLMSupervisor when a call timed out: record it as an error, ignore the late answer."""
        key = _key(system, prompt)
        self._timed_out.add(key)
        self._write({"key": key, "backend": self.inner.describe(), "error": f"TimeoutError: {error}"})

    def complete(self, system: str, prompt: str) -> str:
        key = _key(system, prompt)
        record = {"key": key, "backend": self.inner.describe()}
        try:
            text = self.inner.complete(system, prompt)
            record["response"] = text
            return text
        except Exception as exc:  # record failures too, so replays reproduce them
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if key not in self._timed_out:
                self._write(record)

    def describe(self) -> str:
        return f"record({self.inner.describe()})"

    @property
    def last_model(self):
        return getattr(self.inner, "last_model", None)


class ReplayBackend(LLMBackend):
    """Replay responses recorded by :class:`RecordingBackend` (exact prompt match)."""

    name = "replay"

    def __init__(self, path: Union[str, Path], strict: bool = True):
        self.responses: Dict[str, str] = {}
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    if rec["key"] not in self.responses:  # the first record of a prompt is the one that was used
                        self.responses[rec["key"]] = rec.get("response", ReplayedError(rec.get("error", "error")))
        self.strict = strict

    def complete(self, system: str, prompt: str) -> str:
        key = _key(system, prompt)
        if key not in self.responses:
            if self.strict:
                raise ReplayMismatchError("prompt not found in replay file (did the co-simulation change?)")
            return "{}"
        value = self.responses[key]
        if isinstance(value, ReplayedError):
            raise value
        return value


# ===================================================================== utilities
_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Return the first JSON object found in ``text`` (or ``None``)."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text, i)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _json_default(obj):
    if isinstance(obj, np.generic):
        return obj.item()
    return str(obj)


# ==================================================================== component
SetpointSpec = Union[Tuple[float, float, float], Mapping[str, float]]


class LLMSupervisor(Component):
    """LLM-in-the-loop supervisory controller with guardrails.

    Parameters
    ----------
    name : str
    backend : LLMBackend
    intent : str
        What the operator wants, in plain language.
    setpoints : dict
        ``{name: (min, max, default)}`` -- the decisions the LLM may make. Each
        becomes an output port.
    observations : dict or list
        Inputs shown to the LLM, ``{name: "description [unit]"}``.
    step_size : str
        Decision interval (default ``"1h"``); the building runs faster and holds
        the last decision in between (multi-rate co-simulation).
    max_change : float, optional
        Largest allowed change of any set-point per decision.
    timeout : float
        Seconds to wait for the backend before falling back.
    occupied_hours : (start, end), optional
        Adds an ``occupied`` flag to the state sent to the model.

    Outputs: one per set-point, plus ``valid`` (1 if the LLM answer was used),
    ``latency`` [s] and ``reason`` (the model's short justification).
    """

    def __init__(self, name: str, backend: LLMBackend, intent: str,
                 setpoints: Mapping[str, SetpointSpec],
                 observations: Union[Mapping[str, str], Sequence[str]] = (),
                 step_size: str = "1h", max_change: Optional[float] = None, timeout: float = 30.0,
                 occupied_hours: Optional[Tuple[int, int]] = None, decimals: int = 2):
        super().__init__(name, step_size=step_size)
        self.backend = backend
        self.intent = intent.strip()
        self.limits: Dict[str, Tuple[float, float, float]] = {}
        for sp, spec in setpoints.items():
            if isinstance(spec, Mapping):
                lo, hi, default = spec["min"], spec["max"], spec.get("default", (spec["min"] + spec["max"]) / 2)
            else:
                lo, hi, default = spec
            if not lo <= default <= hi:
                raise ValueError(f"default of {sp!r} outside [{lo}, {hi}]")
            self.limits[sp] = (float(lo), float(hi), float(default))
            self.add_output(sp, initial=float(default), unit="C" if sp.endswith("_sp") else None, kind="hold")
        self.observations = dict(observations) if isinstance(observations, Mapping) else {o: o for o in observations}
        for obs in self.observations:
            self.add_input(obs, default=None)
        self.add_output("valid", initial=0)
        self.add_output("latency", initial=0.0, unit="s")
        self.add_output("reason", initial="")
        if max_change is not None and max_change <= 0:
            raise ValueError("max_change must be positive")
        self.max_change = max_change
        self.timeout = timeout
        self.occupied_hours = occupied_hours
        self.decimals = decimals
        self.current = {sp: lim[2] for sp, lim in self.limits.items()}
        self.stats = {"calls": 0, "valid": 0, "fallback": 0, "clipped": 0, "rate_limited": 0}
        self._pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"llm-{name}")

    # ----------------------------------------------------------------- prompt
    def system_prompt(self) -> str:
        spec = ", ".join(f'"{k}": number in [{lo:g}, {hi:g}]' for k, (lo, hi, _d) in self.limits.items())
        return (
            "You are the supervisory controller of a building in a building-energy co-simulation. "
            "Each call you receive the operator's intent and the current measured state. Decide the "
            "set-points for the next control interval. Respond with ONE JSON object and nothing else, "
            f"with keys {{{spec}, \"reason\": short string}}. Temperatures are in degrees Celsius."
        )

    def build_prompt(self, t: datetime, inputs) -> str:
        obs = {}
        for name, desc in self.observations.items():
            value = inputs.get(name)
            if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool) \
                    and math.isfinite(float(value)):
                value = round(float(value), self.decimals)
            obs[name] = value
        state: Dict[str, Any] = {
            "time": t.strftime("%Y-%m-%d %H:%M"),
            "weekday": t.strftime("%A"),
            "observations": obs,
            "observation_meaning": self.observations,
            "current_setpoints": {k: round(v, self.decimals) for k, v in self.current.items()},
        }
        if self.occupied_hours is not None:
            state["occupied"] = bool(t.weekday() < 5 and self.occupied_hours[0] <= t.hour < self.occupied_hours[1])
        return f"OPERATOR INTENT: {self.intent}\n\nSTATE: {json.dumps(state, default=_json_default)}"

    # ------------------------------------------------------------------ step
    def step(self, t, inputs):
        system, prompt = self.system_prompt(), self.build_prompt(t, inputs)
        self.stats["calls"] += 1
        t0 = time.perf_counter()
        raw, error = None, None
        try:
            raw = self._pool.submit(self.backend.complete, system, prompt).result(timeout=self.timeout)
        except ReplayMismatchError:
            raise  # a strict replay that diverges must stop the run, not fall back silently
        except cf.TimeoutError:
            error = f"timeout after {self.timeout:g}s"
            hook = getattr(self.backend, "on_timeout", None)
            if callable(hook):
                hook(system, prompt, error)
            # abandon the hung call so later decisions do not queue behind it
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"llm-{self.name}")
        except Exception as exc:  # network errors, refusals, injected faults ...
            error = f"{type(exc).__name__}: {exc}"
        latency = time.perf_counter() - t0
        applied, notes, valid = self._guard(raw, error)
        self.current.update(applied)
        reason = notes.pop("reason", "")
        self.ctx.log(
            f"LLM decision: {applied} ({'valid' if valid else 'fallback'})", level=logging.DEBUG, kind="llm",
            backend=self.backend.describe(), model=getattr(self.backend, "last_model", None), prompt=prompt,
            response=raw, error=error, applied=dict(applied),
            guard=notes, latency_s=latency,
        )
        out = dict(applied)
        out.update(valid=int(valid), latency=latency, reason=reason)
        return out

    def _guard(self, raw: Optional[str], error: Optional[str]) -> Tuple[Dict[str, float], Dict[str, Any], bool]:
        notes: Dict[str, Any] = {}
        if error is not None:
            notes["fallback"] = error
            self.stats["fallback"] += 1
            return dict(self.current), notes, False
        data = extract_json(raw or "")
        if data is None:
            notes["fallback"] = "no JSON object in response"
            self.stats["fallback"] += 1
            return dict(self.current), notes, False
        ignored = [k for k in data if k not in self.limits and k != "reason"]
        if ignored:
            notes["ignored_keys"] = ignored
        applied = dict(self.current)
        used_any = False
        for sp, (lo, hi, _d) in self.limits.items():
            value = data.get(sp)
            if isinstance(value, bool):  # JSON true/false are not temperatures
                notes.setdefault("missing_or_invalid", []).append(sp)
                continue
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError):
                notes.setdefault("missing_or_invalid", []).append(sp)
                continue
            if not math.isfinite(value):
                notes.setdefault("missing_or_invalid", []).append(sp)
                continue
            if value < lo or value > hi:
                notes.setdefault("clipped", {})[sp] = value
                self.stats["clipped"] += 1
                value = min(max(value, lo), hi)
            if self.max_change is not None:
                prev = self.current[sp]
                if abs(value - prev) > self.max_change:
                    notes.setdefault("rate_limited", {})[sp] = value
                    self.stats["rate_limited"] += 1
                    value = prev + math.copysign(self.max_change, value - prev)
            applied[sp] = value
            used_any = True
        if not used_any:
            notes["fallback"] = "no usable set-point in response"
            self.stats["fallback"] += 1
            return dict(self.current), notes, False
        self.stats["valid"] += 1
        notes["reason"] = str(data.get("reason", ""))[:300]
        return applied, notes, True

    def setup(self, ctx) -> None:
        # fresh state for every run (components may be reused, e.g. by a Gymnasium environment)
        self.current = {sp: lim[2] for sp, lim in self.limits.items()}
        self.stats = {"calls": 0, "valid": 0, "fallback": 0, "clipped": 0, "rate_limited": 0}
        if self._pool._shutdown:
            self._pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"llm-{self.name}")

    def finalize(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def run_metadata(self) -> Dict[str, Any]:
        return {"backend": self.backend.describe(), "intent": self.intent, **self.stats}
