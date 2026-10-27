"""Tests of the LLM supervisor, its guardrails and the offline/recording backends."""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime

import pytest

from pycosim.components.llm import (
    FaultInjectionBackend,
    LLMBackend,
    LLMRefusal,
    LLMSupervisor,
    OfflineStandInBackend,
    RecordingBackend,
    ReplayBackend,
    extract_json,
)
from pycosim.components.sources import Constant
from pycosim.core.component import Inputs

MONDAY_2PM = datetime(2023, 7, 3, 14)
SETPOINTS = {"clg_sp": (22.0, 28.0, 24.0), "htg_sp": (18.0, 22.0, 20.0)}


class Scripted(LLMBackend):
    """Returns (or raises) the given responses in order; the last one repeats."""

    name = "scripted"

    def __init__(self, *responses, delay=0.0):
        self.responses = list(responses)
        self.delay = delay
        self.calls = []

    def complete(self, system, prompt):
        self.calls.append((system, prompt))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if callable(response):
            response = response()
        if isinstance(response, BaseException):
            raise response
        return response


class FakeCtx:
    def __init__(self):
        self.events = []

    def log(self, message, level=None, kind="info", **data):
        self.events.append({"message": message, "kind": kind, **data})


def make_sup(backend, **kw):
    kw.setdefault("intent", "Keep the office comfortable")
    sup = LLMSupervisor("llm", backend, setpoints=kw.pop("setpoints", SETPOINTS),
                        observations=kw.pop("observations", {"T_zone": "zone temperature [C]",
                                                             "price": "electricity price [$/kWh]"}), **kw)
    sup.ctx = FakeCtx()
    return sup


def decide(sup, t=MONDAY_2PM, **obs):
    obs.setdefault("T_zone", 25.0)
    obs.setdefault("price", 0.1)
    return sup.step(t, Inputs(obs))


@pytest.fixture
def sups():
    created = []

    def factory(*responses, **kw):
        sup = make_sup(Scripted(*responses, delay=kw.pop("delay", 0.0)), **kw)
        created.append(sup)
        return sup

    yield factory
    for sup in created:
        sup.finalize()


# ================================================================= extract_json
@pytest.mark.parametrize("text, expected", [
    ('{"clg_sp": 25}', {"clg_sp": 25}),
    ('  {"a": 1.5, "reason": "x"}  ', {"a": 1.5, "reason": "x"}),
    ('Sure! Here is my decision: {"clg_sp": 25.5} -- hope that helps.', {"clg_sp": 25.5}),
    ('```json\n{"clg_sp": 26}\n```', {"clg_sp": 26}),
    ('```\n{"clg_sp": 26}\n```', {"clg_sp": 26}),
    ('text {"a": {"b": 1}} and later {"c": 2}', {"a": {"b": 1}}),
    ('{broken json} then {"ok": true}', {"ok": True}),
    ('{"reason": "a } b", "clg_sp": 25}', {"reason": "a } b", "clg_sp": 25}),
])
def test_extract_json_finds_first_object(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]", '"just a string"', "{not: json}", None, 42])
def test_extract_json_returns_none(text):
    assert extract_json(text) is None


def test_extract_json_nan_is_parsed_but_not_finite():
    assert math.isnan(extract_json('{"clg_sp": NaN}')["clg_sp"])


def test_extract_json_brace_inside_string_in_prose():
    assert extract_json('Decision: {"reason": "keep T } 24", "clg_sp": 25} done') == {
        "reason": "keep T } 24", "clg_sp": 25}


# =================================================================== guardrails
def test_valid_json_is_applied(sups):
    sup = sups('{"clg_sp": 25.5, "htg_sp": 19, "reason": "pre-cool a bit"}')
    out = decide(sup)
    assert out["clg_sp"] == 25.5 and out["htg_sp"] == 19.0
    assert out["valid"] == 1 and out["reason"] == "pre-cool a bit" and out["latency"] >= 0
    assert set(out) == {"clg_sp", "htg_sp", "valid", "latency", "reason"}
    assert sup.stats == {"calls": 1, "valid": 1, "fallback": 0, "clipped": 0, "rate_limited": 0}
    ev = sup.ctx.events[-1]
    assert ev["kind"] == "llm" and ev["applied"] == {"clg_sp": 25.5, "htg_sp": 19.0} and ev["error"] is None
    assert ev["response"].startswith('{"clg_sp"') and ev["prompt"].startswith("OPERATOR INTENT")
    assert ev["backend"] == "scripted"


def test_json_in_prose_and_partial_answer(sups):
    sup = sups('I recommend {"clg_sp": 25} because it is hot.')
    out = decide(sup)
    assert out["clg_sp"] == 25.0 and out["htg_sp"] == 20.0 and out["valid"] == 1  # htg_sp held at default
    assert sup.ctx.events[-1]["guard"]["missing_or_invalid"] == ["htg_sp"]
    assert out["reason"] == ""


def test_json_in_markdown_fences(sups):
    out = decide(sups('```json\n{"clg_sp": 26.5, "htg_sp": 21}\n```'))
    assert (out["clg_sp"], out["htg_sp"], out["valid"]) == (26.5, 21.0, 1)


def test_out_of_range_values_are_clipped(sups):
    sup = sups('{"clg_sp": 40, "htg_sp": -5}')
    out = decide(sup)
    assert (out["clg_sp"], out["htg_sp"], out["valid"]) == (28.0, 18.0, 1)
    assert sup.stats["clipped"] == 2
    assert sup.ctx.events[-1]["guard"]["clipped"] == {"clg_sp": 40.0, "htg_sp": -5.0}


@pytest.mark.parametrize("response", [
    '{"clg_sp": NaN, "htg_sp": NaN}',
    '{"clg_sp": Infinity}',
    '{"clg_sp": "hot", "htg_sp": null}',
    '{"clg_sp": [24]}',
    "{}",
    '{"reason": "I am not sure"}',
])
def test_invalid_values_fall_back(sups, response):
    sup = sups(response)
    out = decide(sup)
    assert (out["clg_sp"], out["htg_sp"], out["valid"]) == (24.0, 20.0, 0)
    assert sup.stats["fallback"] == 1 and sup.stats["valid"] == 0
    assert sup.ctx.events[-1]["guard"]["fallback"] == "no usable set-point in response"


@pytest.mark.parametrize("response", ["I cannot help with that.", "", "[24, 20]"])
def test_malformed_responses_fall_back(sups, response):
    sup = sups(response)
    out = decide(sup)
    assert out["valid"] == 0 and out["clg_sp"] == 24.0
    assert sup.ctx.events[-1]["guard"]["fallback"] == "no JSON object in response"


def test_boolean_is_not_a_number(sups):
    out = decide(sups('{"clg_sp": true}'))
    assert out["valid"] == 0 and out["clg_sp"] == 24.0


def test_extra_keys_are_ignored(sups):
    sup = sups('{"clg_sp": 25, "htg_sp": 20, "fan_speed": 3, "unlock_doors": true}')
    out = decide(sup)
    assert out["valid"] == 1 and "fan_speed" not in out and "unlock_doors" not in out
    assert sup.ctx.events[-1]["guard"]["ignored_keys"] == ["fan_speed", "unlock_doors"]


@pytest.mark.parametrize("exc, text", [(RuntimeError("network down"), "RuntimeError: network down"),
                                       (LLMRefusal("refusal"), "LLMRefusal: refusal"),
                                       (KeyError("missing"), "KeyError")])
def test_backend_exceptions_fall_back(sups, exc, text):
    sup = sups(exc)
    out = decide(sup)
    assert out["valid"] == 0 and (out["clg_sp"], out["htg_sp"]) == (24.0, 20.0)
    assert text in sup.ctx.events[-1]["error"]
    assert sup.ctx.events[-1]["guard"]["fallback"].startswith(text)
    assert sup.stats["fallback"] == 1


def test_timeout_falls_back(sups):
    sup = sups(lambda: time.sleep(0.6) or '{"clg_sp": 27}', timeout=0.05)
    t0 = time.perf_counter()
    out = decide(sup)
    assert time.perf_counter() - t0 < 0.5
    assert out["valid"] == 0 and out["clg_sp"] == 24.0
    assert out["latency"] == pytest.approx(0.05, abs=0.2)
    assert sup.ctx.events[-1]["error"] == "timeout after 0.05s"


def test_decision_after_a_timeout_is_not_blocked(sups):
    sup = sups(lambda: time.sleep(1.5) or "{}", '{"clg_sp": 27}', timeout=0.4)
    assert decide(sup)["valid"] == 0
    out = decide(sup)
    assert out["valid"] == 1 and out["clg_sp"] == 27.0


def test_fallback_holds_last_valid_value(sups):
    sup = sups('{"clg_sp": 26, "htg_sp": 21}', "garbage", RuntimeError("x"), '{"htg_sp": 19}')
    assert decide(sup)["clg_sp"] == 26.0
    held = decide(sup)
    assert (held["clg_sp"], held["htg_sp"], held["valid"]) == (26.0, 21.0, 0)
    assert decide(sup)["clg_sp"] == 26.0
    partial = decide(sup)
    assert (partial["clg_sp"], partial["htg_sp"], partial["valid"]) == (26.0, 19.0, 1)
    assert sup.stats == {"calls": 4, "valid": 2, "fallback": 2, "clipped": 0, "rate_limited": 0}


def test_rate_limit(sups):
    sup = sups('{"clg_sp": 28}', '{"clg_sp": 28}', '{"clg_sp": 28}', '{"clg_sp": 26.5}', '{"clg_sp": 22}',
               max_change=1.0)
    values = [decide(sup)["clg_sp"] for _ in range(5)]
    assert values == [25.0, 26.0, 27.0, 26.5, 25.5]  # the 4th answer is within the allowed change
    assert sup.stats["rate_limited"] == 4
    assert sup.ctx.events[0]["guard"]["rate_limited"] == {"clg_sp": 28.0}


def test_clip_then_rate_limit(sups):
    sup = sups('{"clg_sp": 40, "htg_sp": 10}', max_change=0.5)
    out = decide(sup)
    assert (out["clg_sp"], out["htg_sp"]) == (24.5, 19.5)
    guard = sup.ctx.events[-1]["guard"]
    assert guard["clipped"] == {"clg_sp": 40.0, "htg_sp": 10.0}
    assert guard["rate_limited"] == {"clg_sp": 28.0, "htg_sp": 18.0}


# ==================================================================== prompts
def test_system_prompt_and_state(sups):
    sup = sups("{}", occupied_hours=(8, 18), decimals=1)
    system = sup.system_prompt()
    assert '"clg_sp": number in [22, 28]' in system and '"htg_sp": number in [18, 22]' in system
    assert "ONE JSON object" in system
    prompt = sup.build_prompt(MONDAY_2PM, Inputs(T_zone=25.1234, price=None))
    assert prompt.startswith("OPERATOR INTENT: Keep the office comfortable\n\nSTATE: ")
    state = json.loads(prompt.split("STATE: ", 1)[1])
    assert state["time"] == "2023-07-03 14:00" and state["weekday"] == "Monday"
    assert state["observations"] == {"T_zone": 25.1, "price": None}
    assert state["observation_meaning"]["price"] == "electricity price [$/kWh]"
    assert state["current_setpoints"] == {"clg_sp": 24.0, "htg_sp": 20.0}
    assert state["occupied"] is True
    saturday = json.loads(sup.build_prompt(datetime(2023, 7, 8, 14), Inputs(T_zone=25.0, price=0.1))
                          .split("STATE: ", 1)[1])
    assert saturday["occupied"] is False
    evening = json.loads(sup.build_prompt(datetime(2023, 7, 3, 19), Inputs(T_zone=25.0, price=0.1))
                         .split("STATE: ", 1)[1])
    assert evening["occupied"] is False
    assert "occupied" not in json.loads(make_sup(Scripted("{}")).build_prompt(MONDAY_2PM, Inputs())
                                        .split("STATE: ", 1)[1])


def test_supervisor_ports_and_validation():
    sup = make_sup(Scripted("{}"), setpoints={"clg_sp": {"min": 20, "max": 30}, "fan": (0, 1, 0.5)},
                   observations=["T_zone", "price"])
    assert sup.limits == {"clg_sp": (20.0, 30.0, 25.0), "fan": (0.0, 1.0, 0.5)}
    assert list(sup.input_ports) == ["T_zone", "price"]
    assert sup.observations == {"T_zone": "T_zone", "price": "price"}
    assert list(sup.output_ports) == ["clg_sp", "fan", "valid", "latency", "reason"]
    assert sup.output_ports["clg_sp"].unit == "C" and sup.output_ports["fan"].unit is None
    assert sup.output_ports["clg_sp"].default == 25.0
    assert sup.step_size == "1h"
    sup.finalize()
    with pytest.raises(ValueError, match="outside"):
        make_sup(Scripted("{}"), setpoints={"clg_sp": (22, 28, 30)})


# ============================================================ offline backends
def test_offline_stand_in_rules():
    be = OfflineStandInBackend()
    assert be.classify("A nursing home for elderly residents") == "comfort"
    assert be.classify("Save money during the peak") == "economy"
    assert be.classify("A small office") == "office"
    assert be.classify("A warehouse") == "default"
    state = {"occupied": True, "observations": {"price": 0.25}}
    econ = json.loads(be.complete("sys", f"OPERATOR INTENT: minimise cost\n\nSTATE: {json.dumps(state)}"))
    assert (econ["clg_sp"], econ["htg_sp"]) == (27.0, 19.0)
    state = {"occupied": False, "observations": {}}
    off = json.loads(be.complete("sys", f"OPERATOR INTENT: comfort first\n\nSTATE: {json.dumps(state)}"))
    assert (off["clg_sp"], off["htg_sp"]) == (29.0, 15.6) and "unoccupied setback" in off["reason"]
    plain = json.loads(be.complete("sys", "hospital ward"))
    assert (plain["clg_sp"], plain["htg_sp"]) == (23.5, 22.5)
    assert be.describe() == "offline-stand-in"


@pytest.mark.parametrize("fault, check", [
    ("p_malformed", lambda s, out: out["valid"] == 0),
    ("p_out_of_range", lambda s, out: out["valid"] == 1 and out["clg_sp"] == 28.0 and s.stats["clipped"] == 2),
    ("p_extra_keys", lambda s, out: out["valid"] == 1 and s.ctx.events[-1]["guard"]["ignored_keys"]
     == ["fan_speed", "unlock_doors"]),
    ("p_refusal", lambda s, out: out["valid"] == 0 and "LLMRefusal" in s.ctx.events[-1]["error"]),
    ("p_timeout", lambda s, out: out["valid"] == 0),
])
def test_fault_injection_each_fault(fault, check):
    backend = FaultInjectionBackend(OfflineStandInBackend(), **{fault: 1.0})
    sup = make_sup(backend)
    try:
        out = decide(sup)
        assert check(sup, out)
        assert backend.injected == [fault[2:]]
        assert backend.describe() == "faults(offline-stand-in)"
    finally:
        sup.finalize()


def test_fault_injection_is_seeded():
    def sequence(seed):
        be = FaultInjectionBackend(OfflineStandInBackend(), p_malformed=0.2, p_out_of_range=0.2, p_refusal=0.2,
                                   seed=seed)
        for _ in range(30):
            try:
                be.complete("s", "office")
            except LLMRefusal:
                pass
        return be.injected

    assert sequence(1) == sequence(1)
    assert sequence(1) != sequence(2)
    assert {"none", "malformed", "out_of_range", "refusal"} >= set(sequence(1))


# ============================================================ in a simulation
def llm_sim(make_sim, backend, name, intent="This is a nursing home; prioritise comfort", **kw):
    sim = make_sim(start="2023-07-03 05:00", end="2023-07-03 10:00", step="15min", name=name)
    sim.add(Constant("meas", T_zone=25.0, price=0.12))
    sup = sim.add(LLMSupervisor("llm", backend, intent=intent, setpoints=SETPOINTS,
                                observations={"T_zone": "zone temperature [C]", "price": "price [$/kWh]"},
                                occupied_hours=(7, 18), **kw))
    sim.connect_matching("meas", "llm")
    return sim, sup


def test_supervisor_multi_rate_events_and_metadata(make_sim):
    sim, sup = llm_sim(make_sim, OfflineStandInBackend(), "multi")
    res = sim.run()
    assert sup.stats["calls"] == 5  # hourly decisions in a 15-min co-simulation
    clg = res["llm.clg_sp"]
    # 05:00 and 06:00 unoccupied -> setback 29/15.6, clipped to the bounds 28/18; then comfort profile
    assert list(clg.iloc[::4]) == [28.0, 28.0, 23.5, 23.5, 23.5]
    assert list(res["llm.htg_sp"].iloc[::4]) == [18.0, 18.0, 22.0, 22.0, 22.0]
    assert list(clg.iloc[8:12]) == [23.5] * 4  # held between decisions
    assert (res["llm.valid"] == 1).all() and sup.stats["clipped"] == 7  # 2 + 2 setback, 3 x htg_sp 22.5
    assert res["llm.reason"].iloc[-1] == "rule profile 'comfort'"
    llm_events = res.events[res.events["kind"] == "llm"]
    assert len(llm_events) == 5
    assert {"prompt", "response", "applied", "guard", "latency_s", "backend"} <= set(res.events.columns)
    assert llm_events["time"].iloc[2] == datetime(2023, 7, 3, 7)
    meta = res.meta["component_metadata"]["llm"]
    assert meta["backend"] == "offline-stand-in" and meta["calls"] == 5 and meta["intent"].startswith("This is")


def test_recording_and_replay_reproduce_the_run(make_sim, tmp_path):
    path = tmp_path / "rec" / "llm.jsonl"
    sim, _ = llm_sim(make_sim, RecordingBackend(OfflineStandInBackend(), path), "record")
    recorded = sim.run()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 5 and all(rec["backend"] == "offline-stand-in" for rec in lines)
    first_prompt = recorded.events[recorded.events["kind"] == "llm"]["prompt"].iloc[0]
    system = LLMSupervisor("x", OfflineStandInBackend(), "i", SETPOINTS).system_prompt()
    assert lines[0]["key"] == hashlib.sha256((system + "\n---\n" + first_prompt).encode("utf-8")).hexdigest()

    sim2, sup2 = llm_sim(make_sim, ReplayBackend(path), "replay")
    replayed = sim2.run()
    cols = ["llm.clg_sp", "llm.htg_sp", "llm.valid", "llm.reason"]
    assert replayed.df[cols].equals(recorded.df[cols])
    assert sup2.stats["valid"] == 5


def test_replay_of_a_changed_scenario(make_sim, tmp_path):
    path = tmp_path / "llm.jsonl"
    sim, _ = llm_sim(make_sim, RecordingBackend(OfflineStandInBackend(), path), "record")
    sim.run()
    # a strict replay that diverges from the recording stops the run instead of silently falling back
    strict_sim, _ = llm_sim(make_sim, ReplayBackend(path), "strict", intent="Save energy")
    from pycosim.components.llm import ReplayMismatchError
    from pycosim.errors import ComponentError

    with pytest.raises(ComponentError) as info:
        strict_sim.run()
    assert isinstance(info.value.original, ReplayMismatchError)
    # a non-strict replay answers "{}" for unknown prompts, which the guardrails turn into fallbacks
    loose_sim, loose_sup = llm_sim(make_sim, ReplayBackend(path, strict=False), "loose", intent="Save energy")
    loose_sim.run()
    assert loose_sup.stats["fallback"] == 5


def test_finalize_shuts_down_worker_thread(make_sim):
    sim, sup = llm_sim(make_sim, OfflineStandInBackend(), "threads")
    sim.run()
    assert sup._pool._shutdown
