"""Tests of the FMU adapter (pycosim.components.FMUComponent).

A small co-simulation FMU -- a first-order thermal mass,
``C dT/dt = UA (T_out - T) + Q`` -- is built with pythonfmu in a temporary
folder and checked against the same model written as a Python component.
The FMU tests are skipped when fmpy or pythonfmu is not installed.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

import pycosim as pc
from pycosim.errors import ComponentError, ConfigurationError

try:
    import fmpy  # noqa: F401
    from pythonfmu.builder import FmuBuilder

    HAVE_FMU_TOOLS = FmuBuilder.has_binary()
except ImportError:  # pragma: no cover - optional dependencies
    HAVE_FMU_TOOLS = False

requires_fmu = pytest.mark.skipif(not HAVE_FMU_TOOLS,
                                  reason="needs fmpy and pythonfmu (with a binary for this platform)")

MODEL = '''
import math

from pythonfmu import Boolean, Fmi2Causality, Fmi2Slave, Fmi2Variability, Integer, Real, String


class ThermalMass(Fmi2Slave):
    """First-order thermal mass: C dT/dt = UA (T_out - T) + Q (exact solution for constant inputs)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.C, self.UA, self.T_start = 2.0e6, 150.0, 20.0
        self.T_out, self.Q, self.heater_on = 30.0, 0.0, True
        self.T, self.Q_loss, self.n_steps, self.elapsed, self.E_in = self.T_start, 0.0, 0, 0.0, 0.0
        self.label = "thermal mass"
        fixed = dict(causality=Fmi2Causality.parameter, variability=Fmi2Variability.fixed)
        discrete = dict(variability=Fmi2Variability.discrete)
        for name in ("C", "UA", "T_start"):
            self.register_variable(Real(name, **fixed))
        self.register_variable(Real("T_out", causality=Fmi2Causality.input))
        self.register_variable(Real("Q", causality=Fmi2Causality.input))
        self.register_variable(Boolean("heater_on", causality=Fmi2Causality.input, **discrete))
        self.register_variable(Real("T", causality=Fmi2Causality.output))
        self.register_variable(Real("Q_loss", causality=Fmi2Causality.output))
        self.register_variable(Integer("n_steps", causality=Fmi2Causality.output, **discrete))
        self.register_variable(Real("elapsed", causality=Fmi2Causality.output))
        self.register_variable(String("label", causality=Fmi2Causality.output, **discrete))
        self.register_variable(Real("room.E_in", causality=Fmi2Causality.local), nested=False)

    def exit_initialization_mode(self):
        self.T = self.T_start

    def do_step(self, current_time, step_size):
        q = self.Q if self.heater_on else 0.0
        T_inf = self.T_out + q / self.UA
        T_new = T_inf + (self.T - T_inf) * math.exp(-step_size * self.UA / self.C)
        self.Q_loss = q - self.C * (T_new - self.T) / step_size   # mean loss to outside over the step
        self.T = T_new
        self.E_in += q * step_size
        self.n_steps += 1
        self.elapsed = current_time + step_size
        return True
'''


class PyThermalMass(pc.Component):
    """The same model as a plain Python component (the reference)."""

    direct_feedthrough = False
    inputs = {"T_out": 30.0, "Q": 0.0, "heater_on": True}

    def __init__(self, name, C=2.0e6, UA=150.0, T_start=20.0, step_size=None):
        super().__init__(name, step_size=step_size)
        self.C, self.UA, self.T_start = C, UA, T_start
        self.add_output("T", T_start, kind="instant")
        self.add_output("Q_loss", 0.0, kind="average", interval="past")
        self.add_output("n_steps", 0, kind="hold")
        self.add_output("elapsed", 0.0, kind="instant")
        self.add_output("room_E_in", 0.0, kind="instant")

    def setup(self, ctx):
        self.T, self.Q_loss, self.n, self.elapsed, self.E = self.T_start, 0.0, 0, 0.0, 0.0

    def output(self, t):
        return {"T": self.T, "Q_loss": self.Q_loss, "n_steps": self.n, "elapsed": self.elapsed,
                "room_E_in": self.E}

    def update(self, t, inputs):
        h = self.ctx.step_size
        q = inputs.Q if inputs.heater_on else 0.0
        T_inf = inputs.T_out + q / self.UA
        T_new = T_inf + (self.T - T_inf) * math.exp(-h * self.UA / self.C)
        self.Q_loss = q - self.C * (T_new - self.T) / h
        self.T = T_new
        self.E += q * h
        self.n += 1
        self.elapsed = (t - self.ctx.start).total_seconds() + h


@pc.component(outputs="T_out")
def outdoor(t):
    return 30.0 + 8.0 * math.sin(2 * math.pi * (t.hour + t.minute / 60 - 9) / 24)


@pc.component(outputs=["Q", "heater_on"])
def thermostat(t, T=20.0):
    """Proportional cooling above 24 C (W, negative = cooling); the unit is off from 2 to 4 h."""
    return {"Q": -min(3000.0, max(0.0, 2000.0 * (T - 24.0))), "heater_on": not 2 <= t.hour < 4}


@pytest.fixture(scope="module")
def thermal_fmu(tmp_path_factory) -> Path:
    if not HAVE_FMU_TOOLS:
        pytest.skip("needs fmpy and pythonfmu")
    folder = tmp_path_factory.mktemp("fmu")
    script = folder / "thermal_mass.py"
    script.write_text(MODEL, encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "pythonfmu", "build", "-f", str(script), "-d", str(folder)],
                          capture_output=True, text=True)
    if proc.returncode != 0:  # pragma: no cover - diagnostics
        pytest.fail(f"pythonfmu build failed:\n{proc.stdout}\n{proc.stderr}")
    path = folder / "ThermalMass.fmu"
    assert path.exists()
    return path


def make_fmu(path, name="room", **kw):
    from pycosim.components import FMUComponent

    kw.setdefault("kinds", {"Q_loss": "average"})
    with pytest.warns(UserWarning, match="label"):  # the String output is skipped
        return FMUComponent(name, path, **kw)


def run_loop(make_sim, room, end="2023-07-05", step="15min"):
    sim = make_sim(start="2023-07-03", end=end, step=step)
    sim.add(room, outdoor.copy("outdoor"), thermostat.copy("thermostat"))
    sim.connect("outdoor.T_out", f"{room.name}.T_out")
    sim.connect(f"{room.name}.T", "thermostat.T")
    sim.connect("thermostat.Q", f"{room.name}.Q")
    sim.connect("thermostat.heater_on", f"{room.name}.heater_on")
    return sim.run()


# ===================================================================== no fmpy
def test_missing_fmpy_fails_only_when_used(monkeypatch):
    import pycosim.components as comps

    monkeypatch.setitem(sys.modules, "fmpy", None)  # "import fmpy" now raises ImportError
    assert "FMUComponent" in comps.__all__
    with pytest.raises(ImportError, match="pip install fmpy"):
        comps.FMUComponent("x", "model.fmu")


# ===================================================================== ports
@requires_fmu
def test_ports_from_model_description(thermal_fmu):
    room = make_fmu(thermal_fmu)
    assert not room.direct_feedthrough
    assert list(room.input_ports) == ["T_out", "Q", "heater_on"]
    assert list(room.output_ports) == ["T", "Q_loss", "n_steps", "elapsed"]
    # input defaults are the FMU's start values, with Python types
    defaults = {k: p.default for k, p in room.input_ports.items()}
    assert defaults == {"T_out": 30.0, "Q": 0.0, "heater_on": True}
    assert type(defaults["heater_on"]) is bool
    # output kinds: continuous Real -> instant, discrete -> hold, overridden -> average
    kinds = {k: (p.kind, p.interval) for k, p in room.output_ports.items()}
    assert kinds == {"T": ("instant", None), "Q_loss": ("average", "past"), "n_steps": ("hold", None),
                     "elapsed": ("instant", None)}
    assert "FMUComponent" in room.describe()
    assert room.fmi_version == "2.0"


@requires_fmu
def test_start_values_change_input_defaults(thermal_fmu):
    room = make_fmu(thermal_fmu, start_values={"T_out": 25.0, "heater_on": "false"})
    assert room.input_ports["T_out"].default == 25.0
    assert room.input_ports["heater_on"].default is False


# ============================================================ against Python
@requires_fmu
@pytest.mark.parametrize("step_size", [None, "30min"])
def test_fmu_matches_python_model(make_sim, thermal_fmu, step_size):
    params = {"C": 1.5e6, "UA": 120.0, "T_start": 22.0}
    fmu_res = run_loop(make_sim, make_fmu(thermal_fmu, parameters=params, step_size=step_size))
    py_res = run_loop(make_sim, PyThermalMass("room", step_size=step_size, **params))
    for port in ("T", "Q_loss", "n_steps", "elapsed"):
        a, b = fmu_res[f"room.{port}"].to_numpy(float), py_res[f"room.{port}"].to_numpy(float)
        np.testing.assert_allclose(a, b, rtol=1e-12, atol=1e-9, err_msg=port)
    np.testing.assert_allclose(fmu_res["thermostat.Q"].to_numpy(float), py_res["thermostat.Q"].to_numpy(float),
                               atol=1e-9)
    # the loop really closed: the thermostat cooled the room and was off between 2 and 4 h
    assert fmu_res["thermostat.Q"].min() < -100
    assert fmu_res["room.T"].iloc[0] == pytest.approx(22.0)
    if step_size is None:
        # the FMU clock starts at 0 at the co-simulation start
        index = fmu_res["room.elapsed"].index
        expected = (index - index[0]).total_seconds().to_numpy()
        np.testing.assert_allclose(fmu_res["room.elapsed"].to_numpy(float), expected, atol=1e-9)
        assert fmu_res["room.n_steps"].iloc[-1] == len(index) - 1


# ================================================================== renaming
@requires_fmu
def test_rename_and_restrict_ports(make_sim, thermal_fmu):
    from pycosim.components import FMUComponent

    room = FMUComponent("zone", thermal_fmu, inputs={"T_amb": "T_out", "Q_hvac": "Q"},
                        outputs={"T_zone": "T", "E": "room.E_in"}, kinds={"T_zone": "instant"})
    assert list(room.input_ports) == ["T_amb", "Q_hvac"]
    assert list(room.output_ports) == ["T_zone", "E"]

    @pc.component(outputs="Q")
    def cooler(t, T=20.0):
        return -min(3000.0, max(0.0, 2000.0 * (T - 24.0)))

    sim = make_sim(end="2023-07-04")
    sim.add(room, outdoor.copy("outdoor"), cooler)
    sim.connect("outdoor.T_out", "zone.T_amb")
    sim.connect("zone.T_zone", "cooler.T")
    sim.connect("cooler.Q", "zone.Q_hvac")
    res = sim.run()

    ref = PyThermalMass("zone")
    sim = make_sim(end="2023-07-04")
    sim.add(ref, outdoor.copy("outdoor"), cooler.copy("cooler"))
    sim.connect("outdoor.T_out", "zone.T_out")
    sim.connect("zone.T", "cooler.T")
    sim.connect("cooler.Q", "zone.Q")
    ref_res = sim.run()
    np.testing.assert_allclose(res["zone.T_zone"].to_numpy(float), ref_res["zone.T"].to_numpy(float), atol=1e-9)
    np.testing.assert_allclose(res["zone.E"].to_numpy(float), ref_res["zone.room_E_in"].to_numpy(float),
                               rtol=1e-12, atol=1e-6)


@requires_fmu
def test_list_and_string_port_selections(thermal_fmu):
    from pycosim.components import FMUComponent

    room = FMUComponent("room", thermal_fmu, inputs="Q", outputs=["T", "room.E_in"])
    assert list(room.input_ports) == ["Q"]
    assert list(room.output_ports) == ["T", "room_E_in"]  # dots are not allowed in port names
    none = FMUComponent("room2", thermal_fmu, inputs=[], outputs=["n_steps"])
    assert not none.input_ports and list(none.output_ports) == ["n_steps"]


# ==================================================================== errors
@requires_fmu
@pytest.mark.parametrize("kwargs, match", [
    ({"inputs": ["T_outdoor"]}, r"no input variable named 'T_outdoor'.*Did you mean 'T_out'.*Available input"),
    ({"outputs": {"x": "nope"}}, r"no output variable named 'nope'.*T, Q_loss, n_steps, elapsed"),
    ({"parameters": {"Cap": 1.0}}, r"no parameter variable named 'Cap'.*C, UA, T_start"),
    ({"parameters": {"T": 1.0}}, r"not 'parameter'"),
    ({"start_values": {"nope": 1.0}}, r"no settable variable named 'nope'"),
    ({"start_values": {"T": 1.0}}, r"start value of 'T'.*cannot be set"),
    ({"inputs": ["C"]}, r"'C' .*cannot be changed during the simulation"),
    ({"outputs": ["label"]}, r"'label' is of type String"),
    ({"kinds": {"nope": "average"}}, r"kinds given for unknown output ports \['nope'\]"),
    ({"kinds": {"T": "smooth"}}, r"kind must be one of"),
])
def test_bad_names_raise(thermal_fmu, kwargs, match):
    from pycosim.components import FMUComponent

    kwargs = {"outputs": ["T", "Q_loss", "n_steps", "elapsed"], **kwargs}
    with pytest.raises(ConfigurationError, match=match):
        FMUComponent("room", thermal_fmu, **kwargs)


@requires_fmu
def test_missing_file_and_model_exchange_only(tmp_path):
    from pycosim.components import FMUComponent

    with pytest.raises(ConfigurationError, match="not found"):
        FMUComponent("x", tmp_path / "missing.fmu")
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<fmiModelDescription fmiVersion="2.0" modelName="MEOnly" guid="{8c4e810f-3df3-4a00-8276-176fa3c9f000}">
  <ModelExchange modelIdentifier="MEOnly"/>
  <ModelVariables>
    <ScalarVariable name="x" valueReference="0" causality="output"><Real/></ScalarVariable>
  </ModelVariables>
  <ModelStructure><Outputs><Unknown index="1"/></Outputs></ModelStructure>
</fmiModelDescription>
"""
    path = tmp_path / "MEOnly.fmu"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modelDescription.xml", xml)
    with pytest.raises(ConfigurationError, match="model-exchange-only FMU"):
        FMUComponent("me", path)


# =================================================================== cleanup
@requires_fmu
def test_extracted_files_are_removed_after_run_and_failure(make_sim, thermal_fmu):
    room = make_fmu(thermal_fmu)
    seen = []

    @pc.component(outputs="y")
    def probe(t, T=0.0):
        seen.append(room._unzipdir)
        return T

    sim = make_sim(end="2023-07-03 02:00")
    sim.add(room, probe)
    sim.connect("room.T", "probe.T")
    sim.run()
    assert seen[0] and all(s == seen[0] for s in seen)
    assert not os.path.exists(seen[0])
    assert room._fmu is None and room._unzipdir is None
    assert not any(isinstance(p, str) and "pycosim_fmu_" in p for p in sys.path)

    # a failing component aborts the run; the FMU is freed and its files removed
    @pc.component(outputs="y")
    def broken(t, T=0.0):
        seen.append(room._unzipdir)
        if len(seen) > 12:
            raise RuntimeError("boom")
        return T

    sim = make_sim(end="2023-07-03 06:00")
    sim.add(room, broken)
    sim.connect("room.T", "broken.T")
    with pytest.raises(ComponentError, match="boom"):
        sim.run()
    assert not os.path.exists(seen[-1])
    assert room._fmu is None


@requires_fmu
def test_fmu_in_a_yaml_scenario(thermal_fmu, tmp_path, restore_sys_path):
    from pycosim.config import build_simulation

    spec = {
        "simulation": {"start": "2023-07-03", "end": "2023-07-03 06:00", "step": "15min", "save": False,
                       "log_level": "WARNING", "output_dir": str(tmp_path)},
        "components": {"room": {"type": "FMUComponent", "path": thermal_fmu.name,  # relative to base_dir
                                "outputs": ["T"], "parameters": {"C": 1.5e6}}},
    }
    res = build_simulation(spec, base_dir=thermal_fmu.parent).run()
    T = res["room.T"].to_numpy(float)
    assert T[0] == pytest.approx(20.0) and np.all(np.diff(T) > 0)  # warms towards T_out = 30 C


@requires_fmu
def test_fmu_errors_carry_the_fmu_log(make_sim, thermal_fmu):
    room = make_fmu(thermal_fmu, parameters={"UA": 0.0})  # do_step divides by zero
    sim = make_sim(end="2023-07-03 02:00")
    sim.add(room)
    with pytest.raises(ComponentError, match=r"doStep\(0s, 900s\).*ZeroDivisionError"):
        sim.run()
    assert room._fmu is None and room._unzipdir is None


# ===================================================================== FMI 3
def _fmi3_container(inner: Path, dest: Path, step: float) -> Path:
    """Wrap the FMI 2.0 FMU into an FMI 3.0 co-simulation FMU with fmpy's container FMU."""
    from fmpy import read_model_description
    from fmpy.container_fmu import Configuration
    from fmpy.container_fmu.cli import create_container_fmu
    from fmpy.container_fmu.config import Component
    from fmpy.model_description import CoSimulation, ModelDescription, ModelVariable, Unknown

    imd = read_model_description(inner)
    comp = Component(name=inner.stem, filename=inner, modelDescription=imd)
    inner_vars = {v.name: v for v in imd.modelVariables}
    md = ModelDescription(fmiVersion="3.0", modelName="ThermalMass3",
                          instantiationToken="{0c2b9f0e-5d6c-4c8e-9a51-3f3f6e1f0a01}")
    md.coSimulation = CoSimulation(modelIdentifier="container_fmu", fixedInternalStepSize=step,
                                   canHandleVariableCommunicationStepSize=True)
    # the container reserves value reference 0 for time
    md.modelVariables.append(ModelVariable(name="time", type="Float64", causality="independent",
                                           variability="continuous"))
    mapping = {}
    for name, causality, variability, start in (("C", "parameter", "fixed", "2000000"),
                                                ("T_out", "input", "continuous", "30"),
                                                ("Q", "input", "continuous", "0"),
                                                ("T", "output", "continuous", None),
                                                ("Q_loss", "output", "continuous", None),
                                                ("elapsed", "output", "continuous", None)):
        var = ModelVariable(name=name, type="Float64", causality=causality, variability=variability, start=start,
                            initial="exact" if causality == "parameter" else None)
        var._python_type = float
        md.modelVariables.append(var)
        mapping[var] = [(comp, inner_vars[name])]
    md.outputs = [Unknown(index=i + 1, variable=v) for i, v in enumerate(md.modelVariables) if v.causality == "output"]
    config = Configuration(parallelDoStep=False, modelDescription=md, components=[comp], connections=[],
                           variableMappings=mapping)
    work = dest / "container"
    work.mkdir(parents=True, exist_ok=True)
    out = dest / "ThermalMass3.fmu"
    create_container_fmu(config, work, out)
    return out


@requires_fmu
def test_fmi3_cosimulation_fmu(make_sim, thermal_fmu, tmp_path):
    try:
        path = _fmi3_container(thermal_fmu, tmp_path, step=900.0)
    except ImportError as exc:  # pragma: no cover - older fmpy without container FMUs
        pytest.skip(f"fmpy cannot build FMI 3 container FMUs: {exc}")
    from pycosim.components import FMUComponent

    room = FMUComponent("room", path, parameters={"C": 1.5e6}, kinds={"Q_loss": "average"})
    assert room.fmi_version.startswith("3.")
    assert list(room.input_ports) == ["T_out", "Q"]
    assert list(room.output_ports) == ["T", "Q_loss", "elapsed"]

    def loop(component):
        sim = make_sim(end="2023-07-04")
        sim.add(component, outdoor.copy("outdoor"), thermostat.copy("thermostat"))
        sim.connect("outdoor.T_out", "room.T_out")
        sim.connect("room.T", "thermostat.T")
        sim.connect("thermostat.Q", "room.Q")
        return sim.run()

    res, ref = loop(room), loop(PyThermalMass("room", C=1.5e6))
    for port in ("T", "Q_loss", "elapsed"):
        np.testing.assert_allclose(res[f"room.{port}"].to_numpy(float), ref[f"room.{port}"].to_numpy(float),
                                   rtol=1e-12, atol=1e-9, err_msg=port)
