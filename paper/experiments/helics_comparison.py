"""Py-CoSim vs HELICS on the same EnergyPlus co-simulation.

Scenario (identical in both tools): DOE small office in EnergyPlus 22.2 (4 time steps per
hour), Tucson TMY3, 2023-07-17 -> 2023-07-24, 15-min exchange. A controller pre-cools on
weekdays (22.0 C 13-16 h, 26.7 C 16-21 h, 24.0 C otherwise) under a time-of-use tariff
(0.30 $/kWh on weekdays 16-21 h, 0.10 $/kWh otherwise); a meter integrates the facility
electricity and its cost.

Variants

* ``pycosim_naive``     GridMeter(aligned=False): latest power x latest price;
* ``pycosim_aligned``   GridMeter(aligned=True): power and price averaged over the step
                        that just ended (TIDE aligns them from their time meaning);
* ``helics``            three value federates + broker, period 900 s, consumers with
                        ``wait_for_current_time_update`` so a value published at t is read
                        at t (Py-CoSim's within-step order); meter = latest x latest;
* ``helics_default``    the same federates with HELICS' default timing (no flag);
* ``helics_handaligned`` HELICS with the interval alignment hand-coded in the meter;
* ``reference_1min``    Py-CoSim, EnergyPlus at 60 steps/h, 1-min exchange, aligned meter;
* ``pycosim_*_ep1min``  both Py-CoSim meters on 15-min exchange over the reference's 1-min
                        EnergyPlus physics (EnergyPlus internal sub-steps), which separates the
                        exchange-semantics error from EnergyPlus' own time-step sensitivity.

Every variant's model gets ``common.fix_sizing_window`` (HVAC autosizing averaged over 90 min
whatever the EnergyPlus time step); without it the 60-steps/h models autosize much larger
coils and fans and the time-step "error" is ~12 % instead of < 1 %.

Every run is a fresh process (or set of processes); the wall time is measured from launch
to exit of all processes. Outputs: results/helics_comparison.csv/.json (+ the log).

usage: python helics_comparison.py            (everything)
       python helics_comparison.py --pycosim NAME ALIGNED STEP TIMESTEP   (one Py-CoSim run)
"""
from __future__ import annotations

import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import tokenize
from pathlib import Path

import pandas as pd

from common import OUT, ROOT, SMALL_OFFICE, TUCSON_EPW, fix_sizing_window, machine_info

HERE = Path(__file__).resolve().parent
FEDS = HERE / "helics_feds"
WORK = HERE / "pycosim_runs" / "helics_comparison"
PY = sys.executable


def _broker_binary() -> Path:
    """The real helics_broker executable: Scripts/helics_broker.exe is a Python shim that spawns it as a
    child, so killing the shim after a failure would leave the broker orphaned."""
    import helics

    exe = "helics_broker.exe" if os.name == "nt" else "helics_broker"
    real = Path(helics.__file__).parent / "install" / "bin" / exe
    return real if real.exists() else Path(PY).parent / exe
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
REPS = 3

# >>> user code: Py-CoSim scenario
import pycosim as pc  # noqa: E402
from pycosim.components import GridMeter, PrecoolingController, TimeOfUseTariff  # noqa: E402

START, END = "2023-07-17", "2023-07-24"


def pycosim_scenario(name, aligned, step="15min", timestep=4, preprocess=None):
    sim = pc.CoSimulation(START, END, step, name=name, output_dir=WORK, log_level="WARNING")
    sim.add(pc.EnergyPlusComponent("office", SMALL_OFFICE, TUCSON_EPW, timestep=timestep, step_size=step,
                                   daylight_saving=False, preprocess=preprocess,
                                   meters={"P_facility": ("Electricity:Facility", "W")},
                                   actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH")}))
    sim.add(PrecoolingController("ctrl", peak_hours=(16, 21), precool_hours=3, base_sp=24.0, precool_sp=22.0,
                                 peak_sp=26.7))
    sim.add(TimeOfUseTariff("tariff", peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.10))
    sim.add(GridMeter("meter", aligned=aligned))
    sim.connect("ctrl.clg_sp", "office.clg_sp")
    sim.connect("office.P_facility", "meter.P_load" if aligned else "meter.P_net")
    sim.connect("tariff.price", "meter.price")
    return sim.run()
# <<< user code


# ------------------------------------------------------------------ runners
class RunFailed(RuntimeError):
    pass


def _wait_all(procs: dict, timeout: float, logs: dict) -> None:
    """Wait for every process; on a failure or a timeout kill the remaining ones (only our own PIDs)."""
    deadline = time.perf_counter() + timeout
    try:
        while any(p.poll() is None for p in procs.values()):
            bad = {n: p.returncode for n, p in procs.items() if p.poll() not in (None, 0)}
            if bad:
                raise RunFailed(f"process(es) failed: {bad}")
            if time.perf_counter() > deadline:
                raise RunFailed(f"timeout after {timeout:.0f} s")
            time.sleep(0.02)
        bad = {n: p.returncode for n, p in procs.items() if p.returncode != 0}
        if bad:
            raise RunFailed(f"process(es) failed: {bad}")
    except RunFailed as exc:
        tails = []
        for n, path in logs.items():
            try:
                tails.append(f"--- {n} ---\n" + Path(path).read_text(errors="replace")[-1500:])
            except OSError:
                pass
        raise RunFailed(f"{exc}\n" + "\n".join(tails)) from None
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.kill()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    pass


def retried(fn, name: str, attempts: int = 3) -> dict:
    """Run ``fn(name)``; repeat after a failed run (this machine is short of memory and Windows occasionally
    fails to start a process, e.g. 'DLL load failed ... The handle is invalid'). Failures are logged."""
    for attempt in range(1, attempts + 1):
        try:
            return fn(name)
        except RunFailed as exc:
            first = str(exc).splitlines()
            detail = next((ln for ln in first if "Error" in ln), first[0] if first else "")
            print(f"   ! {name} attempt {attempt} failed: {first[0] if first else ''} | {detail}", flush=True)
            if attempt == attempts:
                raise
            time.sleep(5)
    raise AssertionError("unreachable")


def run_pycosim(name: str, aligned: bool, step: str = "15min", timestep: int = 4, timeout: float = 1800) -> dict:
    """One Py-CoSim run in a fresh interpreter (end-to-end wall time, like the HELICS processes)."""
    shutil.rmtree(WORK / name, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    log = WORK / f"{name}.log"
    with open(log, "w") as fh:
        t0 = time.perf_counter()
        proc = subprocess.Popen([PY, str(Path(__file__).resolve()), "--pycosim", name, str(int(aligned)), step,
                                 str(timestep)], cwd=HERE, stdout=fh, stderr=subprocess.STDOUT,
                                creationflags=NO_WINDOW)
        _wait_all({"pycosim": proc}, timeout, {"pycosim": log})
        wall = time.perf_counter() - t0
    df = pd.read_csv(WORK / name / "timeseries.csv", index_col=0, parse_dates=True)
    meta = json.loads((WORK / name / "meta.json").read_text())
    h = meta["step_s"]
    steps = pd.DataFrame({"t_s": (df.index - df.index[0]).total_seconds(), "P_W": df["office.P_facility"],
                          "clg_sp": df["ctrl.clg_sp"], "price_latest": df["tariff.price"]}).reset_index(drop=True)
    steps["cost_usd"] = df["meter.cost"].diff().fillna(df["meter.cost"].iloc[0]).to_numpy()
    out = {"wall_s": wall, "inner_wall_s": meta["wall_time_s"], "step_s": h,
           "energy_kWh": float(df["meter.E_import"].iloc[-1]), "cost_usd": float(df["meter.cost"].iloc[-1]),
           "steps": steps}
    align = WORK / name / "alignment.csv"
    if align.exists():
        out["alignment"] = pd.read_csv(align).to_dict(orient="records")
    return out


def prepare_model() -> Path:
    """The epJSON Py-CoSim's EnergyPlusComponent writes (same edits), for the HELICS building federate."""
    from pycosim.energyplus import EnergyPlusModel

    m = EnergyPlusModel.load(SMALL_OFFICE)
    m.set_timesteps_per_hour(4)
    m.set_run_period(pd.Timestamp(START).date(), (pd.Timestamp(END) - pd.Timedelta(days=1)).date())
    m.remove_daylight_saving()
    fix_sizing_window(m, 4)  # 90-min window, as for every Py-CoSim variant
    return m.save(WORK / "in.epJSON")


def run_helics(name: str, model: Path, meter: str = "meter_fed.py", same_time: bool = True,
               timeout: float = 900) -> dict:
    """Broker + building, controller and meter federates, each in its own process."""
    run = WORK / name
    shutil.rmtree(run, ignore_errors=True)
    run.mkdir(parents=True)
    cfg = {}
    for fed in ("building", "controller", "meter"):
        data = json.loads((FEDS / f"{fed}.json").read_text())
        if "wait_for_current_time_update" in data:
            data["wait_for_current_time_update"] = same_time
        cfg[fed] = run / f"{fed}.json"
        cfg[fed].write_text(json.dumps(data, indent=1))
    cmds = {
        "broker": [_broker_binary(), "-f3", "--coretype=zmq", "--loglevel=warning"],
        "building": [PY, FEDS / "building_fed.py", cfg["building"], model, TUCSON_EPW, run / "eplus",
                     run / "building_log.csv"],
        "controller": [PY, FEDS / "controller_fed.py", cfg["controller"]],
        "meter": [PY, FEDS / meter, cfg["meter"], run / "meter_result.json"],
    }
    procs, files, logs = {}, [], {}
    t0 = time.perf_counter()
    try:
        for n, cmd in cmds.items():
            logs[n] = run / f"{n}.log"
            files.append(open(logs[n], "w"))
            procs[n] = subprocess.Popen([str(c) for c in cmd], cwd=run, stdout=files[-1], stderr=subprocess.STDOUT,
                                        creationflags=NO_WINDOW)
        _wait_all(procs, timeout, logs)
        wall = time.perf_counter() - t0
    finally:
        for p in procs.values():  # never leave a broker/federate behind
            if p.poll() is None:
                p.kill()
        for fh in files:
            fh.close()
    res = json.loads((run / "meter_result.json").read_text())
    met = pd.DataFrame(res["steps"], columns=["t_s", "P_W", "price_used"])
    bld = pd.read_csv(run / "building_log.csv")
    steps = met.merge(bld[["t_s", "P_facility_W", "clg_sp_C"]], on="t_s", how="left")
    steps["cost_usd"] = steps["P_W"] * steps["price_used"] * 900.0 / 3.6e6
    inner = None
    for line in logs["building"].read_text(errors="replace").splitlines():
        if line.startswith("run_energyplus wall"):
            inner = float(line.split()[2])
    return {"wall_s": wall, "inner_wall_s": inner, "step_s": 900.0, "energy_kWh": res["energy_kWh"],
            "cost_usd": res["cost_usd"], "steps": steps}


# ------------------------------------------------------------- code counting
def count_python(text: str) -> int:
    """Non-blank lines holding code: comments and docstrings are not counted."""
    toks = [t for t in tokenize.generate_tokens(io.StringIO(text).readline)]
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
    lines = set()
    for i, tok in enumerate(toks):
        if tok.type in skip:
            continue
        if tok.type == tokenize.STRING:
            prev = next((t for t in reversed(toks[:i]) if t.type not in (tokenize.NL, tokenize.COMMENT)), None)
            nxt = next((t for t in toks[i + 1:] if t.type not in (tokenize.NL, tokenize.COMMENT)), None)
            if (prev is None or prev.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)) and (
                    nxt is None or nxt.type in (tokenize.NEWLINE, tokenize.ENDMARKER)):
                continue  # docstring
        lines.update(range(tok.start[0], tok.end[0] + 1))
    source = text.splitlines()
    return len([n for n in lines if "# harness" not in source[n - 1]])  # timing instrumentation is not counted


def count_json(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def lines_of_code() -> dict:
    src = Path(__file__).read_text()
    region = src.split("# >>> user code: Py-CoSim scenario", 1)[1].split("# <<< user code", 1)[0]
    py = count_python(region)
    feds = {f: count_python((FEDS / f).read_text()) for f in
            ("building_fed.py", "controller_fed.py", "meter_fed.py", "meter_fed_aligned.py")}
    cfgs = {f: count_json((FEDS / f).read_text()) for f in
            ("building.json", "controller.json", "meter.json", "runner.json")}
    cfg_total = sum(cfgs.values())
    helics_py = feds["building_fed.py"] + feds["controller_fed.py"] + feds["meter_fed.py"]
    helics_al = helics_py - feds["meter_fed.py"] + feds["meter_fed_aligned.py"]
    return {
        "per_file": {**feds, **cfgs, "helics_comparison.py (Py-CoSim region)": py},
        "pycosim": {"python": py, "config": 0},
        "helics": {"python": helics_py, "config": cfg_total},
        "helics_handaligned": {"python": helics_al, "config": cfg_total},
    }


# ------------------------------------------------------------------- analysis
def window_totals(steps: pd.DataFrame, step_s: float, end_s: float) -> tuple:
    """Energy and cost of the intervals [t - h, t) that lie inside the common window [0, end_s)."""
    sel = (steps["t_s"] - step_s >= -1e-9) & (steps["t_s"] <= end_s + 1e-9)
    s = steps[sel]
    return float((s["P_W"] * step_s / 3.6e6).sum()), float(s["cost_usd"].sum())


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    print("machine:", json.dumps(machine_info()), flush=True)
    import helics

    print("HELICS", helics.helicsGetVersion(), "| Py-CoSim", pc.__version__, flush=True)
    print("other python.exe processes on the machine at start:", _count_python() - 1, flush=True)
    model = prepare_model()
    variants = {
        "pycosim_naive": lambda n: run_pycosim(n, aligned=False),
        "pycosim_aligned": lambda n: run_pycosim(n, aligned=True),
        "helics": lambda n: run_helics(n, model),
        "helics_default": lambda n: run_helics(n, model, same_time=False),
        "helics_handaligned": lambda n: run_helics(n, model, meter="meter_fed_aligned.py"),
    }
    runs = {k: [] for k in variants}
    # 1-min EnergyPlus runs (90-min sizing window, see fix_sizing_window): the reference, and the two
    # Py-CoSim meters on 15-min exchange over the *same* 1-min physics (EnergyPlus internal sub-steps)
    slow = {
        "reference_1min": lambda n: run_pycosim(n, aligned=True, step="1min", timestep=60),
        "pycosim_naive_ep1min": lambda n: run_pycosim(n, aligned=False, step="15min", timestep=60),
        "pycosim_aligned_ep1min": lambda n: run_pycosim(n, aligned=True, step="15min", timestep=60),
    }
    for key, fn in slow.items():
        r = retried(fn, f"{key}_0")
        runs[key] = [r]
        print(f"rep 0 {key:22s} E={r['energy_kWh']:.4f} kWh  cost=${r['cost_usd']:.4f}  wall={r['wall_s']:.2f} s",
              flush=True)
    for rep in range(REPS):
        for key, fn in variants.items():
            r = retried(fn, f"{key}_{rep}")
            runs[key].append(r)
            print(f"rep {rep} {key:22s} E={r['energy_kWh']:.4f} kWh  cost=${r['cost_usd']:.4f}  "
                  f"wall={r['wall_s']:.2f} s", flush=True)
    for key, fn in slow.items():  # repeat only runs shorter than 30 s
        while runs[key][0]["wall_s"] < 30.0 and len(runs[key]) < REPS:
            runs[key].append(retried(fn, f"{key}_{len(runs[key])}"))
    sizes15, sizes1 = coil_sizes(WORK / "pycosim_aligned_0"), coil_sizes(WORK / "reference_1min_0")
    size_diff = {k: 100 * (sizes1[k] / sizes15[k] - 1) for k in sizes15}
    print("\nDX coil capacity, 1-min vs 15-min EnergyPlus [%]:", {k: round(v, 2) for k, v in size_diff.items()})

    # the Py-CoSim component writes the same model the HELICS building federate runs
    same_model = json.loads((WORK / "pycosim_naive_0" / "office" / "in.epJSON").read_text()) == json.loads(
        model.read_text())
    print("\nidentical EnergyPlus input for Py-CoSim and HELICS:", same_model)

    # determinism across repetitions
    for key, rs in runs.items():
        costs = {round(r["cost_usd"], 12) for r in rs}
        print(f"{key:20s} distinct costs over {len(rs)} reps: {len(costs)}")

    ref = runs["reference_1min"][0]
    end_common = 7 * 86400.0 - 900.0  # every meter covers [0, T - 15 min)
    ref_E_w, ref_C_w = window_totals(ref["steps"], 60.0, end_common)
    aligned = runs["pycosim_aligned"][0]
    loc = lines_of_code()
    ep15, ep1 = "EnergyPlus 4 steps/h", "EnergyPlus 60 steps/h"
    labels = {  # tool, meter, timing, EnergyPlus physics, exchange step
        "pycosim_naive": ("Py-CoSim", "GridMeter(aligned=False): latest P x latest price", "within-step order",
                          ep15, "15 min"),
        "pycosim_aligned": ("Py-CoSim", "GridMeter(aligned=True): P and price read 'past'", "within-step order",
                            ep15, "15 min"),
        "helics": ("HELICS 3.6.1", "latest P x latest price", "wait_for_current_time_update", ep15, "15 min"),
        "helics_default": ("HELICS 3.6.1", "latest P x latest price", "default (no flag)", ep15, "15 min"),
        "helics_handaligned": ("HELICS 3.6.1", "hand-coded: P x previous price", "wait_for_current_time_update",
                               ep15, "15 min"),
        "pycosim_naive_ep1min": ("Py-CoSim", "GridMeter(aligned=False): latest P x latest price",
                                 "within-step order", ep1, "15 min"),
        "pycosim_aligned_ep1min": ("Py-CoSim", "GridMeter(aligned=True): P and price read 'past'",
                                   "within-step order", ep1, "15 min"),
        "reference_1min": ("Py-CoSim", "GridMeter(aligned=True)", "within-step order", ep1, "1 min"),
    }
    loc_key = {"helics": "helics", "helics_default": "helics", "helics_handaligned": "helics_handaligned"}
    same_physics = {k: (runs["pycosim_aligned"][0] if labels[k][3] == ep15 else ref) for k in labels}
    rows = []
    for key in labels:
        rs = runs[key]
        r = rs[0]
        e_w, c_w = window_totals(r["steps"], r["step_s"], end_common)
        sp_E_w, sp_C_w = window_totals(same_physics[key]["steps"], same_physics[key]["step_s"], end_common)
        walls = [x["wall_s"] for x in rs]
        tool, meter, timing, physics, exchange = labels[key]
        lc = loc[loc_key.get(key, "pycosim")]
        rows.append({
            "variant": key, "tool": tool, "meter": meter, "timing": timing, "physics": physics,
            "exchange": exchange, "energy_kWh": r["energy_kWh"], "cost_usd": r["cost_usd"],
            "energy_err_pct": 100 * (r["energy_kWh"] / ref["energy_kWh"] - 1),
            "cost_err_pct": 100 * (r["cost_usd"] / ref["cost_usd"] - 1),
            "cost_diff_vs_aligned_pct": 100 * (r["cost_usd"] / aligned["cost_usd"] - 1),
            "exchange_err_energy_pct": 100 * (e_w / sp_E_w - 1),
            "exchange_err_cost_pct": 100 * (c_w / sp_C_w - 1),
            "energy_common_window_kWh": e_w, "cost_common_window_usd": c_w,
            "energy_common_err_pct": 100 * (e_w / ref_E_w - 1), "cost_common_err_pct": 100 * (c_w / ref_C_w - 1),
            "wall_s_median": statistics.median(walls), "wall_s_min": min(walls), "wall_s_max": max(walls),
            "core_wall_s_median": statistics.median([x["inner_wall_s"] for x in rs]),
            "reps": len(rs), "loc_python": lc["python"], "loc_config": lc["config"],
            "loc_total": lc["python"] + lc["config"],
        })
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "helics_comparison.csv", index=False, float_format="%.6g")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print("\n" + table.round(4).to_string(index=False))

    # step-by-step agreement of HELICS with the Py-CoSim variant using the same semantics
    def compare(a: pd.DataFrame, b: pd.DataFrame, cols) -> dict:
        m = a.merge(b, on="t_s", suffixes=("_a", "_b"))
        return {c: float((m[f"{c}_a"] - m[f"{c}_b"]).abs().max()) for c in cols} | {"steps": len(m)}

    pn, ph = runs["pycosim_naive"][0]["steps"], runs["helics"][0]["steps"]
    hd = runs["helics_default"][0]["steps"]
    agree = {
        "helics_vs_pycosim_naive": compare(
            pn.rename(columns={"price_latest": "price"})[["t_s", "P_W", "clg_sp", "price", "cost_usd"]],
            ph.rename(columns={"clg_sp_C": "clg_sp", "price_used": "price"})[["t_s", "P_W", "clg_sp", "price",
                                                                                "cost_usd"]],
            ["P_W", "clg_sp", "price", "cost_usd"]),
        "helics_handaligned_vs_pycosim_aligned_cost_abs_diff_usd":
            abs(runs["helics_handaligned"][0]["cost_usd"] - aligned["cost_usd"]),
        "helics_vs_pycosim_naive_cost_abs_diff_usd": abs(runs["helics"][0]["cost_usd"]
                                                         - runs["pycosim_naive"][0]["cost_usd"]),
        "helics_vs_pycosim_naive_energy_abs_diff_kWh": abs(runs["helics"][0]["energy_kWh"]
                                                           - runs["pycosim_naive"][0]["energy_kWh"]),
    }
    # what the default HELICS timing does: lag of the set-point and of the meter inputs
    lag = pn[["t_s", "clg_sp"]].merge(hd[["t_s", "clg_sp_C"]], on="t_s")
    agree["helics_default_setpoint_equals_pycosim_setpoint_one_step_earlier"] = bool(
        (lag["clg_sp_C"].iloc[1:].to_numpy() == lag["clg_sp"].iloc[:-1].to_numpy()).all())
    agree["helics_default_meter_power_equals_building_power_one_step_earlier"] = bool(
        (hd["P_W"].iloc[1:].to_numpy() == hd["P_facility_W"].iloc[:-1].to_numpy()).all())
    # boundary attribution: the naive meter charges [15:45, 16:00) at the peak price and [20:45, 21:00) off-peak
    b = pn.copy()
    b["price_true"] = b["price_latest"].shift(1)  # the price in effect during [t - h, t)
    mis = b.iloc[1:][b["price_latest"].iloc[1:] != b["price_true"].iloc[1:]]
    agree["naive_misattributed_steps"] = int(len(mis))
    agree["naive_misattributed_times"] = [str(pd.Timestamp(START) + pd.Timedelta(seconds=s)) for s in mis["t_s"]]
    agree["naive_misattribution_cost_usd"] = float(((mis["price_latest"] - mis["price_true"]) * mis["P_W"]
                                                    * 900.0 / 3.6e6).sum())
    # Py-CoSim's aligned meter has no price for the pre-start interval [-15 min, 0) and charges it at 0
    agree["first_interval_cost_naive_usd"] = float(b["cost_usd"].iloc[0])
    agree["naive_minus_aligned_cost_usd"] = runs["pycosim_naive"][0]["cost_usd"] - aligned["cost_usd"]
    # same 1-min physics: does the 15-min aligned meter reproduce the 1-min reference?
    a1 = runs["pycosim_aligned_ep1min"][0]
    e1, c1 = window_totals(a1["steps"], 900.0, end_common)
    agree["aligned_ep1min_minus_reference_common_window"] = {"energy_kWh": e1 - ref_E_w, "cost_usd": c1 - ref_C_w}
    print("\nagreement:", json.dumps(agree, indent=2))
    print("\nlines of code:", json.dumps(loc, indent=2))
    if "alignment" in aligned:
        print("\nPy-CoSim alignment report (aligned meter):", json.dumps(aligned["alignment"], indent=2))

    summary = {
        "scenario": {"model": str(SMALL_OFFICE.relative_to(ROOT)), "weather": str(TUCSON_EPW.relative_to(ROOT)),
                     "start": START, "end": END, "exchange_step_s": 900, "energyplus_timesteps_per_hour": 4,
                     "reference": ("Py-CoSim, EnergyPlus 60 steps/h, 1-min exchange, GridMeter(aligned=True); "
                                   "common.fix_sizing_window (90 min) applied to every variant's model, i.e. 6 "
                                   "steps at 4 steps/h and 90 steps at 60 steps/h, so HVAC autosizing is "
                                   "comparable across EnergyPlus time steps"),
                     "common_window": "[start, end - 15 min): intervals covered by every meter",
                     "exchange_err": ("error over the common window vs the aligned meter on the same EnergyPlus "
                                      "physics (4 steps/h: pycosim_aligned; 60 steps/h: reference_1min)")},
        "dx_coil_capacity_1min_vs_15min_pct": size_diff,
        "versions": {"helics": helics.helicsGetVersion(), "pycosim": pc.__version__, "energyplus": "22.2.0"},
        "machine": machine_info(),
        "identical_energyplus_input": same_model,
        "results": rows,
        "walls_s": {k: [x["wall_s"] for x in rs] for k, rs in runs.items()},
        "agreement": agree,
        "lines_of_code": loc,
        "loc_method": ("non-blank lines holding Python tokens (comments and docstrings excluded); JSON: non-blank "
                       "lines. Py-CoSim: imports + START/END + scenario function in helics_comparison.py. HELICS: "
                       "building_fed.py + controller_fed.py + meter_fed.py (or meter_fed_aligned.py), plus the "
                       "three federate JSON configs and runner.json. Measurement harness (timing, process "
                       "management, result collection) is excluded for both."),
        "wall_method": ("wall_s: launch of the process(es) to exit of all of them (fresh Python interpreter(s) "
                        "incl. imports; HELICS includes the broker); core_wall_s: Py-CoSim CoSimulation.run() "
                        "(meta wall_time_s, incl. EnergyPlus worker start) vs. the HELICS building federate's "
                        "run_energyplus() call (paces the federation); medians of the repetitions"),
        "core_walls_s": {k: [x["inner_wall_s"] for x in rs] for k, rs in runs.items()},
        "alignment_report_aligned_meter": aligned.get("alignment"),
    }
    (OUT / "helics_comparison.json").write_text(json.dumps(summary, indent=2, default=str))
    print("other python.exe processes on the machine at the end:", _count_python() - 1)
    print("\nwritten:", OUT / "helics_comparison.csv", OUT / "helics_comparison.json")


def coil_sizes(run: Path) -> dict:
    """Autosized DX cooling coil capacities [W] from eplusout.eio."""
    out = {}
    for line in (run / "office" / "eplus" / "eplusout.eio").read_text(errors="replace").splitlines():
        if "Component Sizing Information, Coil:Cooling:DX" in line and "gross_rated_total_cooling_capacity" in line:
            parts = [p.strip() for p in line.split(",")]
            out[parts[2]] = float(parts[-1])
    return out


def _count_python() -> int:
    """Number of python.exe processes running (other jobs on the machine disturb wall times)."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe", "/NH"], capture_output=True, text=True,
                             timeout=30).stdout
        return sum(1 for line in out.splitlines() if line.lower().startswith("python.exe"))
    except Exception:
        return -1


def _single_pycosim(argv) -> None:
    name, aligned, step, timestep = argv[0], bool(int(argv[1])), argv[2], int(argv[3])
    # every variant: EnergyPlus' sizing window held at 90 min whatever its time step (common.fix_sizing_window);
    # at 60 steps/h the model's 6-step window would autosize much larger coils and fans
    pycosim_scenario(name, aligned, step=step, timestep=timestep,
                     preprocess=lambda model: fix_sizing_window(model, timestep))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--pycosim":
        _single_pycosim(sys.argv[2:])
    else:
        main()
