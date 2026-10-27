"""Py-CoSim: a Python co-simulation framework for buildings and everything around them.

Py-CoSim couples EnergyPlus (through its Python API) with any other Python
model -- weather data, PV, batteries, tariffs, machine-learning models,
large language models, reinforcement-learning agents -- on a common,
synchronized clock.

Quick start::

    import pycosim as pc

    @pc.component(outputs="clg_sp")
    def thermostat(t, T_zone=24.0):
        return 24.0 if 8 <= t.hour < 18 else 29.0

    sim = pc.CoSimulation(start="2023-07-01", end="2023-07-08", step="15min")
    zone = sim.add(pc.components.RCBuilding("zone"))
    weather = sim.add(pc.components.EPWWeather("weather", "tucson.epw"))
    sim.add(thermostat)
    sim.connect("weather.T_out", "zone.T_out")
    sim.connect("zone.T_zone", "thermostat.T_zone")
    sim.connect("thermostat.clg_sp", "zone.clg_sp")
    results = sim.run()

Heavy sub-packages are imported lazily so that ``import pycosim`` stays fast
(this also keeps EnergyPlus worker processes light-weight).
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from ._version import __version__

__all__ = [
    "__version__",
    "Component",
    "FunctionComponent",
    "component",
    "Inputs",
    "Port",
    "CoSimulation",
    "Results",
    "Context",
    "observers",
    "components",
    "energyplus",
    "EnergyPlusComponent",
    "EnergyPlusModel",
    "errors",
]

_LAZY = {
    "Component": ("pycosim.core.component", "Component"),
    "FunctionComponent": ("pycosim.core.component", "FunctionComponent"),
    "component": ("pycosim.core.component", "component"),
    "Inputs": ("pycosim.core.component", "Inputs"),
    "Port": ("pycosim.core.component", "Port"),
    "Context": ("pycosim.core.context", "Context"),
    "CoSimulation": ("pycosim.core.master", "CoSimulation"),
    "Results": ("pycosim.core.results", "Results"),
    "observers": ("pycosim.observers", None),
    "errors": ("pycosim.errors", None),
    "components": ("pycosim.components", None),
    "energyplus": ("pycosim.energyplus", None),
    "EnergyPlusComponent": ("pycosim.energyplus.component", "EnergyPlusComponent"),
    "EnergyPlusModel": ("pycosim.energyplus.model", "EnergyPlusModel"),
}


def __getattr__(name: str) -> Any:  # PEP 562 lazy attribute access
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module 'pycosim' has no attribute {name!r}") from None
    module = import_module(module_name)
    value = module if attr is None else getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:  # pragma: no cover - for static type checkers and IDEs only
    from . import components, energyplus
    from . import errors, observers
    from .core.component import Component, FunctionComponent, Inputs, Port, component
    from .core.context import Context
    from .core.master import CoSimulation
    from .core.results import Results
    from .energyplus.component import EnergyPlusComponent
    from .energyplus.model import EnergyPlusModel
