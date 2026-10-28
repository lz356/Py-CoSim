"""Shared helpers for the paper experiments (paths, EnergyPlus SQL reader, plot style)."""
from __future__ import annotations

import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
sys.path.insert(0, str(EXAMPLES))
OUT = ROOT / "paper" / "experiments" / "results"
FIG = ROOT / "paper" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

from _common import OFFICE_ZONES, SMALL_OFFICE, TUCSON_EPW, make_office  # noqa: E402,F401

# Colour-blind safe palette (Okabe-Ito)
COLORS = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]


def set_style():
    import matplotlib as mpl

    mpl.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "DejaVu Serif"],
        "font.size": 9,
        "axes.titlesize": 9,
        "axes.labelsize": 9,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "savefig.bbox": "tight",
        "savefig.dpi": 600,
        "axes.prop_cycle": mpl.cycler(color=COLORS),
    })


# Manuscript figures are drawn at their printed size (text width 6.5 in) so that
# every label prints at 8-9 pt; the PNG is exactly figsize x 600 dpi.
TEXT_WIDTH_IN = 6.5
MIN_FONT_PT = 8.0


def paper_style():
    """``set_style()`` for the manuscript figures: final print size, text >= 8 pt, STIX maths."""
    import matplotlib as mpl

    set_style()
    mpl.rcParams.update({
        "mathtext.fontset": "stix",      # Times-like symbols to match the serif text
        "savefig.bbox": "standard",      # no tight cropping: the PNG keeps the drawn size
        "savefig.pad_inches": 0.0,
        "savefig.dpi": 600,
        "legend.handlelength": 1.6,
        "boxplot.medianprops.color": "k",
    })


def save_paper_figure(fig, name: str) -> None:
    """Save a manuscript figure at 600 dpi and report its size, smallest font and any text problems."""
    from matplotlib.text import Text
    from matplotlib.transforms import Bbox

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    fig_box = fig.bbox
    hidden = set()  # labels of ticks outside the axis limits (kept by matplotlib but not drawn)
    for a in fig.axes:
        for axis in (a.xaxis, a.yaxis):
            lo, hi = sorted(axis.get_view_interval())
            tol = 1e-9 * max(abs(hi - lo), 1.0)
            for tick in axis.get_major_ticks() + axis.get_minor_ticks():
                loc = tick.get_loc()
                if loc is None or not lo - tol <= loc <= hi + tol:
                    hidden.update({id(tick.label1), id(tick.label2)})
    texts = [t for t in fig.findobj(Text) if t.get_visible() and t.get_text().strip() and id(t) not in hidden]
    sizes = [t.get_fontsize() for t in texts]
    w, h = fig.get_size_inches()
    problems = []
    if w > TEXT_WIDTH_IN + 1e-6:
        problems.append(f"width {w:.2f} in > {TEXT_WIDTH_IN} in")
    for t in texts:
        if t.get_fontsize() < MIN_FONT_PT - 1e-6:
            problems.append(f"{t.get_fontsize():.1f} pt: {t.get_text()!r}")
        bb = t.get_window_extent(renderer)
        if bb.x0 < fig_box.x0 - 1 or bb.y0 < fig_box.y0 - 1 or bb.x1 > fig_box.x1 + 1 or bb.y1 > fig_box.y1 + 1:
            problems.append(f"clipped at figure edge: {t.get_text()!r}")
    boxes = [(t, t.get_window_extent(renderer)) for t in texts]
    for i, (t1, b1) in enumerate(boxes):
        for t2, b2 in boxes[i + 1:]:
            inter = Bbox.intersection(b1, b2)
            if inter is not None and inter.width > 1 and inter.height > 1:
                problems.append(f"text overlap: {t1.get_text()!r} / {t2.get_text()!r}")
    fig.savefig(FIG / name, dpi=600)
    print(f"{name}: {w:.2f} x {h:.2f} in, smallest font {min(sizes):.1f} pt"
          + ("".join(f"\n  ! {p}" for p in problems) if problems else ", no text problems"))


def read_sql_series(sql_path: Path, year: int, frequency: str = "Zone Timestep") -> pd.DataFrame:
    """All time-step series of an EnergyPlus SQL file as a DataFrame (index = end of interval)."""
    con = sqlite3.connect(str(sql_path))
    q = """
        SELECT t.Month, t.Day, t.Hour, t.Minute, d.Name, d.KeyValue, r.Value
        FROM ReportData r
        JOIN ReportDataDictionary d ON r.ReportDataDictionaryIndex = d.ReportDataDictionaryIndex
        JOIN Time t ON r.TimeIndex = t.TimeIndex
        WHERE d.ReportingFrequency = ? AND (t.WarmupFlag IS NULL OR t.WarmupFlag = 0)
    """
    rows = con.execute(q, (frequency,)).fetchall()
    con.close()
    recs = []
    for month, day, hour, minute, name, key, value in rows:
        # EnergyPlus stamps the END of each interval with Hour in 0..24 (e.g. 00:15, ..., 01:00, ..., 24:00)
        stamp = datetime(year, month, day) + timedelta(hours=hour, minutes=minute)
        col = f"{name}:{key}" if key else name
        recs.append((stamp, col, value))
    df = pd.DataFrame(recs, columns=["time", "series", "value"])
    return df.pivot_table(index="time", columns="series", values="value", aggfunc="first").sort_index()


def run_energyplus_cli(epjson: Path, out_dir: Path) -> float:
    """Run EnergyPlus from the command line; returns wall time in seconds."""
    import time

    from pycosim.energyplus.discovery import find_energyplus

    inst = find_energyplus()
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    proc = subprocess.run([str(inst.executable), "-w", str(TUCSON_EPW), "-d", str(out_dir), str(epjson)],
                          capture_output=True, text=True)
    wall = time.perf_counter() - t0
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout[-2000:] + proc.stderr[-2000:])
    return wall


def machine_info() -> dict:
    import os

    cpu, cores, mem = platform.processor(), None, None
    try:
        cmd = ("(Get-CimInstance Win32_Processor).Name; (Get-CimInstance Win32_Processor).NumberOfCores; "
               "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)")
        lines = [l.strip() for l in subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                                                   text=True, timeout=60).stdout.splitlines() if l.strip()]
        cpu, cores, mem = lines[0], int(lines[1]), int(lines[2])
    except Exception:
        pass
    return {"cpu": cpu, "physical_cores": cores, "logical_cores": os.cpu_count(), "memory_gb": mem,
            "os": platform.platform(), "python": sys.version.split()[0]}


def load_example(folder: str):
    """Import ``examples/<folder>/run.py`` as a module (the examples double as case-study code)."""
    import importlib.util

    path = EXAMPLES / folder / "run.py"
    spec = importlib.util.spec_from_file_location(f"example_{folder}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fix_sizing_window(model, timesteps_per_hour: int, minutes: float = 90.0) -> None:
    """Keep EnergyPlus's sizing averaging window at ~``minutes`` whatever the time step.

    ``Sizing:Parameters`` counts the window in time steps (6 in the DOE small
    office = 90 min at 4 steps per hour). With 60 steps per hour the same
    setting is a 6-min window, which catches the morning cool-down spike and
    autosizes much larger coils and fans. Fixing the window in minutes keeps
    the equipment comparable across time steps.
    """
    n = max(1, int(round(minutes / (60.0 / timesteps_per_hour))))
    for obj in model.objects("Sizing:Parameters").values():
        obj["timesteps_in_averaging_window"] = n
