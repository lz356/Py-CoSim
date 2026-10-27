"""Tests of the Component abstraction (pycosim.core.component)."""
from __future__ import annotations

from collections import namedtuple
from datetime import datetime

import pytest

import pycosim as pc
from pycosim.core.component import (
    Component,
    FunctionComponent,
    Inputs,
    Port,
    PortRef,
    coerce_outputs,
    component,
    iter_refs,
    normalize_step_size,
)
from pycosim.core.errors import ComponentError, ConfigurationError, PyCoSimError

T0 = datetime(2023, 7, 3)


# =============================================================== port declaration
class DictPorts(Component):
    inputs = {"a": 1.5, "b": {"default": 2.0, "unit": "W", "description": "power"}, "c": None}
    outputs = {"y": 0.0, "z": Port("ignored_name", default=3.0, unit="C")}

    def step(self, t, inputs):
        return {"y": inputs.a + inputs.b}


class ListPorts(Component):
    inputs = ["u", Port("v", default=7, unit="kW")]
    outputs = "y"

    def step(self, t, inputs):
        return inputs.v


class MyFancyController(Component):
    outputs = ["y"]

    def step(self, t, inputs):
        return 1.0


def test_class_attribute_ports_from_mapping():
    c = DictPorts("dp")
    assert list(c.input_ports) == ["a", "b", "c"]
    assert c.input_ports["a"].default == 1.5
    assert c.input_ports["b"].default == 2.0
    assert c.input_ports["b"].unit == "W"
    assert c.input_ports["b"].description == "power"
    assert c.input_ports["c"].default is None
    # a Port given as a mapping value is renamed to its key, and copied
    assert c.output_ports["z"].name == "z"
    assert c.output_ports["z"].default == 3.0 and c.output_ports["z"].unit == "C"
    assert DictPorts.outputs["z"].name == "ignored_name"


def test_class_attribute_ports_from_list_and_string():
    c = ListPorts("lp")
    assert list(c.input_ports) == ["u", "v"]
    assert c.input_ports["u"].default is None
    assert c.input_ports["v"].default == 7 and c.input_ports["v"].unit == "kW"
    assert list(c.output_ports) == ["y"]


def test_instances_do_not_share_port_objects():
    a, b = ListPorts("a"), ListPorts("b")
    a.input_ports["v"].default = 99
    assert b.input_ports["v"].default == 7


def test_duplicate_port_in_list_rejected():
    class Dup(Component):
        outputs = ["y", "y"]

    with pytest.raises(ConfigurationError, match="Duplicate"):
        Dup("d")


def test_add_input_and_add_output():
    class Dyn(Component):
        def __init__(self, name, n):
            super().__init__(name)
            for i in range(n):
                self.add_input(f"in{i}", default=float(i), unit="W", description=f"input {i}")
            self.add_output("total", initial=0.0, unit="W")

        def step(self, t, inputs):
            return sum(inputs.values())

    d = Dyn("dyn", 3)
    assert list(d.input_ports) == ["in0", "in1", "in2"]
    assert d.input_ports["in2"].default == 2.0
    assert d.output_ports["total"].unit == "W"
    with pytest.raises(ConfigurationError, match="already exists"):
        d.add_input("in0")
    with pytest.raises(ConfigurationError, match="already exists"):
        d.add_output("total")


def test_default_component_name_is_snake_case_class_name():
    assert MyFancyController().name == "my_fancy_controller"

    class HTTPServerX(Component):
        pass

    assert HTTPServerX().name == "http_server_x"


@pytest.mark.parametrize("bad", ["a.b", "1abc", "has space", "", "x/y"])
def test_invalid_component_names(bad):
    with pytest.raises(ConfigurationError, match="Invalid component name"):
        MyFancyController(bad)


@pytest.mark.parametrize("bad", ["a.b", "9x", "with space"])
def test_invalid_port_names(bad):
    with pytest.raises(ConfigurationError, match="Invalid port name"):
        Port(bad)
    c = MyFancyController("ok")
    with pytest.raises(ConfigurationError):
        c.add_input(bad)


def test_valid_names_with_dash_and_underscore():
    assert MyFancyController("_ctrl-1").name == "_ctrl-1"
    assert Port("T_zone-2").name == "T_zone-2"


def test_getitem_returns_portref():
    c = DictPorts("dp")
    assert c["a"] == PortRef("dp", "a")
    assert c["y"] == PortRef("dp", "y")
    with pytest.raises(KeyError, match="no port"):
        c["nope"]


def test_component_without_super_init_still_works(make_sim):
    class Lazy(Component):
        outputs = {"y": 0.0}

        def __init__(self, gain):  # forgets super().__init__()
            self.gain = gain

        def step(self, t, inputs):
            return self.gain * 2

    lazy = Lazy(3)
    sim = make_sim(end="2023-07-03 01:00")
    sim.add(lazy)
    res = sim.run()
    assert lazy.name == "lazy"
    assert (res["lazy.y"] == 6).all()


def test_describe_lists_ports_and_units():
    text = DictPorts("dp").describe()
    assert text.startswith("dp [DictPorts, feedthrough, step=master]")
    assert "inputs: a, b [W], c" in text
    assert "outputs: y, z [C]" in text


def test_base_step_not_implemented(make_sim):
    class NoStep(Component):
        outputs = ["y"]

    with pytest.raises(NotImplementedError):
        NoStep("n").step(T0, Inputs())
    sim = make_sim(end="2023-07-03 01:00")
    sim.add(NoStep("n"))
    with pytest.raises(ComponentError) as info:
        sim.run()
    assert isinstance(info.value.original, NotImplementedError)


def test_rng_requires_setup():
    with pytest.raises(RuntimeError, match="rng"):
        MyFancyController("x").rng


def test_repr():
    assert repr(MyFancyController("abc")) == "<MyFancyController 'abc'>"


# ===================================================================== Inputs
def test_inputs_attribute_access():
    inp = Inputs(T_zone=24.0, price=0.1)
    assert inp.T_zone == inp["T_zone"] == 24.0
    with pytest.raises(AttributeError, match="Available inputs: T_zone, price"):
        inp.missing
    with pytest.raises(AttributeError, match=r"\(none\)"):
        Inputs().anything


# ================================================================== decorator
def test_decorator_without_parentheses_infers_inputs():
    @component
    def ctrl(t, T_zone, price=0.1, *, mode="auto"):
        return 1.0

    assert isinstance(ctrl, FunctionComponent)
    assert ctrl.name == "ctrl"
    assert list(ctrl.input_ports) == ["T_zone", "price", "mode"]
    assert ctrl.input_ports["T_zone"].default is None
    assert ctrl.input_ports["price"].default == 0.1
    assert ctrl.input_ports["mode"].default == "auto"
    assert list(ctrl.output_ports) == ["out"]
    assert ctrl.direct_feedthrough is True


def test_decorator_with_options():
    @component(name="thermo", outputs=["clg_sp", "htg_sp"], step_size="1h", priority=3)
    def f(t, occupied=1):
        return (24.0, 21.0) if occupied else (29.0, 15.0)

    assert f.name == "thermo"
    assert list(f.output_ports) == ["clg_sp", "htg_sp"]
    assert f.step_size == "1h"
    assert f.priority == 3
    assert f.step(T0, Inputs(occupied=0)) == (29.0, 15.0)


def test_decorator_time_parameter_receives_time():
    seen = []

    @component(outputs="y")
    def f(now, x=2.0):
        seen.append(now)
        return x * 2

    assert "now" not in f.input_ports
    assert f.step(T0, Inputs(x=3.0)) == 6.0
    assert seen == [T0]


def test_decorator_ctx_parameter_is_not_an_input(make_sim):
    got = {}

    @component(outputs="y")
    def f(t, ctx, x=1.0):
        got["ctx"] = ctx
        return x + ctx.step_index

    assert list(f.input_ports) == ["x"]
    sim = make_sim(end="2023-07-03 01:00")
    sim.add(f)
    res = sim.run()
    assert got["ctx"] is f.ctx
    assert list(res["f.y"]) == [1.0, 2.0, 3.0, 4.0]


def test_function_with_ctx_only_has_no_time_param():
    @component(outputs="y")
    def f(ctx, x=1.0):
        return x

    f.ctx = object()
    assert f.step(T0, Inputs(x=5.0)) == 5.0


def test_declared_inputs_must_be_accepted():
    def f(t, a=1):
        return a

    with pytest.raises(ConfigurationError, match="does not accept declared inputs"):
        FunctionComponent(f, inputs={"a": 1, "b": 2})


def test_declared_inputs_with_var_kwargs():
    def f(t, **inputs):
        return sum(inputs.values())

    fc = FunctionComponent(f, inputs={"a": 1.0, "b": {"default": 2.0, "unit": "W"}}, outputs="s")
    assert fc.input_ports["b"].unit == "W"
    assert fc.step(T0, Inputs(a=1.0, b=2.0)) == 3.0


def test_function_component_rejects_non_feedthrough_and_non_callable():
    with pytest.raises(ConfigurationError, match="stateless"):
        FunctionComponent(lambda t: 1, name="f", direct_feedthrough=False)
    with pytest.raises(TypeError):
        FunctionComponent(42, name="f")


def test_function_component_copy_is_independent(make_sim):
    @component(outputs="y")
    def gain(t, x=1.0, k=2.0):
        return x * k

    other = gain.copy("gain2", priority=-1)
    assert other.name == "gain2" and other.priority == -1 and gain.priority == 0
    assert other.ctx is None
    assert other.func is gain.func
    other.input_ports["k"].default = 10.0
    assert gain.input_ports["k"].default == 2.0
    assert other.input_ports is not gain.input_ports
    assert other.output_ports["y"] is not gain.output_ports["y"]
    with pytest.raises(ConfigurationError):
        gain.copy("bad.name")

    sim = make_sim(end="2023-07-03 00:30")
    sim.add(gain, other)
    res = sim.run()
    assert list(res["gain.y"]) == [2.0, 2.0]
    assert list(res["gain2.y"]) == [10.0, 10.0]


# ============================================================= coerce_outputs
def _comp(outputs):
    class C(Component):
        pass

    C.outputs = outputs
    return C("c")


def test_coerce_none_keeps_previous():
    assert coerce_outputs(_comp(["a"]), None) is None


def test_coerce_dict_full_and_partial():
    c = _comp(["a", "b"])
    assert coerce_outputs(c, {"a": 1, "b": 2}) == {"a": 1, "b": 2}
    assert coerce_outputs(c, {"b": 5}) == {"b": 5}


def test_coerce_dict_unknown_key():
    with pytest.raises(ConfigurationError, match=r"unknown outputs \['zz'\]"):
        coerce_outputs(_comp(["a"]), {"zz": 1})


def test_coerce_scalar_single_output():
    assert coerce_outputs(_comp(["a"]), 3.5) == {"a": 3.5}
    assert coerce_outputs(_comp(["a"]), "text") == {"a": "text"}
    # a tuple with a single declared output is one (tuple) value
    assert coerce_outputs(_comp(["a"]), (1, 2)) == {"a": (1, 2)}


def test_coerce_tuple_in_declaration_order():
    c = _comp(["a", "b", "c"])
    assert coerce_outputs(c, (1, 2, 3)) == {"a": 1, "b": 2, "c": 3}
    assert coerce_outputs(c, [1, 2, 3]) == {"a": 1, "b": 2, "c": 3}  # lists work like tuples
    NT = namedtuple("NT", "x y z")
    assert coerce_outputs(c, NT(4, 5, 6)) == {"a": 4, "b": 5, "c": 6}


def test_coerce_tuple_wrong_length():
    with pytest.raises(ConfigurationError, match="of length 2 but declares 3 outputs"):
        coerce_outputs(_comp(["a", "b", "c"]), (1, 2))
    with pytest.raises(ConfigurationError, match="of length 4 but declares 3 outputs"):
        coerce_outputs(_comp(["a", "b", "c"]), [1, 2, 3, 4])


def test_coerce_scalar_with_several_outputs():
    with pytest.raises(ConfigurationError, match="single value but declares 2 outputs"):
        coerce_outputs(_comp(["a", "b"]), 1.0)
    with pytest.raises(ConfigurationError, match="single value"):
        coerce_outputs(_comp(["a", "b"]), "ab")  # strings are values, not sequences of outputs
    # a list with a single declared output is one (list) value
    assert coerce_outputs(_comp(["a"]), [1.0, 2.0]) == {"a": [1.0, 2.0]}


def _bad_return_sim(make_sim):
    @component(outputs=["a", "b"])
    def f(t):
        return 1.0

    sim = make_sim(end="2023-07-03 01:00", log_level="CRITICAL")
    sim.add(f)
    return sim


def test_bad_return_value_in_simulation_fails_the_run(make_sim):
    sim = _bad_return_sim(make_sim)
    with pytest.raises(PyCoSimError, match="Component 'f' returned a single value"):
        sim.run()
    assert sim.status == "failed"


def test_bad_return_value_in_simulation_is_component_error(make_sim):
    sim = _bad_return_sim(make_sim)
    with pytest.raises(ComponentError) as info:
        sim.run()
    assert info.value.component == "f" and info.value.phase == "step"
    assert isinstance(info.value.original, ConfigurationError)


# ===================================================================== PortRef
def test_portref_parse_variants():
    c = MyFancyController("ctrl")
    assert PortRef.parse("a.b") == PortRef("a", "b")
    assert PortRef.parse("a.b.c") == PortRef("a", "b.c")  # split at the first dot only
    assert PortRef.parse(("a", "x")) == PortRef("a", "x")
    assert PortRef.parse((c, "y")) == PortRef("ctrl", "y")
    ref = PortRef("p", "q")
    assert PortRef.parse(ref) is ref
    assert str(ref) == "p.q"


@pytest.mark.parametrize("bad", ["nodot", 42, None, ("a", "b", "c")])
def test_portref_parse_errors(bad):
    with pytest.raises(ConfigurationError):
        PortRef.parse(bad)


def test_portref_is_hashable_and_frozen():
    ref = PortRef("a", "b")
    assert {ref: 1}[PortRef("a", "b")] == 1
    with pytest.raises(Exception):
        ref.port = "c"


def test_iter_refs():
    c = MyFancyController("ctrl")
    assert iter_refs("a.x") == [PortRef("a", "x")]
    assert iter_refs(PortRef("a", "x")) == [PortRef("a", "x")]
    assert iter_refs((c, "y")) == [PortRef("ctrl", "y")]
    assert iter_refs(("comp", "port")) == [PortRef("comp", "port")]
    assert iter_refs(("a.x", "b.y")) == [PortRef("a", "x"), PortRef("b", "y")]
    assert iter_refs(["a.x", c["y"], (c, "y")]) == [PortRef("a", "x"), PortRef("ctrl", "y"), PortRef("ctrl", "y")]


# ======================================================= step size validation
@pytest.mark.parametrize(
    "size, master, expected",
    [(None, 900, 1), ("15min", 900, 1), ("1h", 900, 4), (3600, 900, 4), ("1d", 3600, 24), (1.5, 0.5, 3)],
)
def test_normalize_step_size(size, master, expected):
    c = MyFancyController("c", step_size=size)
    assert normalize_step_size(c, master) == expected


@pytest.mark.parametrize("size", ["10min", "5min", 1000])
def test_normalize_step_size_errors(size):
    c = MyFancyController("c", step_size=size)
    with pytest.raises(ConfigurationError, match="not a positive integer multiple"):
        normalize_step_size(c, 900)


def test_instance_step_size_and_priority_override_class_defaults():
    class Slow(Component):
        step_size = "1h"
        priority = 5
        outputs = ["y"]

    assert Slow("a").step_size == "1h" and Slow("a").priority == 5
    s = Slow("b", step_size="2h", priority=-1)
    assert s.step_size == "2h" and s.priority == -1
    assert Slow.step_size == "1h"


def test_error_hierarchy():
    from pycosim.core import errors

    assert issubclass(errors.AlgebraicLoopError, ConfigurationError)
    assert issubclass(errors.HandleNotFoundError, ConfigurationError)
    assert issubclass(ConfigurationError, PyCoSimError)
    assert issubclass(ComponentError, PyCoSimError)
    assert issubclass(errors.SynchronizationError, PyCoSimError)
    err = ComponentError("comp", "step", T0, ValueError("boom"))
    assert str(err) == "Component 'comp' failed during 'step' at 2023-07-03 00:00:00: ValueError: boom"
    assert ComponentError("comp", "setup", None, KeyError("k")).time is None


def test_lazy_top_level_api():
    assert pc.Component is Component
    assert pc.component is component
    assert pc.FunctionComponent is FunctionComponent
    assert isinstance(pc.__version__, str) and pc.__version__
    assert "CoSimulation" in dir(pc)
    with pytest.raises(AttributeError):
        pc.does_not_exist


def test_top_level_error_and_observer_modules():
    from pycosim.core import errors as core_errors
    from pycosim.core import observers as core_observers

    errors = __import__("pycosim.errors", fromlist=["ComponentError"])
    observers = __import__("pycosim.observers", fromlist=["LivePlot"])
    for name in ("PyCoSimError", "ConfigurationError", "AlgebraicLoopError", "ComponentError",
                 "SynchronizationError", "HandleNotFoundError"):
        assert getattr(errors, name) is getattr(core_errors, name)
    for name in ("Observer", "ProgressBar", "LivePlot", "CSVStreamRecorder", "CallbackObserver"):
        assert getattr(observers, name) is getattr(core_observers, name)
