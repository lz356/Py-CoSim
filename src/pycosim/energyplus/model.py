"""Read, edit and write EnergyPlus models through epJSON.

IDF files are converted to epJSON with EnergyPlus' own converter
(``energyplus --convert-only``), so every field is accessed by its official
name and the model is always parsed exactly as EnergyPlus parses it. Converted
files are cached by content hash.

Typical edits needed for co-simulation are one-liners::

    model = EnergyPlusModel.load("small_office.idf")
    model.set_timesteps_per_hour(4)
    model.set_run_period("2023-07-01", "2023-07-07")   # inclusive days
    model.use_weather_file_sizing()                    # size HVAC for the EPW climate
    model.scale_floor_area(1.08)                       # +8 % floor area (x/y stretch)
    model.save("office_variant.epJSON")
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from ..core.errors import PyCoSimError
from .discovery import EnergyPlusInstall, find_energyplus, run_energyplus_cli

__all__ = ["EnergyPlusModel", "ModelError", "VALID_TIMESTEPS"]

VALID_TIMESTEPS = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60)
_COORD_KEY = re.compile(r"^(vertex_\d+_)?(vertex_)?[xy]_coordinate$|^[xy]_origin$|^[xy]_coordinate_of_reference_point$")
_SIMPLE_GEOMETRY = (
    "Wall:Exterior", "Wall:Adiabatic", "Wall:Underground", "Wall:Interzone", "Roof", "Ceiling:Adiabatic",
    "Ceiling:Interzone", "Floor:GroundContact", "Floor:Adiabatic", "Floor:Interzone", "Window", "Door",
    "GlazedDoor", "Window:Interzone", "Door:Interzone", "GlazedDoor:Interzone",
)


class ModelError(PyCoSimError, RuntimeError):
    """The EnergyPlus model cannot be read or edited as requested."""


def _cache_dir() -> Path:
    base = os.environ.get("PYCOSIM_CACHE_DIR")
    path = Path(base) if base else Path(tempfile.gettempdir()) / "pycosim_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _to_date(value: Union[str, date, datetime]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


class EnergyPlusModel:
    """An EnergyPlus input model held as an epJSON dictionary.

    Attributes
    ----------
    data : dict
        The epJSON content ``{class: {object_name: {field: value}}}``. Edit it
        freely; helper methods cover the common co-simulation edits.
    source : Path or None
        File the model was loaded from.
    """

    def __init__(self, data: Dict[str, Any], source: Optional[Path] = None):
        self.data = data
        self.source = Path(source) if source is not None else None

    # ------------------------------------------------------------------- I/O
    @classmethod
    def load(cls, path: Union[str, Path], install: Optional[EnergyPlusInstall] = None) -> "EnergyPlusModel":
        """Load an ``.idf`` or ``.epJSON`` file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"EnergyPlus model not found: {path}")
        suffix = path.suffix.lower()
        if suffix == ".epjson":
            with open(path, "r", encoding="utf-8") as fh:
                model = cls(json.load(fh), path)
            if install is not None:
                model._check_version(install)
            return model
        if suffix != ".idf":
            raise ModelError(f"Unsupported model format {path.suffix!r}; use .idf or .epJSON")
        install = install or find_energyplus(require_api=False)
        content = path.read_bytes()
        digest = hashlib.sha1(content + install.version_str.encode()).hexdigest()[:16]
        cached = _cache_dir() / f"{path.stem}-{digest}.epJSON"
        if not cached.exists():
            work = Path(tempfile.mkdtemp(prefix="pycosim_conv_"))
            try:
                src = work / path.name
                shutil.copyfile(path, src)
                proc = run_energyplus_cli(install, ["--convert-only", "-d", str(work), str(src)], cwd=work, timeout=300)
                produced = work / (path.stem + ".epJSON")
                valid = False
                if produced.exists():
                    try:
                        with open(produced, "r", encoding="utf-8") as fh:
                            content_obj = json.load(fh)
                        valid = isinstance(content_obj, dict) and bool(content_obj)
                    except (json.JSONDecodeError, OSError):
                        valid = False
                if not valid:
                    err = ""
                    if (work / "eplusout.err").exists():
                        err = (work / "eplusout.err").read_text(errors="replace")[-3000:]
                    raise ModelError(
                        f"EnergyPlus could not convert {path.name} to epJSON.\n{proc.stdout[-2000:]}\n{err}"
                    )
                shutil.copyfile(produced, cached)
            finally:
                shutil.rmtree(work, ignore_errors=True)
        with open(cached, "r", encoding="utf-8") as fh:
            model = cls(json.load(fh), path)
        model._check_version(install)
        return model

    def save(self, path: Union[str, Path]) -> Path:
        """Write the model as epJSON (EnergyPlus runs epJSON directly)."""
        path = Path(path)
        if path.suffix.lower() != ".epjson":
            path = path.with_suffix(".epJSON")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1)
        return path

    def copy(self) -> "EnergyPlusModel":
        return EnergyPlusModel(copy.deepcopy(self.data), self.source)

    # --------------------------------------------------------------- objects
    @property
    def version(self) -> Optional[str]:
        ver = self.data.get("Version")
        if not ver:
            return None
        return str(next(iter(ver.values())).get("version_identifier"))

    def _check_version(self, install: EnergyPlusInstall) -> None:
        ver = self.version
        if ver is None:
            return
        parts = ver.split(".")
        model_v = tuple(int(p) for p in parts[:2] if p.isdigit())
        if model_v and model_v != install.version[:2]:
            raise ModelError(
                f"Model {self.source.name if self.source else ''} is EnergyPlus version {ver} but the installation "
                f"is {install.version_str}. Update the model with EnergyPlus' IDFVersionUpdater "
                f"(in {install.root / 'PreProcess' / 'IDFVersionUpdater'}) or install EnergyPlus {ver}."
            )

    def classes(self) -> List[str]:
        return sorted(self.data)

    def objects(self, cls: str) -> Dict[str, Dict[str, Any]]:
        """All objects of a class as ``{name: fields}`` (empty dict if none)."""
        return self.data.get(cls, {})

    def names(self, cls: str) -> List[str]:
        return list(self.objects(cls))

    def get(self, cls: str, name: Optional[str] = None) -> Dict[str, Any]:
        objs = self.objects(cls)
        if not objs:
            raise KeyError(f"Model has no {cls} objects")
        if name is None:
            if len(objs) != 1:
                raise KeyError(f"Model has {len(objs)} {cls} objects; specify a name from {list(objs)[:10]}")
            return next(iter(objs.values()))
        for key, value in objs.items():
            if key.lower() == name.lower():
                return value
        raise KeyError(f"No {cls} named {name!r}. Available: {list(objs)[:20]}")

    def add(self, cls: str, name: str, **fields: Any) -> Dict[str, Any]:
        self.data.setdefault(cls, {})[name] = fields
        return fields

    def remove(self, cls: str, name: Optional[str] = None) -> None:
        if name is None:
            self.data.pop(cls, None)
        else:
            self.data.get(cls, {}).pop(name, None)

    def set_field(self, cls: str, name: str, field: str, value: Any) -> None:
        self.get(cls, name)[field] = value

    @property
    def zone_names(self) -> List[str]:
        return self.names("Zone")

    # ----------------------------------------------------------------- time
    @property
    def timesteps_per_hour(self) -> int:
        ts = self.data.get("Timestep")
        if not ts:
            return 6  # EnergyPlus default
        return int(next(iter(ts.values())).get("number_of_timesteps_per_hour", 6))

    def set_timesteps_per_hour(self, n: int) -> None:
        n = int(n)
        if n not in VALID_TIMESTEPS:
            raise ModelError(f"EnergyPlus supports {VALID_TIMESTEPS} time steps per hour, not {n}")
        self.data["Timestep"] = {"Timestep 1": {"number_of_timesteps_per_hour": n}}

    @property
    def sizing_window_steps(self) -> Optional[int]:
        """``Timesteps in Averaging Window`` of ``Sizing:Parameters`` (None if the model has none)."""
        for obj in self.data.get("Sizing:Parameters", {}).values():
            value = obj.get("timesteps_in_averaging_window")
            return int(value) if value not in (None, "") else None
        return None

    def set_sizing_window_minutes(self, minutes: float) -> None:
        """Average design loads for autosizing over about ``minutes`` whatever the time step.

        EnergyPlus counts the sizing averaging window in *time steps*. A model
        prepared with 4 steps per hour and a 6-step window (90 min) gets a 6-min
        window at 60 steps per hour, which can enlarge autosized coils and fans
        considerably. Call this after :meth:`set_timesteps_per_hour` to keep
        the window's duration.
        """
        ts = self.timesteps_per_hour
        n = max(1, int(round(float(minutes) / (60.0 / ts))))
        objs = self.data.setdefault("Sizing:Parameters", {"Sizing:Parameters 1": {}})
        for obj in objs.values():
            obj["timesteps_in_averaging_window"] = n

    def get_run_period(self) -> Tuple[date, date, Optional[int]]:
        """``(first_day, last_day, begin_year)`` of the first RunPeriod (days inclusive)."""
        rps = self.objects("RunPeriod")
        if not rps:
            raise ModelError("Model has no RunPeriod object")
        rp = next(iter(rps.values()))
        year = rp.get("begin_year")
        y = int(year) if year not in (None, "") else 2023
        begin = date(y, int(rp["begin_month"]), int(rp["begin_day_of_month"]))
        end_year = rp.get("end_year")
        ey = int(end_year) if end_year not in (None, "") else y
        end = date(ey, int(rp["end_month"]), int(rp["end_day_of_month"]))
        if end < begin and end_year in (None, ""):
            end = date(y + 1, end.month, end.day)
        return begin, end, int(year) if year not in (None, "") else None

    def set_run_period(self, first_day: Union[str, date, datetime], last_day: Union[str, date, datetime],
                       name: str = "PyCoSim RunPeriod") -> None:
        """Replace all RunPeriods by one covering ``first_day``..``last_day`` (inclusive).

        The calendar year is written explicitly so that weekdays and holidays
        follow the real calendar of the co-simulation clock.
        """
        first, last = _to_date(first_day), _to_date(last_day)
        if last < first:
            raise ModelError("last_day must not be before first_day")
        template = next(iter(self.objects("RunPeriod").values()), {})
        keep = {
            k: v for k, v in template.items()
            if k.startswith("use_") or k.startswith("apply_") or k in ("treat_weather_as_actual",)
        }
        keep.update(
            begin_month=first.month, begin_day_of_month=first.day, begin_year=first.year,
            end_month=last.month, end_day_of_month=last.day, end_year=last.year,
        )
        self.data["RunPeriod"] = {name: keep}

    @property
    def has_daylight_saving(self) -> bool:
        return bool(self.objects("RunPeriodControl:DaylightSavingTime"))

    def remove_daylight_saving(self) -> None:
        """Remove daylight-saving time so schedules follow local *standard* time.

        Py-CoSim's clock, EnergyPlus' internal clock and EPW weather data all
        use local standard time; with a ``RunPeriodControl:DaylightSavingTime``
        object, schedules inside EnergyPlus shift by one hour in summer.
        """
        self.remove("RunPeriodControl:DaylightSavingTime")
        for rp in self.objects("RunPeriod").values():
            rp["use_weather_file_daylight_saving_period"] = "No"

    def set_simulation_control(self, **fields: Any) -> None:
        """Update SimulationControl, e.g. ``run_simulation_for_sizing_periods="No"``."""
        sc = self.data.setdefault("SimulationControl", {"SimulationControl 1": {}})
        next(iter(sc.values())).update(fields)

    def use_weather_file_sizing(self, summer: str = "SummerExtreme", winter: str = "WinterExtreme") -> None:
        """Size HVAC from extreme periods of the weather file instead of design days.

        Replaces all ``SizingPeriod:DesignDay`` objects by
        ``SizingPeriod:WeatherFileConditionType`` objects. This lets a model
        built for one climate be co-simulated consistently in another one.
        """
        self.remove("SizingPeriod:DesignDay")
        self.remove("SizingPeriod:WeatherFileDays")
        self.data["SizingPeriod:WeatherFileConditionType"] = {
            "PyCoSim summer sizing": {"period_selection": summer, "day_of_week_for_start_day": "Monday",
                                      "use_weather_file_daylight_saving_period": "No",
                                      "use_weather_file_rain_and_snow_indicators": "Yes"},
            "PyCoSim winter sizing": {"period_selection": winter, "day_of_week_for_start_day": "Monday",
                                      "use_weather_file_daylight_saving_period": "No",
                                      "use_weather_file_rain_and_snow_indicators": "Yes"},
        }
        self.set_simulation_control(run_simulation_for_sizing_periods="No")

    # --------------------------------------------------------------- outputs
    def add_output_variable(self, variable: str, key: str = "*", frequency: str = "Timestep") -> None:
        name = f"PyCoSim {variable} {key} {frequency}"
        self.data.setdefault("Output:Variable", {})[name] = {
            "key_value": key, "variable_name": variable, "reporting_frequency": frequency,
        }

    def add_output_meter(self, meter: str, frequency: str = "Timestep") -> None:
        self.data.setdefault("Output:Meter", {})[f"PyCoSim {meter} {frequency}"] = {
            "key_name": meter, "reporting_frequency": frequency,
        }

    def enable_sqlite(self) -> None:
        self.data["Output:SQLite"] = {"Output:SQLite 1": {"option_type": "SimpleAndTabular"}}

    # -------------------------------------------------------------- geometry
    def scale_floor_area(self, factor: float) -> "EnergyPlusModel":
        """Stretch the plan by ``sqrt(factor)`` in x and y (heights unchanged).

        Floor areas, envelope areas and window positions scale consistently;
        autosized HVAC equipment follows automatically. Only detailed
        (vertex-based) geometry is supported.
        """
        if factor <= 0:
            raise ModelError("factor must be positive")
        bad = [c for c in _SIMPLE_GEOMETRY if c in self.data]
        if bad:
            raise ModelError(f"scale_floor_area only supports detailed geometry; model uses {bad}")
        s = math.sqrt(factor)
        for cls, objs in self.data.items():
            for fields in objs.values():
                if not isinstance(fields, dict):
                    continue
                for key, value in list(fields.items()):
                    if isinstance(value, (int, float)) and _COORD_KEY.match(key):
                        fields[key] = value * s
                    elif key == "vertices" and isinstance(value, list):
                        for v in value:
                            for vk in ("vertex_x_coordinate", "vertex_y_coordinate"):
                                if isinstance(v.get(vk), (int, float)):
                                    v[vk] = v[vk] * s
                if cls == "Zone":
                    for key in ("floor_area", "volume"):
                        if isinstance(fields.get(key), (int, float)):
                            fields[key] = fields[key] * factor
                elif cls == "InternalMass" and isinstance(fields.get("surface_area"), (int, float)):
                    fields["surface_area"] = fields["surface_area"] * factor
                elif cls == "Construction:FfactorGroundFloor":
                    if isinstance(fields.get("area"), (int, float)):
                        fields["area"] = fields["area"] * factor
                    if isinstance(fields.get("perimeter_exposed"), (int, float)):
                        fields["perimeter_exposed"] = fields["perimeter_exposed"] * s
        return self

    def __repr__(self) -> str:
        src = self.source.name if self.source else "in-memory"
        return f"<EnergyPlusModel {src}: {sum(len(v) for v in self.data.values())} objects, version {self.version}>"
