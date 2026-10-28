"""Build household at-home profiles from 2017 NHTS travel diaries.

Input: the person-level travel-diary tables produced by
``travel_diary_from_nhts.R`` (one row per person with ``STRTTIME_TRIPn`` and
``ENDTIME_TRIPn`` columns plus household attributes). Py-CoSim 0.x shipped two
of them: ``data/NHTS_EV.csv`` (households owning a hybrid/electric/alternative
fuel vehicle, NHTS ``FUELTYPE = 3``) and ``data/Non-EV dataset.csv`` (all
other households). Both are available in the ``v0.1-legacy`` tag of the
repository.

Method (per person, 15-min resolution, travel day 00:00-24:00):

* a person is at home before the first trip starts and after the last trip
  ends, and away in between (trip purposes are not part of the extract, so
  intermediate returns home cannot be identified -- a conservative choice);
* household members without any trip in the diary stayed at home all day
  (NHTS only lists persons who travelled in the trip file);
* the household profile is the fraction of members at home.

A commute-time proxy is the duration of the first trip of the day of the
member who leaves home earliest. This corrects Py-CoSim 0.x, which counted a
person as "at home" whenever they were not travelling.

Usage::

    python tools/nhts/build_household_profiles.py NHTS_EV.csv "Non-EV dataset.csv" \
        --out src/pycosim/data/nhts_household_profiles.csv.gz --max-non-ev 8000
"""
from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd

SLOTS = 96  # 15-minute slots per day
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


def _minutes(value) -> float:
    if not isinstance(value, str):
        return np.nan
    m = _TIME.match(value.strip())
    if not m:
        return np.nan
    return int(m.group(1)) * 60 + int(m.group(2))


def person_presence(starts, ends) -> tuple[np.ndarray, float, float, float]:
    """At-home indicator per 15-min slot, first departure, last return, first-trip duration."""
    s = [x for x in (_minutes(v) for v in starts) if not np.isnan(x)]
    e = [x for x in (_minutes(v) for v in ends) if not np.isnan(x)]
    home = np.ones(SLOTS)
    if not s or not e:
        return home, np.nan, np.nan, np.nan
    first_dep, last_ret = s[0], e[-1]
    first_trip = (e[0] - s[0]) % 1440
    minutes = np.zeros(1440, dtype=bool)  # True = away
    if last_ret >= first_dep:
        minutes[int(first_dep):int(last_ret)] = True
    else:  # diary crosses midnight
        minutes[int(first_dep):] = True
        minutes[:int(last_ret)] = True
    away_frac = minutes.reshape(SLOTS, 15).mean(axis=1)
    return 1.0 - away_frac, first_dep, last_ret, first_trip


def build(df: pd.DataFrame, group: str) -> pd.DataFrame:
    start_cols = [c for c in df.columns if c.startswith("STRTTIME_TRIP")]
    end_cols = [c for c in df.columns if c.startswith("ENDTIME_TRIP")]
    key = lambda c: int(c.rsplit("TRIP", 1)[1])  # noqa: E731
    start_cols.sort(key=key)
    end_cols.sort(key=key)
    rows = []
    for hid, hh in df.groupby("HOUSEID", sort=False):
        size = int(hh["HHSIZE"].iloc[0])
        if size < 1:
            continue
        total = np.zeros(SLOTS)
        deps, firsts, rets = [], [], []
        for _, person in hh.iterrows():
            pres, dep, ret, first = person_presence(person[start_cols].tolist(), person[end_cols].tolist())
            total += pres
            if not np.isnan(dep):
                deps.append(dep)
                firsts.append(first)
                rets.append(ret)
        n_diary = len(hh)
        total += max(size - n_diary, 0)  # members without trips stayed home
        frac = np.clip(total / max(size, n_diary), 0, 1)
        if deps:
            i = int(np.argmin(deps))
            commute, first_dep, last_ret = firsts[i], min(deps), max(rets)
        else:
            commute = first_dep = last_ret = np.nan
        rows.append({
            "houseid": int(hid), "group": group, "hhsize": size, "n_travelers": n_diary,
            "income_cat": int(hh["HHFAMINC"].iloc[0]), "vehicles": int(hh["HHVEHCNT"].iloc[0]),
            "urban": str(hh["HBHUR"].iloc[0]), "state": str(hh["HHSTATE"].iloc[0]),
            "commute_min": commute, "first_departure_min": first_dep, "last_return_min": last_ret,
            **{f"p{j:02d}": round(float(frac[j]), 3) for j in range(SLOTS)},
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ev_csv")
    ap.add_argument("non_ev_csv")
    ap.add_argument("--out", default="nhts_household_profiles.csv.gz")
    ap.add_argument("--max-non-ev", type=int, default=8000, help="random sample size of non-EV households")
    ap.add_argument("--seed", type=int, default=2017)
    args = ap.parse_args()
    ev = pd.read_csv(args.ev_csv, low_memory=False)
    non = pd.read_csv(args.non_ev_csv, low_memory=False)
    rng = np.random.default_rng(args.seed)
    ids = non["HOUSEID"].unique()
    if len(ids) > args.max_non_ev:
        ids = rng.choice(ids, args.max_non_ev, replace=False)
        non = non[non["HOUSEID"].isin(set(ids))]
    out = pd.concat([build(ev, "ev"), build(non, "non_ev")], ignore_index=True)
    out.to_csv(args.out, index=False, compression="gzip")
    print(f"wrote {len(out)} households to {args.out}")
    print(out.groupby("group")[["hhsize", "commute_min"]].describe().T)


if __name__ == "__main__":
    main()
