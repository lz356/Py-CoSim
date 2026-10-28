# NHTS travel-diary processing

`src/pycosim/data/nhts_household_profiles.csv.gz` (used by `pycosim.components.HouseholdOccupancy`) was produced from
the 2017 National Household Travel Survey (U.S. Federal Highway Administration) in two steps:

1. `travel_diary_from_nhts.R` downloads the public NHTS 2017 files with the `summarizeNHTS` R package and writes one
   travel-diary row per person (trip start/end times, vehicle ids, household attributes) for households with and
   without a hybrid/electric/alternative-fuel vehicle (`FUELTYPE = 3`).
2. `build_household_profiles.py` turns those diaries into 15-minute at-home fractions per household (method and
   assumptions in the script's docstring). It sampled 8,000 of the 106,949 non-EV households and kept all 5,548 EV
   households.

The intermediate CSV files used for the packaged data are preserved in the `v0.1-legacy` tag of this repository
(`data/NHTS_EV.csv`, `data/Non-EV dataset.csv`):

```bash
git show v0.1-legacy:data/NHTS_EV.csv > NHTS_EV.csv
git show "v0.1-legacy:data/Non-EV dataset.csv" > non_ev.csv
python tools/nhts/build_household_profiles.py NHTS_EV.csv non_ev.csv --out src/pycosim/data/nhts_household_profiles.csv.gz
```

Limitations: NHTS records one travel day per household; trip purposes are not part of the extract, so a person is
assumed away between the first departure and the last return of the day; only households with at least one
travelling member appear in the trip file.
