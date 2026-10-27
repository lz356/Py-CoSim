#!/usr/bin/env bash
# Re-run every experiment of the paper in sequence (they must not overlap, timings are measured).
set -e
PY=${PY:-../../.venv/Scripts/python}
cd "$(dirname "$0")"
$PY -u verification.py   > results/log_verification.txt 2>&1
$PY -u scalability.py 1 2 4 8 16 > results/log_scalability.txt 2>&1
$PY -u overhead_breakdown.py > results/log_overhead.txt 2>&1
$PY -u case_a_community.py > results/log_case_a.txt 2>&1
$PY -u case_b_llm.py     > results/log_case_b.txt 2>&1
$PY -u case_c_travel.py  > results/log_case_c.txt 2>&1
$PY -u diagrams.py       > results/log_diagrams.txt 2>&1
echo ALL DONE
