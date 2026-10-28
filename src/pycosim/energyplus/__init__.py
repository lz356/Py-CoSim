"""EnergyPlus integration: discovery, model editing and the steppable component.

Sub-modules are imported lazily so the light-weight worker process that runs
EnergyPlus does not need to import pandas/numpy.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "EnergyPlusComponent",
    "EnergyPlusModel",
    "EnergyPlusInstall",
    "find_energyplus",
    "list_available_handles",
]

_LAZY = {
    "EnergyPlusComponent": "pycosim.energyplus.component",
    "EnergyPlusModel": "pycosim.energyplus.model",
    "EnergyPlusInstall": "pycosim.energyplus.discovery",
    "find_energyplus": "pycosim.energyplus.discovery",
    "list_available_handles": "pycosim.energyplus.handles",
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY:
        raise AttributeError(f"module 'pycosim.energyplus' has no attribute {name!r}")
    value = getattr(import_module(_LAZY[name]), name)
    globals()[name] = value
    return value
