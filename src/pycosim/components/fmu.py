"""Functional Mock-up Units (FMUs) as co-simulation components.

:class:`FMUComponent` wraps a *co-simulation* FMU (FMI 2.0 or FMI 3.0, e.g.
exported from Modelica/Dymola/OpenModelica, Simulink or built in Python with
``pythonfmu``) so it can be coupled with EnergyPlus and Python components like
any other component::

    from pycosim.components import FMUComponent

    tank = FMUComponent("tank", "StorageTank.fmu",
                        inputs={"Q": "heater.Q_flow"},       # port name -> FMU variable
                        outputs=["T_top", "T_bottom"],
                        parameters={"V": 0.3})
    sim.add(tank)
    sim.connect("controller.Q", "tank.Q")

FMUs are optional in Py-CoSim: the adapter needs the ``fmpy`` package
(``pip install fmpy``) only when an :class:`FMUComponent` is created.

Timing
------
The FMU is a state-based (non-feedthrough) component. At each of its steps
``[t, t + h)`` the master first calls :meth:`FMUComponent.output`, which reads
the FMU's outputs at the communication point ``t``, and then
:meth:`FMUComponent.update`, which sets the inputs and calls
``doStep(t, h)``. The FMU clock is the time elapsed since the start of the
co-simulation (0 at ``CoSimulation.start``). A direct input-to-output path
inside the FMU therefore appears one step later, which is the usual
co-simulation behaviour and lets FMUs close feedback loops without algebraic
loops.
"""
from __future__ import annotations

import difflib
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..core.component import Component
from ..core.errors import ConfigurationError
from ..core.timeutils import DurationLike

__all__ = ["FMUComponent"]

PortMap = Union[None, str, Sequence[str], Mapping[str, str]]

_INSTALL_HINT = "FMUComponent needs the 'fmpy' package: pip install fmpy  (or pip install \"py-cosim[fmu]\")"
_PORT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
_FLOAT_TYPES = {"Real", "Float64", "Float32"}
_INT_TYPES = {"Integer", "Enumeration", "Int8", "UInt8", "Int16", "UInt16", "Int32", "UInt32", "Int64", "UInt64"}
_SUPPORTED_TYPES = _FLOAT_TYPES | _INT_TYPES | {"Boolean"}
_STATUS = ["ok", "warning", "discard", "error", "fatal", "pending"]


def _require_fmpy():
    try:
        import fmpy  # noqa: F401
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc
    return fmpy


def _port_name(variable: str) -> str:
    """FMU variable name -> valid port name (``room.T`` -> ``room_T``, ``der(x)`` -> ``der_x``)."""
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", variable).strip("_")
    if not _PORT_RE.match(name):
        name = "_" + name
    return name


def _is_array(var) -> bool:
    return bool(getattr(var, "shape", None) or getattr(var, "dimensions", None))


def _parse_start(var, value: Any) -> Any:
    """Typed Python value of a start value given as a string (model description) or a number."""
    if value is None:
        return None
    if var.type == "Boolean":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1")
        return bool(value)
    try:
        if var.type in _INT_TYPES:
            return int(round(float(value)))
        return float(value)
    except (TypeError, ValueError):
        return None


class FMUComponent(Component):
    """A co-simulation FMU (FMI 2.0 or 3.0) as a state-based Py-CoSim component.

    Parameters
    ----------
    name : str
        Component name.
    path : str or Path
        The ``.fmu`` file.
    inputs, outputs : list, dict or str, optional
        Which FMU variables become ports. ``None`` (default): every variable
        with causality ``input`` / ``output`` of type Real/Float, Integer,
        Enumeration or Boolean (String, Binary, Clock and array variables are
        skipped with a warning). A list (or a single name) restricts the ports
        to these FMU variables; a dict ``{port_name: fmu_variable}`` restricts
        *and* renames them. Port names default to the variable names with
        characters other than letters, digits, ``_`` and ``-`` replaced by
        ``_`` (``room.T`` -> ``room_T``). ``outputs`` may also list local
        variables and parameters; ``inputs`` may list inputs and tunable
        parameters.
    parameters : dict, optional
        ``{fmu_variable: value}`` for variables with causality ``parameter``,
        set before initialization.
    step_size : duration, optional
        Communication step (a multiple of the master step); default: every
        master step.
    kinds : dict, optional
        ``{output_port: kind}`` overriding the time meaning of an output (see
        :mod:`pycosim.core.messages`). By default continuous Real outputs are
        ``"instant"`` (values at the communication point) and discrete,
        Integer, Enumeration and Boolean outputs are ``"hold"``. Use
        ``"average"`` for an output that is the mean over the step that just
        ended (e.g. ``kinds={"P": "average"}``).
    start_values : dict, optional
        ``{fmu_variable: value}`` for other variables with a settable start
        value (states with ``initial="exact"``, inputs, ...), set before
        initialization. A start value given for an input also becomes that
        input port's default.

    Notes
    -----
    Unconnected inputs keep the FMU's start value (the port default); an input
    receiving ``None`` is not set, so the FMU keeps its previous value. Integer
    inputs are rounded to the nearest integer. Log messages of the FMU go to
    the ``pycosim.<name>`` logger; the last ones are added to error messages.
    The FMU is extracted to a temporary folder in :meth:`setup` and the
    folder is removed in :meth:`finalize` / :meth:`abort`. List the variables
    of an FMU with ``fmpy.dump("model.fmu")``.
    """

    direct_feedthrough = False

    def __init__(self, name: str, path: Union[str, Path], *, inputs: PortMap = None, outputs: PortMap = None,
                 parameters: Optional[Mapping[str, Any]] = None, step_size: Optional[DurationLike] = None,
                 kinds: Optional[Mapping[str, str]] = None, start_values: Optional[Mapping[str, Any]] = None):
        super().__init__(name, step_size=step_size)
        fmpy = _require_fmpy()
        from fmpy import read_model_description

        self.path = Path(path)
        if not self.path.exists():
            raise ConfigurationError(f"{name}: FMU file not found: {self.path}")
        try:
            md = read_model_description(str(self.path))
        except Exception as exc:  # most often an FMU whose XML is slightly off the schema
            try:
                md = read_model_description(str(self.path), validate=False)
            except Exception:
                raise ConfigurationError(f"{name}: cannot read the model description of {self.path}: {exc}") from exc
            first = str(exc).strip().splitlines()
            warnings.warn(f"{name}: the modelDescription.xml of {self.path.name} does not validate "
                          f"({first[-1].lstrip('- ') if first else exc}); using it anyway", stacklevel=2)
        self.model_description = md
        version = str(md.fmiVersion or "")
        self.fmi_version = version
        if version.startswith("1."):
            raise ConfigurationError(
                f"{name}: {self.path.name} is an FMI 1.0 FMU; FMUComponent supports FMI 2.0 and 3.0 "
                "co-simulation FMUs. Re-export the model as FMI 2.0 or newer.")
        if not (version.startswith("2.") or version.startswith("3.")):
            raise ConfigurationError(f"{name}: unsupported FMI version {version!r} in {self.path.name}")
        if md.coSimulation is None:
            kind = "model-exchange" if md.modelExchange is not None else "non-co-simulation"
            raise ConfigurationError(
                f"{name}: {self.path.name} is a {kind}-only FMU, but FMUComponent needs a co-simulation FMU "
                "(it brings its own solver). Re-export the model with the co-simulation interface (e.g. "
                "'Co-Simulation' or 'both' in Dymola / OpenModelica / Simulink), or simulate it stand-alone "
                "with fmpy.simulate_fmu.")
        platforms = fmpy.supported_platforms(str(self.path))
        if fmpy.platform not in platforms:
            hint = (" It contains C sources: compile a binary with fmpy.util.compile_platform_binary(path)."
                    if "c-code" in platforms else "")
            raise ConfigurationError(
                f"{name}: {self.path.name} has no binary for this platform ({fmpy.platform}); it supports "
                f"{', '.join(platforms) or 'no platform'}.{hint}")

        self._vars = {v.name: v for v in md.modelVariables}
        self._fmi3 = version.startswith("3.")
        values = self._check_values(parameters, start_values)
        self._start_values: Dict[str, Any] = values
        self._in_map = self._select(inputs, "input")
        self._out_map = self._select(outputs, "output")
        kinds = dict(kinds or {})
        unknown = [k for k in kinds if k not in self._out_map]
        if unknown:
            raise ConfigurationError(
                f"{name}: kinds given for unknown output ports {unknown}; output ports are {list(self._out_map)}")

        for port, var_name in self._in_map.items():
            var = self._vars[var_name]
            default = _parse_start(var, values[var_name]) if var_name in values else _parse_start(var, var.start)
            self.add_input(port, default, self._unit(var), var.description or "")
        for port, var_name in self._out_map.items():
            var = self._vars[var_name]
            kind = kinds.get(port)
            if kind is None:
                kind = "instant" if var.type in _FLOAT_TYPES and var.variability in (None, "continuous") else "hold"
            self.add_output(port, _parse_start(var, var.start), self._unit(var), var.description or "", kind=kind,
                            interval="past" if kind in ("average", "amount") else None)

        self._in_groups = self._groups(self._in_map)
        self._out_groups = self._groups(self._out_map)
        self._fmu = None
        self._instantiated = False
        self._unzipdir: Optional[str] = None
        self._callbacks = None
        self._log_tail: List[str] = []
        self._logger = logging.getLogger(f"pycosim.{self.name}")

    # ------------------------------------------------------------- variables
    @staticmethod
    def _unit(var) -> Optional[str]:
        if var.unit:
            return var.unit
        declared = getattr(var, "declaredType", None)
        return getattr(declared, "unit", None) if declared is not None else None

    def _available(self, causalities: Sequence[str]) -> List[str]:
        return [v.name for v in self._vars.values()
                if v.causality in causalities and v.type in _SUPPORTED_TYPES and not _is_array(v)]

    def _unknown(self, var_name: str, what: str, causalities: Sequence[str]) -> ConfigurationError:
        avail = self._available(causalities)
        close = difflib.get_close_matches(var_name, list(self._vars), n=3)
        shown = ", ".join(avail[:40]) + (f", ... ({len(avail)} in total)" if len(avail) > 40 else "")
        return ConfigurationError(
            f"{self.name}: the FMU {self.path.name} has no {what} variable named {var_name!r}."
            + (f" Did you mean {', '.join(map(repr, close))}?" if close else "")
            + f" Available {what} variables: {shown or '(none)'}")

    def _check_values(self, parameters, start_values) -> Dict[str, Any]:
        from fmpy.simulation import settable_in_initialization_mode, settable_in_instantiated

        merged: Dict[str, Any] = {}
        for var_name, value in dict(parameters or {}).items():
            var = self._vars.get(var_name)
            if var is None:
                raise self._unknown(var_name, "parameter", ("parameter", "structuralParameter"))
            if var.causality not in ("parameter", "structuralParameter"):
                raise ConfigurationError(
                    f"{self.name}: {var_name!r} has causality {var.causality!r}, not 'parameter'; "
                    "set its initial value with start_values= instead")
            merged[var_name] = value
        for var_name, value in dict(start_values or {}).items():
            var = self._vars.get(var_name)
            if var is None:
                raise self._unknown(var_name, "settable", ("input", "parameter", "structuralParameter", "local",
                                                            "output"))
            merged[var_name] = value
        for var_name in merged:
            var = self._vars[var_name]
            if var.variability == "constant" or not (settable_in_instantiated(var)
                                                     or settable_in_initialization_mode(var)):
                raise ConfigurationError(
                    f"{self.name}: the start value of {var_name!r} (causality={var.causality}, "
                    f"variability={var.variability}, initial={var.initial}) cannot be set")
        return merged

    def _select(self, spec: PortMap, direction: str) -> Dict[str, str]:
        """Resolve the ``inputs=`` / ``outputs=`` argument into ``{port: fmu_variable}``."""
        default = spec is None
        if default:
            pairs: List[Tuple[Optional[str], str]] = []
            for var in self._vars.values():
                if var.causality != direction:
                    continue
                if var.type not in _SUPPORTED_TYPES or _is_array(var):
                    what = f"array {var.type}" if _is_array(var) else var.type
                    warnings.warn(f"{self.name}: skipping {direction} {var.name!r} of type {what} "
                                  "(only scalar Real/Float, Integer, Enumeration and Boolean variables become ports)",
                                  stacklevel=3)
                    continue
                pairs.append((None, var.name))
        elif isinstance(spec, str):
            pairs = [(None, spec)]
        elif isinstance(spec, Mapping):
            pairs = [(str(port), var_name) for port, var_name in spec.items()]
        else:
            pairs = [(None, var_name) for var_name in spec]

        if direction == "input":
            allowed, what = ("input",), "input"
        else:
            allowed = ("output", "local", "parameter", "calculatedParameter", "structuralParameter", "input")
            what = "output"
        result: Dict[str, str] = {}
        for port, var_name in pairs:
            if not isinstance(var_name, str):
                raise ConfigurationError(f"{self.name}: FMU variable names must be strings (got {var_name!r})")
            var = self._vars.get(var_name)
            if var is None:
                raise self._unknown(var_name, what, ("input",) if direction == "input" else ("output",))
            if var.type not in _SUPPORTED_TYPES or _is_array(var):
                raise ConfigurationError(
                    f"{self.name}: {var_name!r} is {'an array of ' if _is_array(var) else 'of type '}{var.type}; only "
                    "scalar Real/Float, Integer, Enumeration and Boolean variables can be ports")
            if direction == "input":
                tunable = var.causality in ("parameter", "structuralParameter") and var.variability == "tunable"
                if var.causality not in allowed and not tunable:
                    raise ConfigurationError(
                        f"{self.name}: {var_name!r} (causality={var.causality}, variability={var.variability}) "
                        "cannot be changed during the simulation; only inputs and tunable parameters can be input "
                        "ports (set fixed parameters with parameters=)")
            elif var.causality not in allowed:
                raise ConfigurationError(f"{self.name}: {var_name!r} (causality={var.causality}) cannot be read")
            port = port if port is not None else _port_name(var_name)
            if port in result:
                raise ConfigurationError(
                    f"{self.name}: FMU variables {result[port]!r} and {var_name!r} both map to the {direction} port "
                    f"{port!r}; pass a dict {{port_name: fmu_variable}} to name the ports")
            result[port] = var_name
        return result

    def _method(self, var) -> str:
        if var.type in ("Real", "Float64"):
            return "Real" if not self._fmi3 else "Float64"
        if var.type == "Enumeration":
            return "Int64" if self._fmi3 else "Integer"
        return var.type  # Integer, Boolean (FMI 2); Float32, Int8 ... UInt64, Boolean (FMI 3)

    def _groups(self, mapping: Dict[str, str]) -> List[Tuple[str, List[str], List[int], type]]:
        """Group ports by FMI accessor: ``[(type, ports, value_references, python_type)]``."""
        groups: Dict[str, Tuple[List[str], List[int], type]] = {}
        for port, var_name in mapping.items():
            var = self._vars[var_name]
            method = self._method(var)
            py = bool if var.type == "Boolean" else (float if var.type in _FLOAT_TYPES else int)
            ports, vrs, _ = groups.setdefault(method, ([], [], py))
            ports.append(port)
            vrs.append(var.valueReference)
        return [(method, ports, vrs, py) for method, (ports, vrs, py) in groups.items()]

    # ---------------------------------------------------------------- logging
    def _on_log(self, status: int, category: Any, message: Any) -> None:
        # The FMU is instantiated with logging on (many FMUs report their errors only then); routine
        # messages are dropped unless the component's logger is set to DEBUG.
        try:
            if status == 0 and not self._logger.isEnabledFor(logging.DEBUG):
                return
            text = message.decode("utf-8", "replace") if isinstance(message, bytes) else str(message)
            cat = category.decode("utf-8", "replace") if isinstance(category, bytes) else str(category or "")
            label = _STATUS[status] if 0 <= status < len(_STATUS) else str(status)
            level = logging.DEBUG if status == 0 else logging.WARNING if status in (1, 2, 5) else logging.ERROR
            self._logger.log(level, "FMU [%s%s] %s", label, f"/{cat}" if cat else "", text)
            if status > 0:
                self._log_tail = (self._log_tail + [f"[{label}] {' '.join(text.split())}"])[-5:]
        except Exception:  # pragma: no cover - never raise into the FMU
            pass

    def _fail(self, action: str, exc: Exception) -> RuntimeError:
        tail = "; FMU log: " + " | ".join(self._log_tail) if self._log_tail else ""
        return RuntimeError(f"{self.name}: {action} failed: {str(exc).rstrip('.')}{tail}")

    # -------------------------------------------------------------- lifecycle
    def setup(self, ctx) -> None:
        self._release()  # a previous run that was not finalized
        md = self.model_description
        cs = md.coSimulation
        fixed = getattr(cs, "fixedInternalStepSize", None)
        if fixed:
            ratio = ctx.step_size / float(fixed)
            if abs(ratio - round(ratio)) > 1e-9 or round(ratio) < 1:
                warnings.warn(f"{self.name}: the FMU has a fixed internal step of {fixed:g}s, but the component "
                              f"step {ctx.step_size:g}s is not a multiple of it; doStep may fail", stacklevel=2)
        self._log_tail = []
        try:
            self._instantiate(ctx)
        except BaseException:
            self._release()
            raise

    def _instantiate(self, ctx) -> None:
        from fmpy import extract
        from fmpy.simulation import apply_start_values, settable_in_initialization_mode, settable_in_instantiated

        md = self.model_description
        self._unzipdir = tempfile.mkdtemp(prefix=f"pycosim_fmu_{self.name}_")
        extract(str(self.path), unzipdir=self._unzipdir)
        common = dict(guid=md.guid, unzipDirectory=self._unzipdir,
                      modelIdentifier=md.coSimulation.modelIdentifier, instanceName=self.name)
        cwd = os.getcwd()
        try:
            if self._fmi3:
                from fmpy.fmi3 import FMU3Slave

                fmu = FMU3Slave(**common)
                self._fmu = fmu
                fmu.instantiate(loggingOn=True, logMessage=lambda env, status, category, message:
                                self._on_log(status, category, message))
            else:
                from fmpy.fmi2 import FMU2Slave

                fmu = FMU2Slave(**common)
                self._fmu = fmu
                fmu.instantiate(callbacks=self._fmi2_callbacks(), loggingOn=True)
        except Exception as exc:
            os.chdir(cwd)  # fmpy changes the working directory to load the binary and may not restore it on failure
            raise self._fail("instantiating the FMU", exc) from exc
        self._instantiated = True
        try:
            pending = dict(self._start_values)
            if self._fmi3:
                pending = apply_start_values(fmu, md, pending, settable=settable_in_instantiated)
                fmu.enterInitializationMode(startTime=0.0)
            else:
                fmu.setupExperiment(startTime=0.0)
                pending = apply_start_values(fmu, md, pending, settable=settable_in_instantiated)
                fmu.enterInitializationMode()
            pending = apply_start_values(fmu, md, pending, settable=settable_in_initialization_mode)
            if pending:
                raise ConfigurationError(f"could not set the start values of {sorted(pending)}")
            fmu.exitInitializationMode()
        except ConfigurationError as exc:
            raise ConfigurationError(f"{self.name}: {exc}") from exc
        except Exception as exc:
            raise self._fail("initializing the FMU", exc) from exc

    def _fmi2_callbacks(self):
        from ctypes import byref

        from fmpy import calloc, free
        from fmpy.fmi2 import (
            fmi2CallbackAllocateMemoryTYPE,
            fmi2CallbackFreeMemoryTYPE,
            fmi2CallbackFunctions,
            fmi2CallbackLoggerTYPE,
        )

        callbacks = fmi2CallbackFunctions()
        callbacks.logger = fmi2CallbackLoggerTYPE(
            lambda env, instance, status, category, message: self._on_log(status, category, message))
        callbacks.allocateMemory = fmi2CallbackAllocateMemoryTYPE(calloc)
        callbacks.freeMemory = fmi2CallbackFreeMemoryTYPE(free)
        try:  # formats printf-style messages before they reach Python
            from fmpy.logging import addLoggerProxy

            addLoggerProxy(byref(callbacks))
        except Exception:  # pragma: no cover - logging proxy not available on this platform
            pass
        self._callbacks = callbacks  # keep the ctypes callbacks alive
        return callbacks

    def output(self, t):
        fmu = self._fmu
        values: Dict[str, Any] = {}
        try:
            for method, ports, vrs, py in self._out_groups:
                raw = getattr(fmu, "get" + method)(vrs)
                for port, value in zip(ports, raw):
                    values[port] = py(value)
        except Exception as exc:
            raise self._fail(f"reading the outputs at {t}", exc) from exc
        return values

    def update(self, t, inputs):
        fmu = self._fmu
        try:
            for method, ports, vrs, py in self._in_groups:
                given_vrs, given = [], []
                for port, vr in zip(ports, vrs):
                    value = inputs.get(port)
                    if value is None:
                        continue
                    if py is int:
                        value = int(round(float(value)))
                    elif py is bool:
                        value = bool(value)
                    else:
                        value = float(value)
                    given_vrs.append(vr)
                    given.append(value)
                if given_vrs:
                    getattr(fmu, "set" + method)(given_vrs, given)
        except Exception as exc:
            raise self._fail(f"setting the inputs at {t}", exc) from exc
        now = (t - self.ctx.start).total_seconds()
        h = self.ctx.step_size
        try:
            if self._fmi3:
                _event, terminate, early, last = fmu.doStep(currentCommunicationPoint=now, communicationStepSize=h)
            else:
                fmu.doStep(currentCommunicationPoint=now, communicationStepSize=h)
                terminate, early, last = False, False, now + h
        except Exception as exc:
            stopped = None
            if not self._fmi3 and getattr(exc, "status", None) == 2:  # fmi2Discard: did the FMU stop?
                try:
                    if fmu.getBooleanStatus(3):  # fmi2Terminated
                        stopped = fmu.getRealStatus(2)  # fmi2LastSuccessfulTime
                except Exception:
                    pass
            if stopped is not None:
                raise RuntimeError(f"{self.name}: the FMU terminated the simulation at FMU time {stopped:g}s "
                                   f"(co-simulation time {t})") from exc
            raise self._fail(f"doStep({now:g}s, {h:g}s) at {t}", exc) from exc
        if terminate:
            raise RuntimeError(f"{self.name}: the FMU requested to terminate the simulation at FMU time "
                               f"{last:g}s (co-simulation time {t})")
        if early and not math.isclose(last, now + h):
            raise RuntimeError(f"{self.name}: the FMU returned early from doStep at {last:g}s instead of "
                               f"{now + h:g}s")

    def finalize(self) -> None:
        self._release(terminate=True)

    def abort(self) -> None:
        self._release(terminate=True)

    def _release(self, terminate: bool = False) -> None:
        """Terminate and free the FMU instance and delete the extracted files (safe to call repeatedly)."""
        fmu, self._fmu = self._fmu, None
        if fmu is not None:
            if terminate and getattr(self, "_instantiated", False):
                try:
                    fmu.terminate()
                except Exception:
                    pass
            try:
                if getattr(fmu, "component", None):
                    fmu.freeInstance()
                else:
                    fmu.freeLibrary()
            except Exception:
                pass
        self._instantiated = False
        self._callbacks = None
        unzipdir, self._unzipdir = self._unzipdir, None
        if unzipdir:
            self._forget_python(unzipdir)
            shutil.rmtree(unzipdir, ignore_errors=True)

    @staticmethod
    def _forget_python(folder: str) -> None:
        """Undo what Python-based FMUs (pythonfmu) did to this interpreter: sys.path entries and modules."""
        roots = set()
        for form in (os.path.abspath(folder), os.path.realpath(folder)):
            roots.add(os.path.normcase(form).rstrip("\\/") + os.sep)

        def inside(p: Any) -> bool:
            try:
                candidate = os.path.normcase(os.path.abspath(p))
            except (TypeError, ValueError):
                return False
            return any(candidate.startswith(root) for root in roots)

        sys.path[:] = [p for p in sys.path if not (isinstance(p, str) and inside(p))]
        for mod_name, module in list(sys.modules.items()):
            file = getattr(module, "__file__", None)
            if file and inside(file):
                sys.modules.pop(mod_name, None)
