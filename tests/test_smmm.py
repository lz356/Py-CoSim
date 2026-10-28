"""SMMM 2.0: time-meaning-carrying messages, alignment, patterns, quality, async, replay, provenance."""
import math
import random
import threading
import time
from datetime import datetime, timedelta

import numpy as np
import pytest

import pycosim as pc
from pycosim.core.errors import AlgebraicLoopError, ConfigurationError
from pycosim.core.messages import MISSING, Signal


# ------------------------------------------------------------------ Signal
def _sig(kind, interval="past", step=900.0, initial=None):
    return Signal("a.x", kind, interval, step, initial)


def test_hold_window_is_time_weighted():
    s = _sig("hold")
    s.append(0, 1.0, 0.0, None)
    s.append(2, 3.0, 1800.0, None)
    v, cov, ids = s.window(0.0, 3600.0)
    assert v == pytest.approx(2.0)
    assert cov == 1.0 and ids == [0, 1]
    assert s.at(1799.0)[0] == 1.0 and s.at(1800.0)[0] == 3.0


def test_hold_window_before_first_message_reports_partial_coverage():
    s = _sig("hold")
    s.append(1, 5.0, 900.0, None)
    v, cov, _ = s.window(0.0, 1800.0)
    assert v == 5.0 and cov == pytest.approx(0.5)
    assert s.window(-900.0, 0.0)[0] is MISSING


def test_average_past_aggregates_exactly_across_rates():
    s = _sig("average", "past", 900.0)
    for k, v in enumerate([10.0, 20.0, 30.0, 40.0], start=1):  # published at 15, 30, 45, 60 min for the step before
        s.append(k, v, (k - 1) * 900.0, k * 900.0)
    v, cov, ids = s.window(0.0, 3600.0)
    assert v == pytest.approx(25.0) and cov == 1.0 and ids == [0, 1, 2, 3]
    v, cov, _ = s.window(900.0, 2700.0)
    assert v == pytest.approx(25.0)
    v, cov, _ = s.window(450.0, 1350.0)  # half of the first and half of the second interval
    assert v == pytest.approx(15.0) and cov == 1.0


def test_average_window_extrapolates_by_holding_and_reports_coverage():
    s = _sig("average", "past", 900.0)
    s.append(1, 10.0, 0.0, 900.0)
    v, cov, _ = s.window(0.0, 1800.0)
    assert v == pytest.approx(10.0) and cov == pytest.approx(0.5)


def test_amount_splits_proportionally():
    s = _sig("amount", "past", 3600.0)
    s.append(1, 3600.0, 0.0, 3600.0)  # 3600 J over one hour
    total, cov, _ = s.window(0.0, 900.0)
    assert total == pytest.approx(900.0) and cov == 1.0
    total, cov, _ = s.window(0.0, 7200.0)  # second hour not published yet: rate held
    assert total == pytest.approx(7200.0) and cov == pytest.approx(0.5)


def test_instant_window_is_trapezoidal():
    s = _sig("instant")
    s.append(0, 0.0, 0.0, None)
    s.append(1, 10.0, 900.0, None)
    v, cov, _ = s.window(0.0, 900.0)
    assert v == pytest.approx(5.0) and cov == 1.0
    assert s.at(450.0)[0] == pytest.approx(5.0)


def test_future_dated_hold_messages_form_a_schedule():
    s = _sig("hold")
    s.append(0, 0.10, 0.0, None)
    s.append(0, 0.40, 7200.0, None)  # announced now, valid from +2 h
    s.append(1, 0.12, 900.0, None)   # published later but valid earlier
    assert s.at(3600.0)[0] == 0.12
    assert s.at(7300.0)[0] == 0.40


def test_event_window_lists_values():
    s = _sig("event")
    s.append(1, "dr", 900.0, None)
    s.append(3, "end", 2700.0, None)
    assert s.window(0.0, 1800.0)[0] == ["dr"]
    assert s.window(0.0, 3600.0)[0] == ["dr", "end"]


# --------------------------------------------------------------- the master
def _sim(tmp_path, end="2023-07-03 06:00", step="15min", save=False, **kw):
    return pc.CoSimulation("2023-07-03", end, step, save=save, output_dir=tmp_path, log_level="WARNING", **kw)


class _Ramp(pc.Component):
    """State-based producer of an interval average (like EnergyPlus): P over [t-h, t) = 1000 * (k)."""

    direct_feedthrough = False
    outputs = {"P": {"default": 0.0, "kind": "average", "interval": "past"}}

    def setup(self, ctx):
        self.k = 0

    def output(self, t):
        return {"P": 1000.0 * self.k}

    def update(self, t, inputs):
        self.k += 1


def test_past_read_aligns_tariff_and_power(tmp_path):
    """Cost = integral of P * price: aligned reads are exact, naive reads are shifted by one step."""
    sim = _sim(tmp_path, end="2023-07-03 02:00")
    sim.add(_Ramp("bldg"))

    @pc.component(outputs={"price": {"default": 0.0, "kind": "hold"}})
    def tariff(t):
        return 0.30 if t.hour >= 1 else 0.10

    sim.add(tariff)
    aligned = sim.add(pc.FunctionComponent(lambda t, P=0.0, price=0.0: P * price, name="aligned", outputs="c"))
    naive = sim.add(pc.FunctionComponent(lambda t, P=0.0, price=0.0: P * price, name="naive", outputs="c"))
    sim.connect("bldg.P", "aligned.P", read="past")
    sim.connect("tariff.price", "aligned.price", read="past")
    sim.connect("bldg.P", "naive.P")
    sim.connect("tariff.price", "naive.price")
    res = sim.run()
    # at 01:00 the building reports P for 00:45-01:00 (4 kW) -- the price in that interval was 0.10
    assert res.df.loc["2023-07-03 01:00", "aligned.c"] == pytest.approx(4000 * 0.10)
    assert res.df.loc["2023-07-03 01:00", "naive.c"] == pytest.approx(4000 * 0.30)
    assert len(res.alignment) == 2


def test_slow_consumer_gets_exact_interval_mean(tmp_path):
    sim = _sim(tmp_path, end="2023-07-03 03:00")
    sim.add(_Ramp("bldg"))
    sim.add(pc.FunctionComponent(lambda t, P=0.0: P, name="hourly", outputs="P_mean", step_size="1h"))
    sim.connect("bldg.P", "hourly.P", read="past")
    res = sim.run()
    # hour 1 (01:00 reads 00:00-01:00): steps report 1,2,3,4 kW -> 2.5 kW
    assert res.df.loc["2023-07-03 01:00", "hourly.P_mean"] == pytest.approx(2500.0)
    assert res.df.loc["2023-07-03 02:00", "hourly.P_mean"] == pytest.approx(6500.0)


def test_pattern_subscription_matches_all_producers(tmp_path):
    sim = _sim(tmp_path)
    for i in range(3):
        sim.add(pc.FunctionComponent(lambda t, i=i: float(i + 1), name=f"b{i}", outputs="P_elec"))
    sim.add(pc.FunctionComponent(lambda t, total=0.0: total, name="meter", outputs="seen"))
    conn = sim.subscribe("meter.total", "*.P_elec", reduce="sum")
    res = sim.run()
    assert res["meter.seen"].iloc[-1] == 6.0
    assert len(conn.sources) == 3


def test_pattern_without_match_is_an_error(tmp_path):
    sim = _sim(tmp_path)
    sim.add(pc.FunctionComponent(lambda t, x=0.0: x, name="m", outputs="y"))
    sim.connect("*.nothing", "m.x")
    with pytest.raises(ConfigurationError, match="matches no output"):
        sim.initialize()


def test_pattern_matching_many_needs_reduce(tmp_path):
    sim = _sim(tmp_path)
    for i in range(2):
        sim.add(pc.FunctionComponent(lambda t: 1.0, name=f"b{i}", outputs="P"))
    sim.add(pc.FunctionComponent(lambda t, x=0.0: x, name="m", outputs="y"))
    sim.connect("*.P", "m.x")
    with pytest.raises(ConfigurationError, match="reduce"):
        sim.initialize()


def test_topic_subscription_decouples_producer(tmp_path):
    sim = _sim(tmp_path)
    sim.add(pc.FunctionComponent(lambda t: 0.25, name="utility_api",
                                 outputs={"p": {"default": 0.0, "kind": "hold", "topic": "grid/price"}}))
    sim.add(pc.FunctionComponent(lambda t, price=0.0: price, name="m", outputs="y"))
    sim.connect("grid/price", "m.price")
    res = sim.run()
    assert (res["m.y"] == 0.25).all()
    assert res.meta["topics"] == {"grid/price": "utility_api.p"}


def test_past_read_of_hold_source_breaks_algebraic_loop(tmp_path):
    def build(read):
        sim = _sim(tmp_path)
        sim.add(pc.FunctionComponent(lambda t, y=0.0: y + 1.0, name="a",
                                     outputs={"x": {"default": 0.0, "kind": "hold"}}))
        sim.add(pc.FunctionComponent(lambda t, x=0.0: x, name="b", outputs={"y": {"default": 0.0, "kind": "hold"}}))
        sim.connect("a.x", "b.x", read=read)
        sim.connect("b.y", "a.y")
        return sim

    with pytest.raises(AlgebraicLoopError):
        build(None).initialize()
    res = build("past").run()
    assert res["a.x"].iloc[-1] > 1.0


def test_delay_with_window_read_is_rejected(tmp_path):
    sim = _sim(tmp_path)
    sim.add(pc.FunctionComponent(lambda t: 1.0, name="a", outputs="x"))
    sim.add(pc.FunctionComponent(lambda t, x=0.0: x, name="b", outputs="y"))
    sim.connect("a.x", "b.x", delay=True, read="past")
    with pytest.raises(ConfigurationError, match="delay"):
        sim.initialize()


def test_context_window_and_at(tmp_path):
    seen = {}
    sim = _sim(tmp_path, end="2023-07-03 02:00")
    sim.add(_Ramp("bldg"))

    @pc.component(outputs="m", step_size="1h")
    def probe(t, ctx):
        seen[t] = ctx.window("bldg.P", t - timedelta(hours=1))
        return seen[t]

    sim.add(probe)
    sim.run()
    assert seen[datetime(2023, 7, 3, 1)] == pytest.approx(2500.0)


# ------------------------------------------------------------------ quality
def test_bounds_clip_and_fallback_are_flagged(tmp_path):
    values = iter([1.0, 50.0, float("nan"), -3.0, 2.0, 2.0, 2.0, 2.0] * 4)

    @pc.component(outputs={"sp": {"default": 24.0, "bounds": (0.0, 10.0)}})
    def noisy(t):
        return next(values)

    sim = _sim(tmp_path, end="2023-07-03 02:00")
    sim.add(noisy)
    res = sim.run()
    assert list(res["noisy.sp"].iloc[:5]) == [1.0, 10.0, 10.0, 0.0, 2.0]
    q = res.quality.set_index("quality")["messages"].to_dict()
    assert q["clipped"] >= 2 and q["fallback"] >= 1


def test_on_invalid_error_stops_the_run(tmp_path):
    @pc.component(outputs={"sp": {"default": 24.0, "bounds": (0.0, 10.0), "on_invalid": "error"}})
    def bad(t):
        return 99.0

    sim = _sim(tmp_path)
    sim.add(bad)
    with pytest.raises(pc.errors.ComponentError):
        sim.run()


# -------------------------------------------------------------- asynchrony
class _Slow(pc.Component):
    outputs = {"u": {"default": 0.0, "kind": "hold"}}
    inputs = {"x": 0.0}

    def __init__(self, name, latency=0.0, jitter=0.0, **kw):
        super().__init__(name, **kw)
        self.latency, self.jitter = latency, jitter

    def step(self, t, inputs):
        time.sleep(self.latency + random.random() * self.jitter)
        return {"u": float(inputs.x) * 2.0}


def _async_sim(tmp_path, n, latency, make_async, jitter=0.0):
    sim = _sim(tmp_path, end="2023-07-03 02:00")
    sim.add(pc.FunctionComponent(lambda t: float(t.hour * 60 + t.minute), name="src", outputs="x"))
    for i in range(n):
        comp = _Slow(f"agent{i}", latency, jitter)
        if make_async:
            comp.make_async(delay="15min")
        sim.add(comp)
        sim.connect("src.x", f"agent{i}.x")
    return sim


def test_async_outputs_apply_one_delay_later(tmp_path):
    res = _async_sim(tmp_path, 1, 0.0, True).run()
    df = res.df
    # the answer to the inputs at 00:15 (x=15) is published at 00:30
    assert df.loc["2023-07-03 00:30", "agent0.u"] == 30.0
    assert df.loc["2023-07-03 00:15", "agent0.u"] == 0.0
    assert res.meta["asynchronous"]["agent0"]["delivered"] == 7


def test_async_results_do_not_depend_on_latency(tmp_path):
    fast = _async_sim(tmp_path, 2, 0.0, True).run().df
    slow = _async_sim(tmp_path, 2, 0.02, True, jitter=0.03).run().df
    assert fast.equals(slow)


def test_async_calls_overlap(tmp_path):
    t0 = time.perf_counter()
    _async_sim(tmp_path, 4, 0.05, False).run()
    blocking = time.perf_counter() - t0
    t0 = time.perf_counter()
    _async_sim(tmp_path, 4, 0.05, True).run()
    overlapped = time.perf_counter() - t0
    assert overlapped < 0.6 * blocking


def test_async_deadline_keeps_previous_output_flagged_late(tmp_path):
    sim = _sim(tmp_path, end="2023-07-03 01:00")
    sim.add(pc.FunctionComponent(lambda t: 1.0, name="src", outputs="x"))
    slow = sim.add(_Slow("agent", latency=0.3).make_async(deadline=0.05))
    sim.connect("src.x", "agent.x")
    res = sim.run()
    assert res.meta["asynchronous"]["agent"]["late"] >= 1
    assert "late" in set(res.quality["quality"])
    assert (res["agent.u"] == 0.0).all()


def test_async_component_sees_state_of_its_own_step(tmp_path):
    seen = []

    class Peek(pc.Component):
        outputs = {"v": 0.0}

        def step(self, t, inputs):
            time.sleep(0.01)
            seen.append((t, self.ctx.now, self.ctx.latest("src.x")))
            return {"v": 1.0}

    sim = _sim(tmp_path, end="2023-07-03 01:00")
    sim.add(pc.FunctionComponent(lambda t: float(t.minute), name="src", outputs="x"))
    sim.add(Peek("peek").make_async(delay="30min"))
    sim.run()
    for t, now, x in seen:
        assert t == now and x == float(t.minute)


def test_async_rejects_bad_delay(tmp_path):
    sim = _sim(tmp_path)
    sim.add(_Slow("a").make_async(delay="10min"))
    with pytest.raises(ConfigurationError, match="decision delay"):
        sim.initialize()


# ---------------------------------------------------------- record / replay
class _Dice(pc.Component):
    """Non-deterministic on purpose (unseeded global RNG), like a sampled LLM answer."""

    outputs = {"sp": {"default": 24.0, "kind": "hold"}}
    inputs = {"T": 25.0}

    def step(self, t, inputs):
        return {"sp": 23.0 + random.random() * 3.0}


def _dice_sim(tmp_path, name, **kw):
    sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", name=name, output_dir=tmp_path,
                          log_level="WARNING", **kw)
    sim.add(pc.components.RCBuilding("house", 100))
    sim.add(_Dice("ai", step_size="1h"))
    sim.connect("house.T_zone", "ai.T")
    sim.connect("ai.sp", "house.clg_sp")
    return sim


def test_record_and_replay_reproduce_a_nondeterministic_run(tmp_path):
    live1 = _dice_sim(tmp_path, "live1", record=["ai"]).run()
    live2 = _dice_sim(tmp_path, "live2").run()
    assert not live1["house.T_zone"].equals(live2["house.T_zone"])
    sim = _dice_sim(tmp_path, "replay")
    sim.replay("ai", tmp_path / "live1")
    rep = sim.run()
    np.testing.assert_array_equal(rep["house.T_zone"].to_numpy(), live1["house.T_zone"].to_numpy())
    np.testing.assert_array_equal(rep["ai.sp"].to_numpy(), live1["ai.sp"].to_numpy())
    assert "replayed" in set(rep.quality["quality"])


def test_replay_from_results_object_and_counterfactual(tmp_path):
    live = _dice_sim(tmp_path, "live", record=True).run()
    sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", name="cf", output_dir=tmp_path, log_level="WARNING")
    sim.add(pc.components.RCBuilding("house", 200))  # a bigger house, same decisions
    sim.add(_Dice("ai", step_size="1h"))
    sim.connect("house.T_zone", "ai.T")
    sim.connect("ai.sp", "house.clg_sp")
    sim.replay("ai", live)
    cf = sim.run()
    np.testing.assert_array_equal(cf["ai.sp"].to_numpy(), live["ai.sp"].to_numpy())
    assert not np.allclose(cf["house.Q_cool"].to_numpy(), live["house.Q_cool"].to_numpy())


def test_replay_of_async_component_is_exact(tmp_path):
    def build(name, **kw):
        sim = _dice_sim(tmp_path, name, **kw)
        sim["ai"].make_async(delay="15min")
        return sim

    live = build("alive", record=["ai"]).run()
    sim = build("areplay")
    sim.replay("ai", tmp_path / "alive")
    rep = sim.run()
    np.testing.assert_array_equal(rep["house.T_zone"].to_numpy(), live["house.T_zone"].to_numpy())


def test_replay_requires_a_recording(tmp_path):
    _dice_sim(tmp_path, "norec").run()
    sim = _dice_sim(tmp_path, "x")
    with pytest.raises(ConfigurationError, match="No recorded messages"):
        sim.replay("ai", tmp_path / "norec")


# --------------------------------------------------------------- provenance
def test_lineage_follows_the_chain(tmp_path):
    sim = _sim(tmp_path, end="2023-07-03 01:00", trace=True, save=True)
    sim.add(pc.FunctionComponent(lambda t: float(t.minute), name="sensor", outputs="x"))
    sim.add(pc.FunctionComponent(lambda t, x=0.0: x + 1, name="ml", outputs="y"))
    sim.add(pc.FunctionComponent(lambda t, y=0.0: y * 2, name="ctrl", outputs="u"))
    sim.connect("sensor.x", "ml.x")
    sim.connect("ml.y", "ctrl.y")
    res = sim.run()
    lin = res.lineage("ctrl.u", "2023-07-03 00:30")
    assert set(lin["source"]) == {"ml.y", "sensor.x"}
    assert lin[lin["source"] == "sensor.x"]["source_value"].iloc[0] == 30.0
    loaded = pc.Results.load(tmp_path / "cosim")
    assert len(loaded.lineage("ctrl.u")) == 2


def test_messages_frame_has_validity(tmp_path):
    sim = _sim(tmp_path, end="2023-07-03 01:00", record=True)
    sim.add(_Ramp("bldg"))
    res = sim.run()
    m = res.messages[res.messages["ref"] == "bldg.P"]
    row = m.iloc[1]
    assert row["end"] - row["start"] == timedelta(minutes=15)
    assert row["end"] == row["time"]


def test_message_envelope_publishes_future_values(tmp_path):
    @pc.component(outputs={"price": {"default": 0.1, "kind": "hold"}}, step_size="1h")
    def utility(t):
        if t.hour == 0:  # announce a peak price for 02:00-03:00
            return pc.Message(0.5, start=t + timedelta(hours=2))
        return pc.HOLD

    sim = _sim(tmp_path, end="2023-07-03 04:00")
    sim.add(utility)
    sim.add(pc.FunctionComponent(lambda t, p=0.0: p, name="m", outputs="y"))
    sim.connect("utility.price", "m.p", read="at")
    res = sim.run()
    assert res.df.loc["2023-07-03 01:45", "m.y"] == 0.1
    assert res.df.loc["2023-07-03 02:00", "m.y"] == 0.5


def test_replay_rejects_a_different_clock(tmp_path):
    _dice_sim(tmp_path, "rec15", record=["ai"]).run()
    sim = pc.CoSimulation("2023-07-03", "2023-07-04", "5min", name="r5", output_dir=tmp_path, log_level="WARNING")
    sim.add(pc.components.RCBuilding("house", 100))
    sim.add(_Dice("ai", step_size="1h"))
    sim.connect("house.T_zone", "ai.T")
    sim.connect("ai.sp", "house.clg_sp")
    sim.replay("ai", tmp_path / "rec15")
    with pytest.raises(ConfigurationError, match="master step"):
        sim.initialize()


def test_async_answers_queued_behind_a_late_call_are_flagged(tmp_path):
    sim = _sim(tmp_path, end="2023-07-03 02:00")
    sim.add(pc.FunctionComponent(lambda t: 1.0, name="src", outputs="x"))
    # a decision delay longer than the step queues several calls behind a slow one
    sim.add(_Slow("agent", latency=0.25).make_async(delay="1h", deadline=0.05))
    sim.connect("src.x", "agent.x")
    res = sim.run()
    meta = res.meta["asynchronous"]["agent"]
    late = res.quality.set_index("quality")["messages"].get("late", 0)
    assert meta["late"] == late >= 2
    assert meta["delivered"] + meta["late"] == 4  # every answer due before the end is accounted for
