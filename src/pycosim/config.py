"""Build a co-simulation from a YAML (or dict) scenario file.

Example ``scenario.yaml``::

    simulation:
      name: precooling-demo
      start: 2023-07-03
      end: 2023-07-10
      step: 15min

    components:
      weather:
        type: EPWWeather
        path: weather/tucson.epw
      house:
        type: RCBuilding
        floor_area: 150
      ctrl:
        type: PrecoolingController      # any class in pycosim.components
        peak_hours: [16, 21]
      custom:
        type: my_module:MyController   # or import your own class ("module:Class")
      rule:
        function: my_module:my_rule     # or wrap a plain function
        outputs: [clg_sp]

    connections:
      - weather.T_out -> house.T_out
      - ctrl.clg_sp -> house.clg_sp
      - {from: [a.P, b.P], to: meter.P_net, reduce: sum}       # or reduce: module:function, transform: module:function

Relative paths (keys ``path``, ``model``, ``weather``, ``data``) are resolved
against the YAML file's folder, which is also added to ``sys.path`` so local
modules can be imported.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

import yaml

from .core.component import Component, FunctionComponent
from .core.errors import ConfigurationError
from .core.master import CoSimulation

__all__ = ["load_scenario", "build_simulation"]

_PATH_KEYS = {"path", "model", "weather", "data"}


def _import(ref: str):
    if ":" in ref:
        module, attr = ref.split(":", 1)
    else:
        module, _, attr = ref.rpartition(".")
    if not module:
        raise ConfigurationError(f"Cannot import {ref!r}; use 'module:Name'")
    obj = importlib.import_module(module)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _resolve_type(name: str):
    if ":" in name or "." in name:
        return _import(name)
    from . import components as lib
    from .energyplus.component import EnergyPlusComponent

    registry = {n: getattr(lib, n) for n in lib.__all__}
    registry["EnergyPlusComponent"] = EnergyPlusComponent
    registry["EnergyPlus"] = EnergyPlusComponent
    if name not in registry:
        raise ConfigurationError(f"Unknown component type {name!r}. Built-in types: {sorted(registry)}")
    return registry[name]


def _resolve_paths(params: Dict[str, Any], base: Path) -> Dict[str, Any]:
    out = {}
    for key, value in params.items():
        if key in _PATH_KEYS and isinstance(value, str) and not Path(value).is_absolute():
            value = str((base / value).resolve())
        out[key] = value
    return out


def build_simulation(spec: Mapping[str, Any], base_dir: Union[str, Path] = ".", **overrides: Any) -> CoSimulation:
    """Create a :class:`CoSimulation` from a scenario dictionary."""
    base = Path(base_dir).resolve()
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    sim_kw = dict(spec.get("simulation", {}))
    sim_kw.update(overrides)
    if "output_dir" in sim_kw and not Path(str(sim_kw["output_dir"])).is_absolute():
        sim_kw["output_dir"] = str(base / sim_kw["output_dir"])
    sim = CoSimulation(**sim_kw)
    comps = spec.get("components") or {}
    if not isinstance(comps, Mapping):
        raise ConfigurationError("'components' must be a mapping of name -> settings")
    for name, settings in comps.items():
        settings = dict(settings or {})
        if "function" in settings:
            func = _import(settings.pop("function"))
            comp: Component = FunctionComponent(func, name=name, inputs=settings.pop("inputs", None),
                                                outputs=settings.pop("outputs", None),
                                                step_size=settings.pop("step_size", None))
            if settings:
                raise ConfigurationError(f"{name}: unknown settings for a function component: {sorted(settings)}")
        else:
            if "type" not in settings:
                raise ConfigurationError(f"Component {name!r} needs a 'type' (or 'function')")
            cls = _resolve_type(settings.pop("type"))
            comp = cls(name, **_resolve_paths(settings, base))
        sim.add(comp)
    for item in spec.get("connections") or []:
        if isinstance(item, str):
            if "->" not in item:
                raise ConfigurationError(f"Connection {item!r} must look like 'a.out -> b.in'")
            src, dst = (s.strip() for s in item.split("->", 1))
            sim.connect(src, dst)
        elif isinstance(item, Mapping):
            reduce = item.get("reduce")
            if isinstance(reduce, str) and reduce not in ("sum", "mean", "max", "min", "list"):
                reduce = _import(reduce)
            transform = item.get("transform")
            if isinstance(transform, str):
                transform = _import(transform)
            sim.connect(item["from"], item["to"], delay=bool(item.get("delay", False)), reduce=reduce,
                        transform=transform)
        else:
            raise ConfigurationError(f"Cannot read connection {item!r}")
    return sim


def load_scenario(path: Union[str, Path], **overrides: Any) -> CoSimulation:
    """Read a YAML scenario file and return the (not yet run) co-simulation."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as fh:
        spec = yaml.safe_load(fh) or {}
    return build_simulation(spec, base_dir=path.parent, **overrides)
