"""Tests of the duration / timestamp helpers (pycosim.core.timeutils)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from pycosim.core.timeutils import format_duration, parse_datetime, parse_duration


@pytest.mark.parametrize(
    "value, seconds",
    [
        (900, 900.0),
        (0.5, 0.5),
        ("900", 900.0),
        ("900s", 900.0),
        ("15min", 900.0),
        ("15 minutes", 900.0),
        ("15T", 900.0),
        ("1h", 3600.0),
        ("1.5 hours", 5400.0),
        ("1 day", 86400.0),
        ("2w", 1209600.0),
        ("PT15M", 900.0),
        ("P1D", 86400.0),
        (timedelta(minutes=5), 300.0),
        (pd.Timedelta("10min"), 600.0),
    ],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == pytest.approx(seconds)


@pytest.mark.parametrize("value, exc", [
    (None, ValueError),
    (True, TypeError),
    (0, ValueError),
    (-5, ValueError),
    ("-1h", ValueError),
    ("abc", ValueError),
    ("5 fortnights", ValueError),
    ([1], TypeError),
])
def test_parse_duration_errors(value, exc):
    with pytest.raises(exc):
        parse_duration(value)


def test_parse_datetime():
    assert parse_datetime("2023-07-01") == datetime(2023, 7, 1)
    assert parse_datetime("2023-07-01 06:30") == datetime(2023, 7, 1, 6, 30)
    assert parse_datetime(date(2023, 7, 1)) == datetime(2023, 7, 1)
    aware = datetime(2023, 7, 1, 12, tzinfo=timezone.utc)
    assert parse_datetime(aware) == datetime(2023, 7, 1, 12)
    assert parse_datetime(pd.Timestamp("2023-07-01 01:00")) == datetime(2023, 7, 1, 1)
    with pytest.raises(TypeError):
        parse_datetime(12345)


@pytest.mark.parametrize(
    "seconds, text",
    [(900, "15min"), (3600, "1h"), (5400, "90min"), (86400, "1d"), (7200, "2h"), (45, "45s"), (1.5, "1.5s")],
)
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text
