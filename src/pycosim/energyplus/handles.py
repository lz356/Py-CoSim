"""Discover every sensor, meter, internal variable and actuator a model offers.

Finding exact EnergyPlus names is the most common stumbling block of Python
EMS work. :func:`list_available_handles` runs the model for one day in a
worker process, asks EnergyPlus for its API catalogue and returns a table
that can be filtered and pasted directly into :class:`EnergyPlusComponent`::

    >>> df = list_available_handles("small_office.idf", "tucson.epw")
    >>> df[df.kind == "actuator"].query("key.str.contains('CLGSETP')")

Command line: ``pycosim handles small_office.idf -w tucson.epw --kind actuator``.
"""
from __future__ import annotations

import csv
import io
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from .component import EnergyPlusRuntimeError, _ProcessEndpoint
from .discovery import find_energyplus
from .model import EnergyPlusModel

__all__ = ["list_available_handles", "parse_api_catalogue"]

_KINDS = {
    "actuator": "actuator",
    "internalvariable": "internal_variable",
    "outputmeter": "meter",
    "outputvariable": "variable",
    "pluginglobalvariable": "plugin_global",
    "plugintrendvariable": "plugin_trend",
}


def parse_api_catalogue(text: str) -> pd.DataFrame:
    """Parse ``list_available_api_data_csv`` output into a tidy table."""
    rows = []
    for rec in csv.reader(io.StringIO(text)):
        if not rec:
            continue
        kind = _KINDS.get(rec[0].strip().lower().replace(" ", ""))
        if kind is None:
            continue
        fields = [f.strip().rstrip(";").strip() for f in rec[1:]]
        fields = [f for f in fields if f != ""] or [""]
        unit = ""
        if fields[-1].startswith("[") and fields[-1].endswith("]"):
            unit = fields[-1].strip("[] ")
            fields = fields[:-1]
        if kind == "actuator":
            ctype, control, key = (fields + ["", "", ""])[:3]
            rows.append({"kind": kind, "name": ctype, "control": control, "key": key, "unit": unit,
                         "spec": repr((ctype, control, key))})
        elif kind == "meter":
            name = fields[0] if fields else ""
            rows.append({"kind": kind, "name": name, "control": "", "key": "", "unit": unit, "spec": repr(name)})
        else:
            name, key = (fields + ["", ""])[:2]
            rows.append({"kind": kind, "name": name, "control": "", "key": key, "unit": unit,
                         "spec": repr((name, key))})
    return pd.DataFrame(rows, columns=["kind", "name", "control", "key", "unit", "spec"])


def list_available_handles(model: Union[str, Path, EnergyPlusModel], weather: Union[str, Path],
                           energyplus_dir: Optional[Union[str, Path]] = None, timeout: float = 300.0) -> pd.DataFrame:
    """Run ``model`` briefly and return all API handles it exposes."""
    install = find_energyplus(energyplus_dir)
    m = model.copy() if isinstance(model, EnergyPlusModel) else EnergyPlusModel.load(model, install)
    first, _last, _year = m.get_run_period()
    m.set_run_period(first, first)
    work = Path(tempfile.mkdtemp(prefix="pycosim_handles_"))
    path = m.save(work / "in.epJSON")
    (work / "eplus").mkdir(exist_ok=True)
    cfg = {
        "eplus_root": str(install.root),
        "args": ["-w", str(Path(weather).resolve()), "-d", str(work / "eplus"), str(path)],
        "mode": "list_handles",
    }
    ep = _ProcessEndpoint(cfg, work / "worker.log", startup_timeout=120.0)
    try:
        while True:
            msg = ep.recv(timeout)
            if msg[0] == "handles":
                return parse_api_catalogue(msg[1])
            if msg[0] in ("done", "error"):
                raise EnergyPlusRuntimeError(f"EnergyPlus stopped before listing handles: {msg}\n{ep.log_tail()}")
    finally:
        ep.kill()
        import shutil

        shutil.rmtree(work, ignore_errors=True)
