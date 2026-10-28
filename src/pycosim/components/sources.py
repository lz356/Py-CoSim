"""Generic signal sources: constants, daily profiles and arbitrary time series."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Union

import numpy as np
import pandas as pd

from ..core.component import Component

__all__ = ["Constant", "DailyProfile", "TimeSeriesSource"]


class Constant(Component):
    """Outputs constant values, e.g. ``Constant("limits", P_max=50e3, T_max=26)``."""

    def __init__(self, name: str, **values: Any):
        super().__init__(name)
        if not values:
            raise ValueError("Constant needs at least one keyword value")
        self.values = values
        for key, value in values.items():
            self.add_output(key, initial=value, kind="hold")

    def step(self, t, inputs):
        return dict(self.values)


class DailyProfile(Component):
    """A value that follows a 24-hour profile (with an optional weekend profile).

    Parameters
    ----------
    profile : sequence of 24 floats or ``{hour: value}``
        Value for each hour of the day (step-wise constant). A dict gives
        change points, e.g. ``{0: 29, 7: 24, 19: 29}``.
    weekend : same format, optional
        Profile for Saturdays and Sundays.
    output : str
        Output port name (default ``"value"``).
    """

    def __init__(self, name: str, profile: Union[Sequence[float], Mapping[int, float]],
                 weekend: Optional[Union[Sequence[float], Mapping[int, float]]] = None, output: str = "value",
                 unit: Optional[str] = None, kind: Optional[str] = "hold"):
        super().__init__(name)
        self.weekday_profile = self._expand(profile)
        self.weekend_profile = self._expand(weekend) if weekend is not None else self.weekday_profile
        self.output_name = output
        self.add_output(output, unit=unit, kind=kind)

    @staticmethod
    def _expand(profile) -> np.ndarray:
        if isinstance(profile, Mapping):
            points = sorted((int(h), float(v)) for h, v in profile.items())
            out = np.empty(24)
            for h in range(24):
                applicable = [v for ph, v in points if ph <= h]
                out[h] = applicable[-1] if applicable else points[-1][1]
            return out
        arr = np.asarray(profile, dtype=float)
        if arr.shape != (24,):
            raise ValueError("profile must have 24 hourly values")
        return arr

    def step(self, t: datetime, inputs):
        prof = self.weekend_profile if t.weekday() >= 5 else self.weekday_profile
        return {self.output_name: float(prof[t.hour])}


class TimeSeriesSource(Component):
    """Plays back measured or pre-computed data (CSV file, Series or DataFrame).

    Parameters
    ----------
    data : path, pandas.Series or pandas.DataFrame
        Must have a datetime index (or a first column that parses as dates).
        Each column becomes an output port.
    method : {"previous", "linear", "nearest"}
        How to read values between samples.
    repeat_yearly : bool
        Map the simulation time onto the data's year (handy for typical-year
        data such as TMY tariffs or load profiles).
    """

    def __init__(self, name: str, data: Union[str, Path, pd.Series, pd.DataFrame], method: str = "previous",
                 repeat_yearly: bool = False, columns: Optional[Sequence[str]] = None, units: Optional[Dict[str, str]] = None,
                 kind: Optional[str] = None, interval: Optional[str] = None):
        super().__init__(name)
        if isinstance(data, (str, Path)):
            data = pd.read_csv(data, index_col=0, parse_dates=True)
        if isinstance(data, pd.Series):
            data = data.to_frame(name=data.name or "value")
        if not isinstance(data.index, pd.DatetimeIndex):
            data.index = pd.to_datetime(data.index)
        data = data.sort_index()
        if columns is not None:
            data = data[list(columns)]
        if method not in ("previous", "linear", "nearest"):
            raise ValueError("method must be 'previous', 'linear' or 'nearest'")
        self.data = data
        self.method = method
        self.repeat_yearly = repeat_yearly
        self._t = data.index.values.astype("datetime64[ns]").astype(np.int64).astype(np.float64)
        self._v = {c: data[c].to_numpy(dtype=float) for c in data.columns}
        for col in data.columns:  # kind/interval: what each data point means in time (see pycosim.Port)
            self.add_output(str(col), unit=(units or {}).get(str(col)), kind=kind, interval=interval)

    def _map_time(self, t: datetime) -> datetime:
        if not self.repeat_yearly:
            return t
        year = self.data.index[0].year
        try:
            return t.replace(year=year)
        except ValueError:  # Feb 29 onto non-leap year
            return t.replace(year=year, day=28)

    def step(self, t, inputs):
        x = float(np.datetime64(pd.Timestamp(self._map_time(t)).to_pydatetime(), "ns").astype(np.int64))
        ts = self._t
        out = {}
        if self.method == "linear":
            for c, v in self._v.items():
                out[c] = float(np.interp(x, ts, v))
            return out
        i = int(np.searchsorted(ts, x, side="right")) - 1
        if self.method == "nearest" and i + 1 < len(ts) and (i < 0 or ts[i + 1] - x < x - ts[i]):
            i += 1
        i = min(max(i, 0), len(ts) - 1)
        for c, v in self._v.items():
            out[c] = float(v[i])
        return out
