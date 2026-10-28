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
=========================  =========================================================

EnergyPlus buildings live in :mod:`pycosim.energyplus`
(``pycosim.EnergyPlusComponent``).
"""
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
    "OfflineStandInBackend", "FaultInjectionBackend", "RecordingBackend", "ReplayBackend",
]
