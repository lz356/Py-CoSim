"""Tests of the in-memory data bus (pycosim.core.bus)."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import pycosim as pc
from pycosim.core.bus import DataBus

T0 = datetime(2023, 7, 3)
TIMES = [T0 + timedelta(minutes=15 * k) for k in range(6)]


def run_bus(declare, publications, n_steps=6):
    """``publications``: {k: {ref: value}} published at master step k."""
    bus = DataBus(TIMES)
    for ref, initial in declare.items():
        bus.declare(ref, initial)
    for k in range(n_steps):
        bus.begin_step(k)
        for ref, value in publications.get(k, {}).items():
            bus.publish(ref, value)
    return bus


def test_numeric_forward_fill_without_initial():
    bus = run_bus({"a.x": None}, {1: {"a.x": 2.0}, 3: {"a.x": 4}})
    col = bus.to_frame()["a.x"].to_numpy()
    assert np.isnan(col[0])
    np.testing.assert_array_equal(col[1:], [2.0, 2.0, 4.0, 4.0, 4.0])


def test_numeric_forward_fill_with_initial_value():
    bus = run_bus({"a.x": 0.5}, {2: {"a.x": 1.0}})
    np.testing.assert_array_equal(bus.to_frame()["a.x"].to_numpy(), [0.5, 0.5, 1.0, 1.0, 1.0, 1.0])


def test_numeric_none_publication_is_nan_until_next_value():
    bus = run_bus({"a.x": 0.0}, {0: {"a.x": 1.0}, 2: {"a.x": None}, 4: {"a.x": 3.0}})
    col = bus.to_frame()["a.x"].to_numpy()
    assert col[0] == col[1] == 1.0
    assert np.isnan(col[2]) and np.isnan(col[3])
    assert col[4] == col[5] == 3.0


def test_booleans_and_numpy_scalars_are_numeric():
    bus = run_bus({"a.b": None, "a.n": None}, {0: {"a.b": True, "a.n": np.int64(3)}, 2: {"a.b": np.bool_(False)}})
    df = bus.to_frame()
    assert df["a.b"].dtype.kind == "f" and df["a.n"].dtype.kind == "f"
    assert list(df["a.b"]) == [1.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    assert list(df["a.n"]) == [3.0] * 6


def test_string_forward_fill_with_initial():
    bus = run_bus({"a.mode": "off"}, {2: {"a.mode": "on"}, 4: {"a.mode": "boost"}})
    assert list(bus.to_frame()["a.mode"]) == ["off", "off", "on", "on", "boost", "boost"]


def test_string_forward_fill_without_initial():
    bus = run_bus({"a.mode": None}, {1: {"a.mode": "on"}})
    col = bus.to_frame()["a.mode"]
    assert pd.isna(col.iloc[0])
    assert list(col.iloc[1:]) == ["on"] * 5


def test_object_values_are_forward_filled_unchanged():
    d1, d2 = {"k": 1}, [1, 2, 3]
    bus = run_bus({"a.o": None}, {0: {"a.o": d1}, 3: {"a.o": d2}})
    col = list(bus.to_frame()["a.o"])
    assert col[:3] == [d1, d1, d1] and col[3:] == [d2, d2, d2]
    assert col[0] is d1


def test_mixed_numbers_and_strings_use_object_path():
    bus = run_bus({"a.v": 0}, {1: {"a.v": 1.5}, 3: {"a.v": "fault"}})
    assert list(bus.to_frame()["a.v"]) == [0, 1.5, 1.5, "fault", "fault", "fault"]


def test_complex_numbers_are_not_coerced_to_float():
    bus = run_bus({"a.z": None}, {0: {"a.z": 1 + 2j}})
    assert bus.to_frame()["a.z"].iloc[-1] == 1 + 2j


def test_to_frame_upto_subset_and_index():
    bus = run_bus({"a.x": 0.0, "b.y": 0.0}, {k: {"a.x": float(k), "b.y": -float(k)} for k in range(6)})
    df = bus.to_frame(refs=["b.y"], upto=3)
    assert list(df.columns) == ["b.y"]
    assert list(df.index) == TIMES[:3]
    assert df.index.name == "time"
    assert list(df["b.y"]) == [0.0, -1.0, -2.0]
    # publications after `upto` are ignored, numeric and object path alike
    bus2 = run_bus({"a.s": "x"}, {0: {"a.s": "p"}, 4: {"a.s": "q"}})
    assert list(bus2.to_frame(upto=3)["a.s"]) == ["p", "p", "p"]


def test_to_frame_only_covers_completed_steps():
    bus = run_bus({"a.x": 1.0}, {}, n_steps=2)
    assert len(bus.to_frame()) == 2
    empty = DataBus(TIMES)
    empty.declare("a.x", 1.0)
    df = empty.to_frame()
    assert len(df) == 0 and list(df.columns) == ["a.x"]


def test_publish_twice_in_one_step_overwrites():
    bus = run_bus({"a.x": None}, {})
    bus.begin_step(0)
    bus.publish("a.x", 1.0)
    bus.publish("a.x", 2.0)
    assert bus.latest("a.x") == 2.0
    h = bus.history("a.x")
    assert list(h) == [2.0] and list(h.index) == [TIMES[0]]


def test_history_latest_and_has_value():
    bus = run_bus({"a.x": None, "b.y": 7.0}, {0: {"a.x": 1.0}, 2: {"a.x": 2.0}, 5: {"a.x": 3.0}})
    h = bus.history("a.x")
    assert list(h) == [1.0, 2.0, 3.0]
    assert list(h.index) == [TIMES[0], TIMES[2], TIMES[5]]
    assert h.name == "a.x" and h.index.name == "time"
    assert list(bus.history("a.x", n=2)) == [2.0, 3.0]
    assert bus.latest("a.x") == 3.0
    assert bus.latest("b.y") == 7.0  # initial value counts as latest
    assert len(bus.history("b.y")) == 0  # ... but is not a publication
    assert bus.latest("nope", default=-1) == -1
    assert bus.has_value("b.y")
    with pytest.raises(KeyError):
        bus.history("nope")


def test_history_with_n_zero_is_empty():
    bus = run_bus({"a.x": None}, {0: {"a.x": 1.0}, 1: {"a.x": 2.0}})
    assert len(bus.history("a.x", n=0)) == 0


def test_unknown_ref_cannot_be_published():
    bus = DataBus(TIMES)
    bus.begin_step(0)
    with pytest.raises(KeyError, match="Unknown port 'x.y'"):
        bus.publish("x.y", 1)


def test_declare_without_initial_has_no_value():
    bus = DataBus(TIMES)
    bus.declare("a.x")
    assert not bus.has_value("a.x")
    assert bus.refs == ["a.x"]


def test_previous_snapshot_all_refs():
    bus = run_bus({"a.x": 0.0}, {}, n_steps=0)
    for k, v in enumerate([1.0, 2.0, 3.0]):
        bus.begin_step(k)
        assert bus.previous("a.x") == (0.0 if k == 0 else v - 1.0)
        bus.publish("a.x", v)
        assert bus.previous("a.x") == (0.0 if k == 0 else v - 1.0)  # snapshot is frozen during the step


def test_previous_snapshot_tracks_only_selected_refs():
    bus = DataBus(TIMES)
    bus.declare("a.x", 0.0)
    bus.declare("b.y", 0.0)
    bus.track_previous(["a.x"])
    bus.begin_step(0)
    bus.publish("a.x", 1.0)
    bus.publish("b.y", 1.0)
    bus.begin_step(1)
    assert bus.previous("a.x") == 1.0
    assert bus.previous("b.y", default="untracked") == "untracked"
    empty = DataBus(TIMES)
    empty.declare("a.x", 5.0)
    empty.track_previous([])
    empty.begin_step(0)
    assert empty.previous("a.x") is None


def test_event_log():
    bus = DataBus(TIMES)
    bus.log_event(TIMES[1], "comp", "warning", "too hot", value=31.0)
    assert bus.events == [{"time": TIMES[1], "source": "comp", "kind": "warning", "message": "too hot", "value": 31.0}]


# --------------------------------------------------------------------------
# Values published from the (undocumented) ``initial_outputs()`` hook are stored
# with step index -1. The object/str path of ``to_frame`` then never advances
# past that entry, and ``history`` maps index -1 onto the *last* time stamp.
class _WithInitialOutputs(pc.Component):
    outputs = {"s": "declared", "x": 0.0}
    step_size = "1h"

    def initial_outputs(self):
        return {"s": "boot", "x": 5.0}

    def step(self, t, inputs):
        return {"s": f"h{t.hour}", "x": float(t.hour)}


def test_initial_outputs_are_visible_to_consumers(make_sim):
    seen = []

    @pc.component(outputs="y")
    def reader(t, s=None):
        seen.append(s)
        return 0.0

    sim = make_sim(end="2023-07-03 01:00", step="30min")
    sim.add(_WithInitialOutputs("i"), reader)
    sim.connect("i.s", "reader.s", delay=True)
    sim.run()
    assert seen == ["boot", "h0"]


def test_initial_outputs_string_series_in_results(make_sim):
    sim = make_sim(start="2023-07-03", end="2023-07-03 03:00", step="30min")
    sim.add(_WithInitialOutputs("i"))
    res = sim.run()
    assert list(res["i.s"]) == ["h0", "h0", "h1", "h1", "h2", "h2"]


def test_initial_outputs_history_is_chronological(make_sim):
    sim = make_sim(start="2023-07-03", end="2023-07-03 03:00", step="30min")
    sim.add(_WithInitialOutputs("i"))
    sim.run()
    hist = sim.bus.history("i.x")
    assert hist.index.is_monotonic_increasing
    assert hist.index[0] <= sim.start
