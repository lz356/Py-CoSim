#!/usr/bin/env bash
# Reproduce every result of the SMMM 2.0 manuscript (about 1.5 h on a 4-core laptop).
# Timing experiments (e4, e6, scalability) should run on an otherwise idle machine.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-../../.venv/Scripts/python}
mkdir -p results

run() { echo "== $1"; "$PY" "$@" > "results/log_${1%.py}.txt" 2>&1; }

run verification.py              # V1-V3: Py-CoSim vs standalone EnergyPlus
run e2a_alignment_synthetic.py   # alignment across step sizes, analytic reference
run e2c_timestep_cli.py          # EnergyPlus time-step and sizing-window check (command line)
run e2b_coupling_energyplus.py   # coupling error vs 1-min reference
"$PY" e2b_plot.py
run e3_replay.py                 # record/replay and counterfactual retrofit estimates
run e4_async.py                  # blocking vs asynchronous slow AI components (timing)
run e6_overhead_smmm.py          # cost of SMMM features (timing)
run scalability.py               # multi-building speed-up and framework overhead (timing)
run case_a_smmm.py               # community case study, naive vs aligned metering
run helics_comparison.py         # head-to-head comparison with HELICS
"$PY" diagrams_smmm.py           # Fig. 1 and Fig. 2
echo "done"
