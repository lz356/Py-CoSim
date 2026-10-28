"""Tests of the master algorithm (pycosim.core.master.CoSimulation) and Results."""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import pycosim as pc
from pycosim.core.component import Component, component
from pycosim.core.errors import ComponentError, ConfigurationError, PyCoSimError
from pycosim.core.observers import CallbackObserver, CSVStreamRecorder, LivePlot, Observer, ProgressBar
from pycosim.core.results import Results

T0 = datetime(2023, 7, 3)


# ===================================================================== helpers
class Plant(Component):
    """x[k+1] = a x[k] + b u[k] (state-space, no feedthrough)."""

    direct_feedthrough = False
    inputs = {"u": 0.0}
    outputs = {"x": None}

    def __init__(self, name, x0, a, b, log=None, **kw):
        super().__init__(name, **kw)
        self.x, self.a, self.b = float(x0), a, b
        self.output_ports["x"].default = float(x0)
        self.log = log if log is not None else []

    def output(self, t):
        self.log.append(("output", self.name, t))
        return {"x": self.x}

    def update(self, t, inputs):
        self.log.append(("update", self.name, t))
        self.x = self.a * self.x + self.b * inputs.u


class PController(Component):
    inputs = {"x": 0.0}
    outputs = {"u": 0.0}

    def __init__(self, name, K, r, log=None):
        super().__init__(name)
        self.K, self.r = K, r
        self.log = log if log is not None else []

    def step(self, t, inputs):
        self.log.append(("step", self.name, t))
        return self.K * (self.r - inputs.x)


class Counter(Component):
    """Publishes how often it has been called."""

    outputs = {"n": 0}

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.calls = 0

    def step(self, t, inputs):
        self.calls += 1
        return self.calls


class Recorder(Component):
    """Records the inputs it receives; output = number of calls."""

    outputs = {"n": 0}

    def __init__(self, name, inputs=None, **kw):
        super().__init__(name, **kw)
        for key, default in (inputs or {"x": None}).items():
            self.add_input(key, default=default)
        self.seen = []

    def step(self, t, inputs):
        self.seen.append(dict(inputs))
        return len(self.seen)


class Source(Component):
    """Outputs a fixed value per port."""

    def __init__(self, name, **values):
        super().__init__(name)
        self.values = values
        for key in values:
            self.add_output(key)

    def step(self, t, inputs):
        return dict(self.values)


class Boom(Component):
    inputs = {"x": 0.0}
    outputs = {"y": 0.0}

    def __init__(self, name, fail_at=3, phase="step", **kw):
        super().__init__(name, **kw)
        self.fail_at, self.phase = fail_at, phase
        self.k = -1
        self.aborted = False
        if phase in ("output", "update"):
            self.direct_feedthrough = False

    def _maybe_fail(self, phase):
        if phase == self.phase and self.k == self.fail_at:
            raise ValueError(f"boom in {phase} at k={self.k}")

    def step(self, t, inputs):
        self.k += 1
        self._maybe_fail("step")
        return inputs.x + 1

    def output(self, t):
        self.k += 1
        self._maybe_fail("output")
        return {"y": float(self.k)}

    def update(self, t, inputs):
        self._maybe_fail("update")

    def abort(self):
        self.aborted = True


# ============================================================ closed loop / analytic
def closed_loop_sim(make_sim, log=None, **kw):
    sim = make_sim(start="2023-07-03", end="2023-07-03 05:00", step="15min", **kw)
    sim.add(Plant("plant", x0=10.0, a=0.9, b=0.5, log=log), PController("ctrl", K=0.8, r=20.0, log=log))
    sim.connect("plant.x", "ctrl.x")
    sim.connect("ctrl.u", "plant.u")
    return sim


def test_closed_loop_matches_analytic_trajectory(make_sim):
    res = closed_loop_sim(make_sim).run()
    k = np.arange(20)
    # x[k+1] = (a - bK) x[k] + bK r  ->  x[k] = x* + (x0 - x*) (a - bK)^k with x* = 16
    expected_x = 16.0 - 6.0 * 0.5 ** k
    np.testing.assert_allclose(res["plant.x"].to_numpy(), expected_x, rtol=0, atol=1e-12)
    np.testing.assert_allclose(res["ctrl.u"].to_numpy(), 0.8 * (20.0 - expected_x), rtol=0, atol=1e-12)
    assert len(res.df) == 20
    assert res.df.index[0] == T0 and res.df.index[-1] == T0 + timedelta(hours=4, minutes=45)
    assert res.ok and res.error is None


def test_three_phase_call_order(make_sim):
    log = []
    sim = closed_loop_sim(make_sim, log=log)
    sim.add(Plant("plant2", x0=0.0, a=1.0, b=0.0, log=log))
    sim.run()
    t1 = T0 + timedelta(minutes=15)
    first_two_steps = [(phase, name, t) for phase, name, t in log if t in (T0, t1)]
    assert first_two_steps == [
        ("output", "plant", T0), ("output", "plant2", T0), ("step", "ctrl", T0),
        ("update", "plant", T0), ("update", "plant2", T0),
        ("output", "plant", t1), ("output", "plant2", t1), ("step", "ctrl", t1),
        ("update", "plant", t1), ("update", "plant2", t1),
    ]


def test_step_by_step_equals_run(make_sim):
    reference = closed_loop_sim(make_sim, name="ref").run()
    sim = closed_loop_sim(make_sim, name="stepwise")
    assert sim.status == "created"
    flags = []
    while True:
        more = sim.step()
        flags.append(more)
        if not more:
            break
    assert flags == [True] * 19 + [False]
    assert sim.done and sim.now == T0 + timedelta(hours=4, minutes=45)
    res = sim.finalize()
    pd.testing.assert_frame_equal(res.df, reference.df)


def test_step_after_end_raises(make_sim):
    sim = closed_loop_sim(make_sim)
    while sim.step():
        pass
    with pytest.raises(PyCoSimError, match="already reached its end"):
        sim.step()


def test_step_after_end_keeps_run_finalizable(make_sim):
    sim = closed_loop_sim(make_sim)
    while sim.step():
        pass
    with pytest.raises(PyCoSimError):
        sim.step()
    assert sim.status == "running"
    assert len(sim.finalize().df) == 20


def test_partial_steps_then_run(make_sim):
    reference = closed_loop_sim(make_sim, name="ref").run()
    sim = closed_loop_sim(make_sim, name="mixed")
    for _ in range(5):
        sim.step()
    assert sim.k == 4 and sim.now == T0 + timedelta(hours=1)
    res = sim.run()
    pd.testing.assert_frame_equal(res.df, reference.df)
    with pytest.raises(PyCoSimError, match="finalize"):
        sim.finalize()


def test_status_transitions(make_sim):
    sim = closed_loop_sim(make_sim)
    assert sim.status == "created" and sim.now == datetime.min
    sim.initialize()
    assert sim.status == "running" and sim.now == T0 and not sim.done
    with pytest.raises(ConfigurationError, match="Cannot initialize"):
        sim.initialize()
    with pytest.raises(ConfigurationError, match="before the simulation is initialized"):
        sim.add(Counter("late"))
    with pytest.raises(ConfigurationError, match="before the simulation is initialized"):
        sim.connect("plant.x", "ctrl.x")
    res = sim.run()
    assert sim.status == "finished"
    assert res.meta["completed_steps"] == 20 and res.meta["n_steps"] == 20


# ================================================================ multi-rate
def test_multi_rate_outputs_are_held(make_sim):
    sim = make_sim(start="2023-07-03", end="2023-07-03 03:00", step="15min")
    slow = sim.add(Counter("slow", step_size="1h"))
    fast = sim.add(Recorder("fast", inputs={"n": None}))
    sim.connect("slow.n", "fast.n")
    res = sim.run()
    assert slow.calls == 3
    assert list(res["slow.n"]) == [1] * 4 + [2] * 4 + [3] * 4
    assert [s["n"] for s in fast.seen] == [1] * 4 + [2] * 4 + [3] * 4
    assert slow.ctx.step_size == 3600 and slow.ctx.step_multiple == 4 and fast.ctx.step_size == 900
    timing = res.timing.set_index("component")
    assert timing.loc["slow", "calls"] == 3 and timing.loc["fast", "calls"] == 12


def test_slow_consumer_samples_fast_source(make_sim):
    sim = make_sim(start="2023-07-03", end="2023-07-03 02:00", step="15min")
    sim.add(Counter("fast"), Recorder("slow", inputs={"n": None}, step_size="30min"))
    sim.connect("fast.n", "slow.n")
    sim.run()
    assert [s["n"] for s in sim["slow"].seen] == [1, 3, 5, 7]


def test_multi_rate_state_component(make_sim):
    log = []
    sim = make_sim(start="2023-07-03", end="2023-07-03 02:00", step="30min")
    plant = sim.add(Plant("plant", x0=1.0, a=2.0, b=0.0, log=log, step_size="1h"))
    res = sim.run()
    assert [(p, t.hour, t.minute) for p, _, t in log] == [("output", 0, 0), ("update", 0, 0),
                                                        ("output", 1, 0), ("update", 1, 0)]
    assert list(res["plant.x"]) == [1.0, 1.0, 2.0, 2.0]
    assert plant.x == 4.0


def test_step_size_not_multiple_of_master(make_sim):
    sim = make_sim()
    sim.add(Counter("c", step_size="10min"))
    with pytest.raises(ConfigurationError, match="not a positive integer multiple"):
        sim.initialize()


# ================================================== connections / data flow
def test_fan_in_reducers_and_transform(make_sim):
    sim = make_sim(end="2023-07-03 00:30")
    sim.add(Source("a", y=1.0), Source("b", y=2.0), Source("c", y=6.0))
    rec = sim.add(Recorder("rec", inputs={k: None for k in ("s", "m", "mx", "mn", "l", "f", "kw", "tsum")}))
    srcs = ["a.y", "b.y", "c.y"]
    sim.connect(srcs, "rec.s", reduce="sum")
    sim.connect(srcs, "rec.m", reduce="mean")
    sim.connect(srcs, "rec.mx", reduce="max")
    sim.connect(srcs, "rec.mn", reduce="min")
    sim.connect(srcs, "rec.l", reduce="list")
    sim.connect(srcs, "rec.f", reduce=lambda xs: xs[0] * xs[1] * xs[2])
    sim.connect("c.y", "rec.kw", transform=lambda w: w / 1000.0)
    sim.connect(srcs, "rec.tsum", reduce="sum", transform=lambda v: -v)
    sim.run()
    assert rec.seen[0] == {"s": 9.0, "m": 3.0, "mx": 6.0, "mn": 1.0, "l": [1.0, 2.0, 6.0], "f": 12.0,
                           "kw": 0.006, "tsum": -9.0}


def test_connection_validation_errors(make_sim):
    sim = make_sim()
    sim.add(Source("a", y=1.0), Source("b", y=2.0), Recorder("rec"))
    with pytest.raises(ConfigurationError, match="pass reduce="):
        sim.connect(["a.y", "b.y"], "rec.x")
    with pytest.raises(ConfigurationError, match="Unknown reduce"):
        sim.connect(["a.y", "b.y"], "rec.x", reduce="median")
    sim.connect("a.y", "rec.x")
    with pytest.raises(ConfigurationError, match="already connected"):
        sim.connect("b.y", "rec.x")


@pytest.mark.parametrize(
    "src, dst, message",
    [
        ("zz.y", "rec.x", "unknown component 'zz'"),
        ("a.y", "zz.x", "unknown component 'zz'"),
        ("a.nope", "rec.x", r"'a' has no output 'nope'. Outputs: \['y'\]"),
        ("a.y", "rec.nope", r"'rec' has no input 'nope'. Inputs: \['x'\]"),
        ("rec.x", "rec.x", "has no output 'x'"),
    ],
)
def test_bad_connections_detected_at_initialize(make_sim, src, dst, message):
    sim = make_sim()
    sim.add(Source("a", y=1.0), Recorder("rec"))
    sim.connect(src, dst)
    with pytest.raises(ConfigurationError, match=message):
        sim.initialize()


def test_connect_accepts_portrefs_and_pairs(make_sim):
    sim = make_sim(end="2023-07-03 00:15")
    a = sim.add(Source("a", y=1.0, z=2.0))
    rec = sim.add(Recorder("rec", inputs={"p": None, "q": None, "r": None}))
    sim.connect(a["y"], rec["p"])
    sim.connect((a, "z"), "rec.q")
    sim.connect([a["y"], (a, "z")], ("rec", "r"), reduce="sum")
    sim.run()
    assert rec.seen == [{"p": 1.0, "q": 2.0, "r": 3.0}]


def test_connect_matching(make_sim):
    sim = make_sim(end="2023-07-03 00:15")
    sim.add(Source("src", T_out=30.0, GHI=500.0, other=1.0))
    rec = sim.add(Recorder("rec", inputs={"T_out": None, "GHI": None, "unrelated": 0.0}))
    conns = sim.connect_matching("src", rec)
    assert [str(c) for c in conns] == ["src.T_out -> rec.T_out", "src.GHI -> rec.GHI"]
    sim.run()
    assert rec.seen == [{"T_out": 30.0, "GHI": 500.0, "unrelated": 0.0}]
    sim2 = make_sim()
    sim2.add(Source("src", a=1.0), Recorder("rec"))
    with pytest.raises(ConfigurationError, match="No matching port names"):
        sim2.connect_matching("src", "rec")
    sim3 = make_sim()
    sim3.add(Source("src", T_out=1.0, GHI=2.0), Recorder("rec", inputs={"T_out": None, "GHI": None}))
    assert len(sim3.connect_matching("src", "rec", ports=["GHI"], delay=True)) == 1
    assert sim3.connections[0].delay


def test_delay_reads_previous_step(make_sim):
    sim = make_sim(end="2023-07-03 01:00")
    sim.add(Counter("cnt"))
    rec = sim.add(Recorder("rec", inputs={"now": None, "before": -1}))
    sim.connect("cnt.n", "rec.now")
    sim.connect("cnt.n", "rec.before", delay=True)
    sim.run()
    assert [s["now"] for s in rec.seen] == [1, 2, 3, 4]
    # at k=0 the delayed input sees the source's *initial* output value (port default 0)
    assert [s["before"] for s in rec.seen] == [0, 1, 2, 3]


def test_delay_without_initial_value_uses_input_default(make_sim):
    class NoInit(Component):
        outputs = {"n": None}

        def __init__(self, name):
            super().__init__(name)
            self.calls = 0

        def step(self, t, inputs):
            self.calls += 1
            return self.calls

    sim = make_sim(end="2023-07-03 00:45")
    sim.add(NoInit("src"))
    rec = sim.add(Recorder("rec", inputs={"before": -1}))
    sim.connect("src.n", "rec.before", delay=True)
    sim.run()
    assert [s["before"] for s in rec.seen] == [-1, 1, 2]


def test_delayed_feedback_loop_runs(make_sim):
    @component(outputs="y")
    def inc(t, x=0.0):
        return x + 1.0

    @component(outputs="y")
    def double(t, x=0.0):
        return 2.0 * x

    sim = make_sim(end="2023-07-03 01:00")
    sim.add(double, inc)
    sim.connect("inc.y", "double.x")
    sim.connect("double.y", "inc.x", delay=True)
    res = sim.run()
    # inc[k] = double[k-1] + 1, double[k] = 2 inc[k], double[-1] = None -> inc default 0
    assert list(res["inc.y"]) == [1.0, 3.0, 7.0, 15.0]
    assert list(res["double.y"]) == [2.0, 6.0, 14.0, 30.0]


def test_unconnected_inputs_get_defaults(make_sim):
    sim = make_sim(end="2023-07-03 00:30")
    rec = sim.add(Recorder("rec", inputs={"a": 3.5, "b": None, "c": "text"}))
    sim.run()
    assert rec.seen == [{"a": 3.5, "b": None, "c": "text"}] * 2


def test_connected_source_without_value_falls_back_to_default(make_sim):
    @component(outputs="y")
    def silent(t):
        return None  # never publishes; port initial is None

    sim = make_sim(end="2023-07-03 00:30")
    sim.add(silent)
    rec = sim.add(Recorder("rec", inputs={"x": 42.0}))
    sim.connect("silent.y", "rec.x")
    res = sim.run()
    assert [s["x"] for s in rec.seen] == [42.0, 42.0]
    assert res["silent.y"].isna().all()


def test_returning_none_holds_previous_outputs(make_sim):
    @component(outputs=["a", "b"])
    def sometimes(t):
        if t.minute == 0:
            return {"a": float(t.hour), "b": "set"}
        if t.minute == 30:
            return {"a": -1.0}  # partial update: b is held
        return None

    sim = make_sim(end="2023-07-03 01:00")
    sim.add(sometimes)
    res = sim.run()
    assert list(res["sometimes.a"]) == [0.0, 0.0, -1.0, -1.0]
    assert list(res["sometimes.b"]) == ["set"] * 4


# ======================================================================= clock
def test_clock_requires_start_end_and_step(tmp_path):
    for kwargs, msg in [({"step": "15min"}, "start/end unknown"),
                        ({"start": "2023-07-03", "step": "15min"}, "start/end unknown"),
                        ({"start": "2023-07-03", "end": "2023-07-04"}, "Master step unknown")]:
        sim = pc.CoSimulation(output_dir=tmp_path, save=False, log_level="WARNING", **kwargs)
        sim.add(Counter("c"))
        with pytest.raises(ConfigurationError, match=msg):
            sim.initialize()


@pytest.mark.parametrize("start, end, step, msg", [
    ("2023-07-04", "2023-07-03", "15min", "must be after start"),
    ("2023-07-03", "2023-07-03", "15min", "must be after start"),
    ("2023-07-03 00:00", "2023-07-03 00:10", "15min", "shorter than one master step"),
])
def test_invalid_windows(make_sim, start, end, step, msg):
    sim = make_sim(start=start, end=end, step=step)
    sim.add(Counter("c"))
    with pytest.raises(ConfigurationError, match=msg):
        sim.initialize()


def test_invalid_step_string(make_sim):
    sim = make_sim(step="soon")
    sim.add(Counter("c"))
    with pytest.raises(ValueError):
        sim.initialize()


def test_window_truncated_to_whole_steps(make_sim, caplog):
    sim = make_sim(start="2023-07-03 00:00", end="2023-07-03 01:10", step="15min")
    sim.add(Counter("c"))
    with caplog.at_level(logging.WARNING, logger="pycosim"):
        res = sim.run()
    assert sim.n_steps == 4 and sim.end == T0 + timedelta(hours=1)
    assert len(res.df) == 4
    assert any("not a multiple of the step" in r.getMessage() for r in caplog.records)


def test_clock_from_suggest_clock_and_overrides(tmp_path):
    class Clocked(Counter):
        def suggest_clock(self):
            return {"start": datetime(2023, 1, 1), "end": datetime(2023, 1, 2), "step": "1h"}

    sim = pc.CoSimulation(output_dir=tmp_path, save=False, log_level="WARNING")
    sim.add(Counter("plain"), Clocked("clocked"))
    sim.initialize()
    assert (sim.start, sim.end, sim.dt, sim.n_steps) == (datetime(2023, 1, 1), datetime(2023, 1, 2), 3600.0, 24)

    sim2 = pc.CoSimulation(end="2023-01-01 06:00", step="30min", output_dir=tmp_path, save=False, log_level="WARNING")
    sim2.add(Clocked("clocked"))
    sim2.initialize()
    assert (sim2.start, sim2.end, sim2.dt, sim2.n_steps) == (datetime(2023, 1, 1), datetime(2023, 1, 1, 6), 1800.0, 12)


def test_no_components(make_sim):
    with pytest.raises(ConfigurationError, match="No components"):
        make_sim().initialize()


def test_add_validation(make_sim):
    sim = make_sim()
    with pytest.raises(ConfigurationError, match="is not a Component"):
        sim.add(lambda t: 1)
    a, b = sim.add(Counter("a"), Counter("b"))
    assert (a.name, b.name) == ("a", "b")
    assert sim.add(Counter("c")).name == "c"
    with pytest.raises(ConfigurationError, match="already exists"):
        sim.add(Counter("a"))
    assert sim["a"] is a and "a" in sim and "zz" not in sim
    with pytest.raises(KeyError, match="Known: a, b, c"):
        sim["zz"]
    assert [c.name for c in sim.components] == ["a", "b", "c"]


# =================================================================== failures
def test_exception_in_step_produces_error_report_and_partial_results(tmp_path):
    sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", name="failing", output_dir=tmp_path,
                          save=True, log_level="CRITICAL")
    src = sim.add(Counter("src"))
    boom = sim.add(Boom("boom", fail_at=3))
    sim.connect("src.n", "boom.x")
    ends = []
    sim.add_observer(CallbackObserver(lambda s, k: None))
    sim.add_observer(type("End", (Observer,), {"on_end": lambda self, s, r: ends.append(r)})())
    with pytest.raises(ComponentError) as info:
        sim.run()
    err = info.value
    assert err.component == "boom" and err.phase == "step"
    assert err.time == T0 + timedelta(minutes=45)
    assert isinstance(err.original, ValueError) and isinstance(err.__cause__, ValueError)
    assert sim.status == "failed"
    assert boom.aborted
    report_path = tmp_path / "failing" / "error_report.txt"
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    for needle in ("component  : boom", "phase      : step", "x = 4", "ValueError: boom in step at k=3",
                   "src.n -> boom.x", "2023-07-03 00:45:00"):
        assert needle in report, needle
    partial = sim.partial_results
    assert not partial.ok and "boom in step" in partial.error
    assert list(partial["src.n"]) == [1, 2, 3, 4]
    assert len(partial.df) == 4
    # partial results were saved as well, and observers were told about the end
    assert (tmp_path / "failing" / "timeseries.csv").exists()
    assert ends == [partial]
    with pytest.raises(PyCoSimError, match="state 'failed'"):
        sim.step()
    assert src.calls == 4


def test_completed_steps_after_failure(make_sim):
    sim = make_sim()
    sim.add(Boom("boom", fail_at=3))
    with pytest.raises(ComponentError):
        sim.run()
    assert sim.partial_results.meta["completed_steps"] == 3


@pytest.mark.parametrize("phase", ["output", "update"])
def test_exception_in_state_component_phases(make_sim, phase):
    sim = make_sim()
    sim.add(Boom("boom", fail_at=2, phase=phase))
    with pytest.raises(ComponentError) as info:
        sim.run()
    assert info.value.phase == phase and info.value.time == T0 + timedelta(minutes=30)


def test_exception_in_setup_aborts_previous_components(tmp_path):
    aborted = []

    class Tracked(Counter):
        def abort(self):
            aborted.append(self.name)

    class BadSetup(Tracked):
        def setup(self, ctx):
            raise RuntimeError("cannot start")

    sim = pc.CoSimulation("2023-07-03", "2023-07-04", "15min", name="setupfail", output_dir=tmp_path,
                          save=False, log_level="CRITICAL")
    sim.add(Tracked("first"), Tracked("second"), BadSetup("bad"), Tracked("never"))
    with pytest.raises(ComponentError) as info:
        sim.run()
    assert info.value.phase == "setup" and info.value.time is None and info.value.component == "bad"
    assert aborted == ["bad", "second", "first"]  # the failing component too (it may have started something)
    assert sim.status == "failed"
    report = (tmp_path / "setupfail" / "error_report.txt").read_text(encoding="utf-8")
    assert "before first step" in report and "RuntimeError: cannot start" in report


def test_ready_runs_after_all_setups_and_failures_abort_everything(make_sim):
    calls = []

    class Slow(Counter):
        def setup(self, ctx):
            calls.append(("setup", self.name))

        def ready(self):
            calls.append(("ready", self.name))
            if self.name == "b":
                raise TimeoutError("external tool did not start")

        def abort(self):
            calls.append(("abort", self.name))

    sim = make_sim(log_level="CRITICAL")
    sim.add(Slow("a"), Slow("b"), Slow("c"))
    with pytest.raises(ComponentError) as info:
        sim.run()
    assert info.value.component == "b" and info.value.phase == "ready"
    assert calls == [("setup", "a"), ("setup", "b"), ("setup", "c"), ("ready", "a"), ("ready", "b"),
                     ("abort", "c"), ("abort", "b"), ("abort", "a")]


def test_partial_results_attribute_exists_after_setup_failure(make_sim):
    class BadSetup(Counter):
        def setup(self, ctx):
            raise RuntimeError("cannot start")

    sim = make_sim(log_level="CRITICAL")
    sim.add(BadSetup("bad"))
    with pytest.raises(ComponentError):
        sim.run()
    assert sim.partial_results is None


def test_exception_in_finalize_still_finalizes_others(make_sim):
    finalized = []

    class Fin(Counter):
        def finalize(self):
            finalized.append(self.name)

    class BadFin(Counter):
        def finalize(self):
            raise OSError("disk full")

    sim = make_sim(end="2023-07-03 01:00", log_level="CRITICAL")
    sim.add(BadFin("bad"), Fin("good"))
    with pytest.raises(ComponentError) as info:
        sim.run()
    assert info.value.phase == "finalize" and info.value.component == "bad"
    assert finalized == ["good"]
    assert sim.status == "finished"


def test_framework_error_inside_component_is_wrapped(make_sim):
    class Raises(Counter):
        def step(self, t, inputs):
            raise ConfigurationError("bad config at runtime")

    sim = make_sim(log_level="CRITICAL")
    sim.add(Raises("r"))
    with pytest.raises(ComponentError) as info:
        sim.run()
    assert isinstance(info.value.original, ConfigurationError)


# ============================================================= reproducibility
class Noise(Component):
    outputs = {"v": None}

    def step(self, t, inputs):
        return float(self.ctx.rng.normal())


def noise_run(make_sim, seed, names=("n1", "n2"), **kw):
    sim = make_sim(end="2023-07-03 02:00", seed=seed, **kw)
    for n in names:
        sim.add(Noise(n))
    return sim.run()


def test_seed_reproducibility(make_sim):
    a = noise_run(make_sim, 7, name="a")
    b = noise_run(make_sim, 7, name="b")
    c = noise_run(make_sim, 8, name="c")
    pd.testing.assert_frame_equal(a.df, b.df)
    assert not np.allclose(a["n1.v"], c["n1.v"])
    # streams are independent per component name ...
    assert not np.allclose(a["n1.v"], a["n2.v"])
    # ... and do not depend on the other components present
    d = noise_run(make_sim, 7, names=("zz", "n1"), name="d")
    np.testing.assert_array_equal(a["n1.v"].to_numpy(), d["n1.v"].to_numpy())


def test_component_rng_property_after_setup(make_sim):
    sim = make_sim(end="2023-07-03 00:15")
    n = sim.add(Noise("n"))
    sim.initialize()
    assert n.rng is n.ctx.rng


# ===================================================================== context
def test_context_features(make_sim, tmp_path):
    got = {}

    class Probe(Component):
        outputs = {"y": 0.0}

        def setup(self, ctx):
            got["setup"] = (ctx.start, ctx.end, ctx.dt, ctx.step_size, ctx.n_steps, ctx.component)

        def step(self, t, inputs):
            ctx = self.ctx
            assert ctx.now == t
            got.setdefault("k", []).append(ctx.step_index)
            got["src_latest"] = ctx.latest("src.n")
            got["unknown"] = ctx.latest("nope.x", default="dflt")
            got["hist"] = list(ctx.history("src.n", n=2))
            if ctx.step_index == 2:
                ctx.log("hello", kind="note", value=3)
            got["dir"] = ctx.output_dir
            got["td"] = ctx.step_timedelta
            return float(ctx.step_index)

    sim = make_sim(end="2023-07-03 01:00", name="ctxrun")
    sim.add(Counter("src"), Probe("probe"))  # not connected: read through the bus
    res = sim.run()
    assert got["setup"] == (T0, T0 + timedelta(hours=1), 900.0, 900.0, 4, "probe")
    assert got["k"] == [0, 1, 2, 3]
    assert got["src_latest"] == 4
    assert got["unknown"] == "dflt"
    assert got["hist"] == [3, 4]
    assert got["dir"] == tmp_path / "ctxrun" / "probe" and got["dir"].is_dir()
    assert got["td"] == timedelta(minutes=15)
    ev = res.events
    assert len(ev) == 1
    row = ev.iloc[0]
    assert (row["source"], row["kind"], row["message"], row["value"]) == ("probe", "note", "hello", 3)
    assert row["time"] == T0 + timedelta(minutes=30)


def test_context_log_during_setup_has_no_time(make_sim):
    class LogsInSetup(Counter):
        def setup(self, ctx):
            ctx.log("starting", kind="info")

    sim = make_sim(end="2023-07-03 00:15")
    sim.add(LogsInSetup("c"))
    res = sim.run()
    assert res.events.iloc[0]["time"] is None or pd.isna(res.events.iloc[0]["time"])


# ===================================================================== describe
def test_describe(make_sim):
    sim = closed_loop_sim(make_sim, name="desc")
    text = sim.describe()
    assert text.startswith("CoSimulation 'desc'")
    assert "window" not in text
    assert "plant [Plant, state-based (no feedthrough), step=master]" in text
    assert "ctrl [PController, feedthrough, step=master]" in text
    assert "plant.x -> ctrl.x" in text and "ctrl.u -> plant.u" in text
    assert "execution: output[plant] -> step[ctrl] -> update[plant]" in text
    sim.initialize()
    assert "window: 2023-07-03 00:00:00 -> 2023-07-03 05:00:00, step 15min (20 steps)" in sim.describe()


# ===================================================================== results
def results_sim(make_sim, **kw):
    @component(outputs=["mode", "power"])
    def device(t, x=0.0):
        return ("on" if t.minute < 30 else "off"), 100.0 + t.minute

    sim = make_sim(start="2023-07-03", end="2023-07-03 03:00", **kw)
    sim.add(Counter("cnt"), device)
    sim["device"].output_ports["power"].unit = "W"
    sim.connect("cnt.n", "device.x")
    return sim


def test_results_save_load_round_trip(make_sim, tmp_path):
    sim = results_sim(make_sim, name="persist", save=True)
    sim.add(type("Logger", (Component,), {
        "outputs": {"z": 0.0},
        "step": lambda self, t, inputs: (self.ctx.log("tick", kind="note") if t.minute == 0 else None) or 0.0,
    })("logger"))
    res = sim.run()
    folder = tmp_path / "persist"
    for f in ("timeseries.csv", "events.csv", "timing.csv", "meta.json"):
        assert (folder / f).exists(), f
    assert not (folder / "error_report.txt").exists()
    loaded = Results.load(folder)
    pd.testing.assert_frame_equal(loaded.df, res.df, check_dtype=False, check_freq=False)
    assert loaded.meta == json.loads(json.dumps(res.meta, default=str))
    assert len(loaded.events) == len(res.events) == 3
    assert list(loaded.timing["component"]) == list(res.timing["component"])
    assert loaded.ok


def test_results_save_load_keeps_units(make_sim, tmp_path):
    res = results_sim(make_sim, name="units", save=True).run()
    assert res.units["device.power"] == "W"
    assert Results.load(tmp_path / "units").units == res.units


def test_results_save_load_with_error(tmp_path):
    r = Results(pd.DataFrame({"a.x": [1.0]}, index=pd.DatetimeIndex([T0], name="time")),
                pd.DataFrame(columns=["time", "source", "kind", "message"]),
                pd.DataFrame([{"component": "a", "calls": 1, "total_s": 0.1, "mean_ms": 100.0}]), {"name": "x"},
                error="it failed")
    folder = r.save(tmp_path / "out")
    loaded = Results.load(folder)
    assert loaded.error == "it failed" and not loaded.ok


def test_results_accessors(make_sim):
    res = results_sim(make_sim, name="acc").run()
    assert "cnt.n" in res and "nope.x" not in res
    assert list(res["cnt.n"])[:3] == [1, 2, 3]
    dev = res.component("device")
    assert list(dev.columns) == ["mode", "power"]
    assert list(dev["mode"])[:4] == ["on", "on", "off", "off"]
    with pytest.raises(KeyError, match="nope"):
        res.component("nope")
    hourly = res.resample("1h")
    assert list(hourly.columns) == ["cnt.n", "device.power"]  # strings are dropped
    assert list(hourly["cnt.n"]) == [2.5, 6.5, 10.5]
    assert hourly["device.power"].iloc[0] == pytest.approx(122.5)
    assert res.units["device.power"] == "W"
    assert res.wall_time > 0
    text = res.summary()
    assert "Py-CoSim run 'acc'" in text and "status : OK" in text and "12 steps" in text
    assert repr(res) == text
    meta = res.meta
    assert meta["components"] == {"cnt": "Counter", "device": "FunctionComponent"}
    assert meta["connections"] == ["cnt.n -> device.x"]
    assert meta["step"] == "15min" and meta["step_s"] == 900.0 and meta["seed"] == 0
    assert meta["pycosim_version"] == pc.__version__


def test_results_plot(make_sim):
    import matplotlib.figure

    res = results_sim(make_sim, name="plot").run()
    fig = res.plot(["cnt.n", "device.power"], "device.power", title="demo")
    assert isinstance(fig, matplotlib.figure.Figure)
    assert len(fig.axes) == 2
    labels = [t.get_text() for t in fig.axes[0].get_legend().get_texts()]
    assert labels == ["cnt.n", "device.power [W]"]
    assert fig.axes[0].get_title() == "demo"
    fig2 = res.plot(resample="1h")
    assert len(fig2.axes) == 1


# =================================================================== observers
def test_callback_observer_every(make_sim):
    calls = []
    sim = closed_loop_sim(make_sim)
    sim.add_observer(CallbackObserver(lambda s, k: calls.append((k, s.now)), every=5))
    sim.run()
    assert [k for k, _ in calls] == [0, 5, 10, 15]
    assert calls[1][1] == T0 + timedelta(minutes=75)


def test_add_observer_type_check(make_sim):
    with pytest.raises(ConfigurationError):
        make_sim().add_observer(lambda s, k: None)


def test_progress_bar(make_sim):
    stream = io.StringIO()
    sim = closed_loop_sim(make_sim)
    sim.add_observer(ProgressBar(stream=stream, min_interval=0.0, width=10))
    sim.run()
    text = stream.getvalue()
    assert "[##########] 100.0%" in text and text.endswith("\n")
    assert "2023-07-03 04:45" in text


def test_progress_flag_adds_progress_bar(make_sim, capsys):
    sim = closed_loop_sim(make_sim, progress=True)
    sim.run()
    assert "100.0%" in capsys.readouterr().err


def test_csv_stream_recorder(make_sim, tmp_path):
    path = tmp_path / "stream" / "live.csv"
    sim = closed_loop_sim(make_sim)
    sim.add_observer(CSVStreamRecorder(path, refs=["plant.x"], every=2))
    sim.run()
    df = pd.read_csv(path)
    assert list(df.columns) == ["time", "plant.x"]
    assert len(df) == 10
    assert df["time"].iloc[1] == "2023-07-03T00:30:00"
    assert df["plant.x"].iloc[0] == pytest.approx(10.0)


def test_csv_stream_recorder_all_refs(make_sim, tmp_path):
    path = tmp_path / "all.csv"
    sim = closed_loop_sim(make_sim)
    sim.add_observer(CSVStreamRecorder(path))
    sim.run()
    df = pd.read_csv(path)
    assert list(df.columns) == ["time", "plant.x", "ctrl.u"] and len(df) == 20


@pytest.mark.filterwarnings("ignore:FigureCanvasAgg is non-interactive")
def test_live_plot_with_agg_backend(make_sim):
    import matplotlib.pyplot as plt

    sim = closed_loop_sim(make_sim)
    live = sim.add_observer(LivePlot([["plant.x", "ctrl.u"], "plant.x"], every=1000, window=8, title="live"))
    sim.run()
    assert live._fig is not None and len(live._axes) == 2
    line = live._lines[0][0]
    assert len(line.get_xdata()) == 8  # scrolling window
    np.testing.assert_allclose(line.get_ydata(), 16.0 - 6.0 * 0.5 ** np.arange(12, 20))
    assert live._axes[0].get_title() == "live"
    plt.close(live._fig)
