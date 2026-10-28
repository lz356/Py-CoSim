"""Locate EnergyPlus installations and their Python API (``pyenergyplus``).

Search order:

1. an explicit path passed by the user,
2. the first of the environment variables ``PYCOSIM_ENERGYPLUS_DIR``,
   ``ENERGYPLUS_DIR``, ``EPLUS_DIR``, ``ENERGYPLUS_INSTALL_DIR`` that points to an
   installation (an environment variable pins the version),
3. the ``energyplus`` executable on ``PATH``,
4. standard installation folders (``C:\\EnergyPlusV*``,
   ``/Applications/EnergyPlus-*``, ``/usr/local/EnergyPlus-*``, ...).

Among the installations found in steps 3-4 the newest one wins (or the one
matching the requested ``version``).
"""
from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

from ..core.errors import PyCoSimError

__all__ = ["EnergyPlusInstall", "find_energyplus", "find_all_energyplus", "EnergyPlusNotFoundError"]

_ENV_VARS = ("PYCOSIM_ENERGYPLUS_DIR", "ENERGYPLUS_DIR", "EPLUS_DIR", "ENERGYPLUS_INSTALL_DIR")
_VERSION_RE = re.compile(r"(\d+)[-_.](\d+)(?:[-_.](\d+))?")


class EnergyPlusNotFoundError(PyCoSimError, RuntimeError):
    """No usable EnergyPlus installation was found."""


@dataclass(frozen=True)
class EnergyPlusInstall:
    """An EnergyPlus installation folder."""

    root: Path
    version: Tuple[int, int, int]

    @property
    def version_str(self) -> str:
        return ".".join(map(str, self.version))

    @property
    def executable(self) -> Path:
        name = "energyplus.exe" if os.name == "nt" else "energyplus"
        return self.root / name

    @property
    def idd(self) -> Path:
        return self.root / "Energy+.idd"

    @property
    def has_python_api(self) -> bool:
        return (self.root / "pyenergyplus" / "api.py").exists()

    def import_api(self):
        """Import and return the ``pyenergyplus.api.EnergyPlusAPI`` class."""
        if not self.has_python_api:
            raise EnergyPlusNotFoundError(
                f"EnergyPlus at {self.root} has no Python API (pyenergyplus); version 9.4 or newer is required"
            )
        root = str(self.root)
        if root not in sys.path:
            sys.path.insert(0, root)
        from pyenergyplus.api import EnergyPlusAPI  # type: ignore

        return EnergyPlusAPI

    def __str__(self) -> str:
        return f"EnergyPlus {self.version_str} at {self.root}"


def _parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    m = _VERSION_RE.search(text)
    if not m:
        return None
    major, minor, patch = m.group(1), m.group(2), m.group(3) or "0"
    return int(major), int(minor), int(patch)


def _version_from_idd(root: Path) -> Optional[Tuple[int, int, int]]:
    idd = root / "Energy+.idd"
    try:
        with open(idd, "r", encoding="latin-1") as fh:
            first = fh.readline()
        return _parse_version(first.split("IDD_Version", 1)[-1])
    except OSError:
        return None


def _make_install(root: Union[str, Path]) -> Optional[EnergyPlusInstall]:
    root = Path(root).expanduser()
    if root.is_file():
        root = root.parent
    if not root.is_dir():
        return None
    exe = root / ("energyplus.exe" if os.name == "nt" else "energyplus")
    if not exe.exists() and not (root / "Energy+.idd").exists():
        return None
    version = _version_from_idd(root) or _parse_version(root.name) or (0, 0, 0)
    return EnergyPlusInstall(root.resolve(), version)


def _candidate_roots() -> List[Path]:
    roots: List[Path] = []
    for var in _ENV_VARS:
        value = os.environ.get(var)
        if value:
            roots.append(Path(value))
    exe = shutil.which("energyplus")
    if exe:
        roots.append(Path(os.path.realpath(exe)).parent)
    system = platform.system()
    patterns: List[str] = []
    if system == "Windows":
        drive = os.environ.get("SystemDrive", "C:")
        patterns += [
            f"{drive}\\EnergyPlusV*",
            f"{drive}\\EnergyPlus*",
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "EnergyPlusV*"),
        ]
    elif system == "Darwin":
        patterns += ["/Applications/EnergyPlus-*", "/Applications/EnergyPlus*"]
    else:
        patterns += ["/usr/local/EnergyPlus-*", "/usr/local/EnergyPlus*", "/opt/EnergyPlus*", os.path.expanduser("~/EnergyPlus*")]
    for pattern in patterns:
        roots.extend(Path(p) for p in sorted(glob.glob(pattern)))
    return roots


def find_all_energyplus() -> List[EnergyPlusInstall]:
    """All EnergyPlus installations that can be found, newest first."""
    seen = {}
    for root in _candidate_roots():
        inst = _make_install(root)
        if inst is not None and inst.root not in seen:
            seen[inst.root] = inst
    return sorted(seen.values(), key=lambda i: i.version, reverse=True)


def find_energyplus(path: Optional[Union[str, Path]] = None, version: Optional[str] = None,
                    require_api: bool = True) -> EnergyPlusInstall:
    """Return the EnergyPlus installation to use.

    Parameters
    ----------
    path : str or Path, optional
        Installation folder (or the energyplus executable).
    version : str, optional
        Requested version prefix, e.g. ``"22.2"`` or ``"24"``.
    require_api : bool
        Only accept installations that ship the Python API.
    """
    if path is not None:
        inst = _make_install(path)
        if inst is None:
            raise EnergyPlusNotFoundError(f"No EnergyPlus installation found at {path}")
        if require_api and not inst.has_python_api:
            raise EnergyPlusNotFoundError(f"{inst} does not include the Python API (pyenergyplus)")
        return inst
    if version is None:
        for var in _ENV_VARS:  # an environment variable pins the installation
            value = os.environ.get(var)
            inst = _make_install(value) if value else None
            if inst is not None and (inst.has_python_api or not require_api):
                return inst
    installs = [i for i in find_all_energyplus() if (i.has_python_api or not require_api)]
    if version is not None:
        installs = [i for i in installs if i.version_str.startswith(str(version))]
    if not installs:
        hint = (
            "Install EnergyPlus (>= 9.4, recommended >= 22.1) from https://energyplus.net/downloads "
            "or point Py-CoSim to it with the ENERGYPLUS_DIR environment variable "
            "(or EnergyPlusComponent(..., energyplus_dir=...))."
        )
        wanted = f" version {version}" if version else ""
        raise EnergyPlusNotFoundError(f"Could not find an EnergyPlus{wanted} installation. {hint}")
    return installs[0]


def run_energyplus_cli(install: EnergyPlusInstall, args: List[str], cwd: Optional[Union[str, Path]] = None,
                       timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    """Run the EnergyPlus command line (used for IDF <-> epJSON conversion and reference runs)."""
    return subprocess.run([str(install.executable)] + list(args), cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)
