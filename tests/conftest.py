"""Shared fixtures for the Py-CoSim test-suite.

* Matplotlib is forced onto the non-interactive Agg backend before anything
  imports pyplot (``LivePlot`` and ``Results.plot`` are exercised headless).
* Every simulation writes into pytest's ``tmp_path`` -- never into the
  repository's default ``pycosim_runs`` folder.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
EPW_PATH = EXAMPLES / "weather" / "USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"
IDF_PATH = EXAMPLES / "models" / "small_office.idf"


@pytest.fixture(scope="session")
def epw_path() -> Path:
    if not EPW_PATH.exists():  # pragma: no cover - repository layout guard
        pytest.skip(f"example weather file missing: {EPW_PATH}")
    return EPW_PATH


@pytest.fixture(scope="session")
def idf_path() -> Path:
    if not IDF_PATH.exists():  # pragma: no cover - repository layout guard
        pytest.skip(f"example model missing: {IDF_PATH}")
    return IDF_PATH


@pytest.fixture
def make_sim(tmp_path):
    """Factory for a quiet :class:`CoSimulation` writing into ``tmp_path``."""
    import pycosim as pc

    def factory(start="2023-07-03", end="2023-07-04", step="15min", **kw):
        kw.setdefault("output_dir", tmp_path)
        kw.setdefault("save", False)
        kw.setdefault("log_level", "WARNING")
        return pc.CoSimulation(start, end, step, **kw)

    return factory


@pytest.fixture
def restore_sys_path():
    """``build_simulation`` prepends the scenario folder to ``sys.path``; undo that."""
    saved = list(sys.path)
    yield
    sys.path[:] = saved


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    import matplotlib.pyplot as plt

    plt.close("all")
