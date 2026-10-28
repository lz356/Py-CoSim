"""Weather from EnergyPlus EPW files, plus a small solar-position model."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from ..core.component import Component

__all__ = ["EPWWeather", "read_epw", "EPWLocation", "solar_position"]

# EPW column index -> (output name, unit)
EPW_FIELDS: Dict[str, Tuple[int, str]] = {
    "T_out": (6, "C"),
    "T_dew": (7, "C"),
    "RH": (8, "%"),
    "pressure": (9, "Pa"),
    "IR_horizontal": (12, "W/m2"),
    "GHI": (13, "W/m2"),
    "DNI": (14, "W/m2"),
    "DHI": (15, "W/m2"),
    "wind_direction": (20, "deg"),
    "wind_speed": (21, "m/s"),
    "sky_cover": (22, "tenths"),
    "precipitation": (33, "mm"),
}
_CUM_DAYS = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])


@dataclass(frozen=True)
class EPWLocation:
    city: str
    latitude: float
    longitude: float
    timezone: float
    elevation: float


def read_epw(path: Union[str, Path]) -> Tuple[pd.DataFrame, EPWLocation]:
    """Read an EPW file into a DataFrame (8760 hourly rows) and its location."""
    path = Path(path)
    with open(path, "r", encoding="latin-1") as fh:
        header = [next(fh) for _ in range(8)]
        rows = [line.rstrip("\n").split(",") for line in fh if line.strip()]
    loc = header[0].split(",")
    location = EPWLocation(loc[1], float(loc[6]), float(loc[7]), float(loc[8]), float(loc[9]))
    data = {name: np.array([float(r[idx]) for r in rows]) for name, (idx, _u) in EPW_FIELDS.items()}
    data["month"] = np.array([int(r[1]) for r in rows])
    data["day"] = np.array([int(r[2]) for r in rows])
    data["hour"] = np.array([int(r[3]) for r in rows])
    df = pd.DataFrame(data)
    if len(df) not in (8760, 8784):
        raise ValueError(f"{path.name}: expected 8760 hourly records, found {len(df)}")
    if len(df) == 8784:  # drop Feb 29 of leap-year files
        df = df[~((df.month == 2) & (df.day == 29))].reset_index(drop=True)
    return df, location


def hour_of_year(t: datetime) -> float:
    """Hours since Jan 1 00:00 in a non-leap calendar (Feb 29 maps onto Feb 28)."""
    day = min(t.day, 28) if t.month == 2 else t.day
    return (_CUM_DAYS[t.month - 1] + day - 1) * 24.0 + t.hour + t.minute / 60.0 + t.second / 3600.0


def solar_position(t: datetime, latitude: float, longitude: float, timezone: float) -> Tuple[float, float]:
    """Solar zenith and azimuth (degrees, azimuth clockwise from north) at local standard time ``t``.

    Spencer (1971) declination / equation of time; accuracy ~0.5 deg, ample for
    building-scale PV and solar-gain estimates.
    """
    n = hour_of_year(t) / 24.0  # fractional day of year (0-based)
    b = 2.0 * math.pi * n / 365.0
    decl = (0.006918 - 0.399912 * math.cos(b) + 0.070257 * math.sin(b) - 0.006758 * math.cos(2 * b)
            + 0.000907 * math.sin(2 * b) - 0.002697 * math.cos(3 * b) + 0.00148 * math.sin(3 * b))
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(b) - 0.032077 * math.sin(b)
                    - 0.014615 * math.cos(2 * b) - 0.040849 * math.sin(2 * b))
    clock = t.hour + t.minute / 60.0 + t.second / 3600.0
    solar_time = clock + (4.0 * (longitude - 15.0 * timezone) + eot) / 60.0
    omega = math.radians(15.0 * (solar_time - 12.0))
    phi = math.radians(latitude)
    cos_z = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(omega)
    cos_z = max(-1.0, min(1.0, cos_z))
    zenith = math.acos(cos_z)
    sin_z = math.sin(zenith)
    if sin_z < 1e-9:
        return math.degrees(zenith), 180.0
    cos_az = (math.sin(decl) - cos_z * math.sin(phi)) / (sin_z * math.cos(phi))
    az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))  # from north, morning side
    if omega > 0:
        az = 360.0 - az
    return math.degrees(zenith), az


class EPWWeather(Component):
    """Publishes weather variables from an EPW file on the co-simulation clock.

    The value at time ``t`` is linearly interpolated between hourly records
    (EPW record *h* describes the hour ending at *h*:00), which is how
    EnergyPlus itself interpolates weather for sub-hourly time steps. Hence
    ``EPWWeather.T_out`` at ``t`` equals the outdoor temperature an
    EnergyPlus component reports at ``t``.

    Outputs (default): ``T_out`` [C], ``RH`` [%], ``GHI``, ``DNI``, ``DHI``
    [W/m2], ``wind_speed`` [m/s], plus ``solar_zenith``/``solar_azimuth`` [deg].
    """

    def __init__(self, name: str, path: Union[str, Path], fields: Optional[Iterable[str]] = None,
                 interpolate: bool = True, solar: bool = True):
        super().__init__(name)
        self.path = Path(path)
        self.data, self.location = read_epw(self.path)
        self.fields = list(fields) if fields is not None else ["T_out", "RH", "GHI", "DNI", "DHI", "wind_speed"]
        unknown = [f for f in self.fields if f not in EPW_FIELDS]
        if unknown:
            raise ValueError(f"Unknown EPW fields {unknown}; choose from {sorted(EPW_FIELDS)}")
        self.interpolate = interpolate
        self.solar = solar
        self._arrays = {f: self.data[f].to_numpy() for f in self.fields}
        for f in self.fields:
            self.add_output(f, unit=EPW_FIELDS[f][1], kind="instant")
        if solar:
            self.add_output("solar_zenith", unit="deg", kind="instant")
            self.add_output("solar_azimuth", unit="deg", kind="instant")

    def value_at(self, field: str, t: datetime) -> float:
        arr = self._arrays[field] if field in self._arrays else self.data[field].to_numpy()
        x = hour_of_year(t) - 1.0  # record index whose end time is t
        if not self.interpolate:
            return float(arr[int(math.ceil(x)) % 8760])
        i0 = int(math.floor(x))
        w = x - i0
        a, b = arr[i0 % 8760], arr[(i0 + 1) % 8760]
        return float(a + (b - a) * w)

    def step(self, t, inputs):
        out = {f: self.value_at(f, t) for f in self.fields}
        if self.solar:
            z, az = solar_position(t, self.location.latitude, self.location.longitude, self.location.timezone)
            out["solar_zenith"], out["solar_azimuth"] = z, az
        return out
