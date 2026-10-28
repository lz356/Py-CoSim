"""Small helpers to parse durations and timestamps given in friendly formats."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Union

DurationLike = Union[int, float, str, timedelta]
DatetimeLike = Union[str, date, datetime]

_UNITS = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "t": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
    "w": 604800.0,
    "week": 604800.0,
    "weeks": 604800.0,
}
_DURATION_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]+)?\s*$")


def parse_duration(value: DurationLike) -> float:
    """Convert a duration to seconds.

    Accepts numbers (already in seconds), :class:`datetime.timedelta`,
    pandas ``Timedelta`` and strings such as ``"15min"``, ``"1h"``,
    ``"900s"``, ``"1 day"`` or ISO-8601 ``"PT15M"``.
    """
    if value is None:
        raise ValueError("duration must not be None")
    if isinstance(value, bool):
        raise TypeError("duration must be a number, string or timedelta")
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, timedelta):
        seconds = value.total_seconds()
    elif hasattr(value, "total_seconds"):  # pandas.Timedelta, numpy timedelta wrapper
        seconds = float(value.total_seconds())
    elif isinstance(value, str):
        text = value.strip()
        if text.upper().startswith("P"):
            import pandas as pd

            seconds = pd.Timedelta(text).total_seconds()
        else:
            match = _DURATION_RE.match(text)
            if not match:
                raise ValueError(f"Cannot parse duration {value!r}; use e.g. '15min', '1h' or 900")
            number, unit = match.groups()
            factor = _UNITS.get((unit or "s").lower())
            if factor is None:
                raise ValueError(f"Unknown time unit {unit!r} in duration {value!r}")
            seconds = float(number) * factor
    else:
        raise TypeError(f"Unsupported duration type: {type(value).__name__}")
    if seconds <= 0:
        raise ValueError(f"duration must be positive, got {value!r}")
    return seconds


def parse_datetime(value: DatetimeLike) -> datetime:
    """Convert strings, dates or pandas timestamps to a naive :class:`datetime`."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().replace(tzinfo=None)
    if isinstance(value, str):
        import pandas as pd

        return pd.Timestamp(value).to_pydatetime().replace(tzinfo=None)
    raise TypeError(f"Unsupported datetime type: {type(value).__name__}")


def format_duration(seconds: float) -> str:
    """Human-readable duration (``900`` -> ``'15min'``)."""
    seconds = float(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    if seconds.is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:g}s"
