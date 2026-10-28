"""Occupancy from travel behaviour: household at-home profiles from the 2017 NHTS.

The packaged data set ``nhts_household_profiles.csv.gz`` holds, for 13,548
U.S. households of the 2017 National Household Travel Survey, the fraction of
household members at home in each 15-minute slot of the survey day, the
household size, a commute-time proxy and other attributes (see
``tools/nhts/build_household_profiles.py`` for the method). Two groups are
included: households owning a hybrid/electric/alternative-fuel vehicle
(``group="ev"``) and a random sample of all other households (``"non_ev"``).

:class:`HouseholdOccupancy` assigns sampled households to dwelling units and
publishes the number of people at home in each unit -- ready to drive the
``People`` actuators of an EnergyPlus model.
"""
from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..core.component import Component

__all__ = ["load_household_profiles", "select_households", "HouseholdOccupancy", "OccupancyMLP", "PROFILE_COLUMNS"]

PROFILE_COLUMNS = [f"p{j:02d}" for j in range(96)]
LONG_COMMUTE_MIN = 30.0   # upper quartile of the commute proxy
SHORT_COMMUTE_MAX = 10.0  # lower quartile


@lru_cache(maxsize=1)
def _load() -> pd.DataFrame:
    with resources.files("pycosim.data").joinpath("nhts_household_profiles.csv.gz").open("rb") as fh:
        return pd.read_csv(fh, compression="gzip")


def load_household_profiles() -> pd.DataFrame:
    """The packaged NHTS household profile table (a copy)."""
    return _load().copy()


def select_households(group: Optional[str] = None, commute: Optional[str] = None,
                      hhsize: Optional[Sequence[int]] = None, urban: Optional[Sequence[str]] = None,
                      data: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Filter households.

    Parameters
    ----------
    group : {"ev", "non_ev"}, optional
    commute : {"long", "short"}, optional
        ``long``: commute proxy >= 30 min; ``short``: <= 10 min.
    hhsize : iterable of int, optional
    urban : iterable of NHTS ``HBHUR`` codes (``"U"`` urban, ``"S"`` suburban,
        ``"C"`` second city, ``"T"`` small town, ``"R"`` rural), optional
    """
    df = load_household_profiles() if data is None else data
    if group is not None:
        df = df[df["group"] == group]
    if commute == "long":
        df = df[df["commute_min"] >= LONG_COMMUTE_MIN]
    elif commute == "short":
        df = df[df["commute_min"] <= SHORT_COMMUTE_MAX]
    elif commute is not None:
        raise ValueError("commute must be 'long', 'short' or None")
    if hhsize is not None:
        df = df[df["hhsize"].isin(list(hhsize))]
    if urban is not None:
        df = df[df["urban"].isin(list(urban))]
    if df.empty:
        raise ValueError("No household matches the selection")
    return df.reset_index(drop=True)


class HouseholdOccupancy(Component):
    """Number of people at home in ``n_units`` dwellings, driven by NHTS diaries.

    Each unit is assigned a household (size and attributes) sampled from the
    selection. With ``resample="daily"`` the unit draws a new survey day each
    midnight from households of the same size and selection, which reproduces
    day-to-day variability; ``resample=None`` repeats one day.

    Outputs: ``{prefix}_{i}`` (people at home in unit *i*, 0-based, zero
    padded), ``total`` and ``fraction`` (community-wide fraction at home).
    """

    def __init__(self, name: str, n_units: int, *, group: Optional[str] = None, commute: Optional[str] = None,
                 hhsize: Optional[Sequence[int]] = None, urban: Optional[Sequence[str]] = None,
                 households: Optional[pd.DataFrame] = None, resample: Optional[str] = "daily",
                 prefix: str = "occ"):
        super().__init__(name)
        if n_units < 1:
            raise ValueError("n_units must be >= 1")
        self.n_units = int(n_units)
        self.pool = households if households is not None else select_households(group, commute, hhsize, urban)
        if resample not in ("daily", None):
            raise ValueError("resample must be 'daily' or None")
        self.resample = resample
        width = max(2, len(str(n_units - 1)))
        self.unit_ports: List[str] = [f"{prefix}_{i:0{width}d}" for i in range(n_units)]
        for port in self.unit_ports:
            self.add_output(port, initial=0.0, unit="people")
        self.add_output("total", initial=0.0, unit="people")
        self.add_output("fraction", initial=0.0)
        self._by_size: Dict[int, np.ndarray] = {}
        self.assignment: Optional[pd.DataFrame] = None

    def setup(self, ctx) -> None:
        pool = self.pool
        profiles = pool[PROFILE_COLUMNS].to_numpy(dtype=float)
        sizes = pool["hhsize"].to_numpy(dtype=int)
        for size in np.unique(sizes):
            self._by_size[int(size)] = profiles[sizes == size]
        idx = ctx.rng.choice(len(pool), size=self.n_units, replace=len(pool) < self.n_units)
        self.assignment = pool.iloc[idx][["houseid", "group", "hhsize", "commute_min", "urban", "income_cat"]].reset_index(drop=True)
        self.sizes = sizes[idx].astype(float)
        self.today = profiles[idx].copy()
        self._day = ctx.start.date()

    def _new_day(self) -> None:
        rng = self.ctx.rng
        for i, size in enumerate(self.sizes.astype(int)):
            candidates = self._by_size[int(size)]
            self.today[i] = candidates[rng.integers(len(candidates))]

    def step(self, t, inputs):
        if self.resample == "daily" and t.date() != self._day:
            self._day = t.date()
            self._new_day()
        slot = (t.hour * 60 + t.minute) // 15
        people = self.today[:, slot] * self.sizes
        out = dict(zip(self.unit_ports, people.tolist()))
        total = float(people.sum())
        out["total"] = total
        out["fraction"] = total / float(self.sizes.sum())
        return out

    def run_metadata(self):
        if self.assignment is None:
            return {}
        return {"n_units": self.n_units, "mean_hhsize": float(self.sizes.mean()),
                "mean_commute_min": float(self.assignment["commute_min"].mean())}


class OccupancyMLP:
    """Data-driven occupancy model (multi-layer perceptron), as in Py-CoSim 0.x.

    Learns the expected fraction of household members at home from time of
    day (sin/cos encoding) and household size, separately for a household
    selection. Requires scikit-learn.

    >>> model = OccupancyMLP().fit(select_households(commute="long"))
    >>> model.predict(hour=8.5, hhsize=3)
    """

    def __init__(self, hidden_layer_sizes=(32, 32), random_state: int = 0, max_iter: int = 300):
        self.hidden_layer_sizes = hidden_layer_sizes
        self.random_state = random_state
        self.max_iter = max_iter
        self.model = None

    @staticmethod
    def _features(hours: np.ndarray, hhsize: np.ndarray) -> np.ndarray:
        ang = 2 * np.pi * hours / 24.0
        return np.column_stack([np.sin(ang), np.cos(ang), np.sin(2 * ang), np.cos(2 * ang), hhsize / 4.0])

    def fit(self, households: pd.DataFrame) -> "OccupancyMLP":
        from sklearn.neural_network import MLPRegressor

        prof = households[PROFILE_COLUMNS].to_numpy(dtype=float)
        sizes = households["hhsize"].to_numpy(dtype=float)
        hours = (np.arange(96) * 0.25 + 0.125)
        X = self._features(np.tile(hours, len(households)), np.repeat(sizes, 96))
        y = prof.reshape(-1)
        self.model = MLPRegressor(hidden_layer_sizes=self.hidden_layer_sizes, random_state=self.random_state,
                                  max_iter=self.max_iter, early_stopping=True)
        self.model.fit(X, y)
        return self

    def predict(self, hour, hhsize) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("call fit() first")
        hour = np.atleast_1d(np.asarray(hour, dtype=float))
        hhsize = np.broadcast_to(np.atleast_1d(np.asarray(hhsize, dtype=float)), hour.shape)
        return np.clip(self.model.predict(self._features(hour, hhsize)), 0.0, 1.0)
