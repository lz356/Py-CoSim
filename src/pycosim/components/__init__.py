"""Ready-made components for common co-simulation tasks.

=========================  =========================================================
Weather and data           :class:`EPWWeather`, :class:`TimeSeriesSource`,
                           :class:`DailyProfile`, :class:`Constant`
Buildings (no EnergyPlus)  :class:`RCBuilding`
Energy resources / grid    :class:`PVSystem`, :class:`Battery`,
                           :class:`TimeOfUseTariff`, :class:`GridMeter`
Controls                   :class:`ThermostatSchedule`, :class:`PrecoolingController`,
                           :class:`PIController`, :class:`PeakShavingController`
People and travel          :class:`HouseholdOccupancy`, :class:`OccupancyMLP`
AI decision making         :class:`LLMSupervisor` and LLM backends
FMI / Modelica models      :class:`FMUComponent` (co-simulation FMUs; needs ``fmpy``)
=========================  =========================================================

EnergyPlus buildings live in :mod:`pycosim.energyplus`
(``pycosim.EnergyPlusComponent``).
"""
from typing import Any

from .controllers import PeakShavingController, PIController, PrecoolingController, ThermostatSchedule
from .energy import Battery, GridMeter, PVSystem, TimeOfUseTariff
from .llm import (
    AnthropicBackend,
    FaultInjectionBackend,
    LLMBackend,
    LLMSupervisor,
    OfflineStandInBackend,
    OllamaBackend,
    OpenAIBackend,
    RecordingBackend,
    ReplayBackend,
    StochasticStandInBackend,
)
from .occupancy import HouseholdOccupancy, OccupancyMLP, load_household_profiles, select_households
from .rc_building import RCBuilding
from .sources import Constant, DailyProfile, TimeSeriesSource
from .weather import EPWWeather, read_epw, solar_position

__all__ = [
    "EPWWeather", "read_epw", "solar_position",
    "TimeSeriesSource", "DailyProfile", "Constant",
    "RCBuilding",
    "PVSystem", "Battery", "TimeOfUseTariff", "GridMeter",
    "ThermostatSchedule", "PrecoolingController", "PIController", "PeakShavingController",
    "HouseholdOccupancy", "OccupancyMLP", "load_household_profiles", "select_households",
    "LLMSupervisor", "LLMBackend", "AnthropicBackend", "OpenAIBackend", "OllamaBackend",
    "OfflineStandInBackend", "StochasticStandInBackend", "FaultInjectionBackend", "RecordingBackend",
    "ReplayBackend",
    "FMUComponent",
]

# Optional components, imported on first use so that this package works without their dependencies.
_LAZY = {"FMUComponent": ".fmu"}


def __getattr__(name: str) -> Any:  # PEP 562
    if name in _LAZY:
        from importlib import import_module

        value = getattr(import_module(_LAZY[name], __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
