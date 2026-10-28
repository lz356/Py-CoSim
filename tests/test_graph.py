"""Tests of the dependency analysis (pycosim.core.graph)."""
from __future__ import annotations

import pytest

from pycosim.core.component import Component, PortRef
from pycosim.core.errors import AlgebraicLoopError, ConfigurationError
from pycosim.core.graph import Connection, build_execution_order, resolve_reduce


class Node(Component):
    inputs = {"x": 0.0, "x2": 0.0}
    outputs = {"y": 0.0}

    def __init__(self, name, priority=None, feedthrough=True):
        super().__init__(name, priority=priority)
        self.direct_feedthrough = feedthrough

    def step(self, t, inputs):
        return inputs.x + inputs.x2

    def output(self, t):
        return {"y": 0.0}


def conn(src, dst, delay=False):
    sources = [PortRef.parse(s) for s in (src if isinstance(src, list) else [src])]
    return Connection(sources, PortRef.parse(dst), delay=delay)


def names(order):
    return [c.name for c in order]


def test_chain_is_sorted_against_insertion_order():
    a, b, c = Node("a"), Node("b"), Node("c")
    order = build_execution_order([a, b, c], [conn("c.y", "b.x"), conn("b.y", "a.x")])
    assert names(order) == ["c", "b", "a"]


def test_independent_components_keep_insertion_order():
    comps = [Node(n) for n in "dcab"]
    assert names(build_execution_order(comps, [])) == ["d", "c", "a", "b"]


def test_priority_breaks_ties():
    comps = [Node("a", priority=2), Node("b", priority=-1), Node("c"), Node("d", priority=-1)]
    assert names(build_execution_order(comps, [])) == ["b", "d", "c", "a"]


def test_priority_never_overrides_dependencies():
    a, b = Node("a", priority=-10), Node("b", priority=10)
    assert names(build_execution_order([a, b], [conn("b.y", "a.x")])) == ["b", "a"]


def test_diamond_and_priority_among_ready_nodes():
    comps = [Node("a"), Node("b"), Node("c"), Node("d")]
    edges = [conn("a.y", "b.x"), conn("a.y", "c.x"), conn("b.y", "d.x"), conn("c.y", "d.x2")]
    assert names(build_execution_order(comps, edges)) == ["a", "b", "c", "d"]
    comps[2].priority = -1
    assert names(build_execution_order(comps, edges)) == ["a", "c", "b", "d"]


def test_fan_in_connection_orders_all_sources_first():
    comps = [Node("sink"), Node("s1"), Node("s2")]
    order = build_execution_order(comps, [conn(["s1.y", "s2.y"], "sink.x")])
    assert names(order) == ["s1", "s2", "sink"]


def test_duplicate_edges_counted_once():
    a, b = Node("a"), Node("b")
    order = build_execution_order([b, a], [conn("a.y", "b.x"), conn("a.y", "b.x2")])
    assert names(order) == ["a", "b"]


def test_delayed_connection_breaks_loop():
    a, b = Node("a"), Node("b")
    order = build_execution_order([a, b], [conn("a.y", "b.x"), conn("b.y", "a.x", delay=True)])
    assert names(order) == ["a", "b"]
    # the delayed edge imposes no ordering at all
    order = build_execution_order([b, a], [conn("a.y", "b.x", delay=True)])
    assert names(order) == ["b", "a"]


def test_three_cycle_reported_with_path():
    comps = [Node("a"), Node("b"), Node("c")]
    edges = [conn("a.y", "b.x"), conn("b.y", "c.x"), conn("c.y", "a.x")]
    with pytest.raises(AlgebraicLoopError) as info:
        build_execution_order(comps, edges)
    err = info.value
    assert sorted(err.cycle) == ["a", "b", "c"]
    path = " -> ".join(err.cycle + err.cycle[:1])
    assert path in str(err)
    assert "a -> b -> c -> a" in str(err)
    assert "delay=True" in str(err) and "direct_feedthrough = False" in str(err)
    assert isinstance(err, ConfigurationError)


def test_cycle_excludes_upstream_and_downstream_nodes():
    comps = [Node("src"), Node("a"), Node("b"), Node("sink")]
    edges = [conn("src.y", "a.x"), conn("a.y", "b.x"), conn("b.y", "a.x2"), conn("b.y", "sink.x")]
    with pytest.raises(AlgebraicLoopError) as info:
        build_execution_order(comps, edges)
    assert sorted(info.value.cycle) == ["a", "b"]
    assert "src" not in str(info.value).split("\n")[0]


def test_self_loop():
    a = Node("a")
    with pytest.raises(AlgebraicLoopError) as info:
        build_execution_order([a], [conn("a.y", "a.x")])
    assert info.value.cycle == ["a"]
    assert "a -> a" in str(info.value)
    assert names(build_execution_order([a], [conn("a.y", "a.x", delay=True)])) == ["a"]


def test_non_feedthrough_component_breaks_loop():
    plant = Node("plant", feedthrough=False)
    ctrl = Node("ctrl")
    edges = [conn("plant.y", "ctrl.x"), conn("ctrl.y", "plant.x")]
    assert names(build_execution_order([plant, ctrl], edges)) == ["ctrl"]


def test_non_feedthrough_self_loop_is_fine():
    plant = Node("plant", feedthrough=False)
    assert build_execution_order([plant], [conn("plant.y", "plant.x")]) == []


def test_loop_through_two_feedthrough_and_one_state_component():
    plant = Node("plant", feedthrough=False)
    a, b = Node("a"), Node("b")
    edges = [conn("plant.y", "a.x"), conn("a.y", "b.x"), conn("b.y", "plant.x")]
    assert names(build_execution_order([plant, b, a], edges)) == ["a", "b"]


def test_connection_str():
    assert str(conn("a.y", "b.x")) == "a.y -> b.x"
    assert str(conn(["a.y", "c.y"], "b.x", delay=True)) == "[a.y, c.y] -> b.x (delayed)"


@pytest.mark.parametrize(
    "name, values, expected",
    [
        ("sum", [1, 2, None, 3], 6),
        ("sum", [None, None], 0),
        ("mean", [1, 2, 3, None], 2),
        ("mean", [None, None], None),
        ("max", [1, 5, None, 3], 5),
        ("max", [None], None),
        ("min", [4, None, 2], 2),
        ("min", [], None),
        ("list", [1, None, "a"], [1, None, "a"]),
    ],
)
def test_builtin_reducers(name, values, expected):
    assert resolve_reduce(name)(values) == expected


def test_resolve_reduce_callable_none_and_unknown():
    f = lambda xs: 42  # noqa: E731
    assert resolve_reduce(f) is f
    assert resolve_reduce(None) is None
    with pytest.raises(ConfigurationError, match="Unknown reduce 'median'"):
        resolve_reduce("median")


def test_simulation_reports_algebraic_loop(make_sim):
    sim = make_sim()
    sim.add(Node("a"), Node("b"))
    sim.connect("a.y", "b.x")
    sim.connect("b.y", "a.x")
    assert "execution order: INVALID" in sim.describe()
    with pytest.raises(AlgebraicLoopError):
        sim.initialize()
