"""Command line interface: ``pycosim doctor | handles | run | version``."""
from __future__ import annotations

import argparse
import importlib
import platform
import sys
from typing import List, Optional

from ._version import __version__


def _doctor(_args) -> int:
    print(f"Py-CoSim {__version__}")
    print(f"Python  {sys.version.split()[0]} ({platform.platform()})")
    for mod in ("numpy", "pandas", "matplotlib", "yaml", "sklearn", "gymnasium", "anthropic", "fmpy"):
        try:
            m = importlib.import_module(mod)
            print(f"  [ok] {mod:<11} {getattr(m, '__version__', '')}")
        except ImportError:
            print(f"  [--] {mod:<11} not installed (optional)" if mod not in ("numpy", "pandas", "matplotlib", "yaml")
                  else f"  [!!] {mod:<11} MISSING")
    from .energyplus.discovery import find_all_energyplus

    from .energyplus.discovery import find_energyplus

    installs = find_all_energyplus()
    try:
        selected = find_energyplus().root
    except Exception:
        selected = None
    if not installs and selected is None:
        print("EnergyPlus: none found. Install it from https://energyplus.net or set ENERGYPLUS_DIR.")
        return 1
    if selected is not None and selected not in {i.root for i in installs}:
        installs.insert(0, find_energyplus())
    print("EnergyPlus installations (* = the one Py-CoSim uses):")
    for i, inst in enumerate(installs):
        flag = "*" if inst.root == selected else " "
        api = "Python API ok" if inst.has_python_api else "no Python API"
        print(f" {flag} EnergyPlus {inst.version_str:<8} {inst.root}  ({api})")
    try:
        api_cls = next(i for i in installs if i.root == selected).import_api()
        print(f"    pyenergyplus import ok (API version {api_cls.api_version()})")
    except Exception as exc:  # pragma: no cover - environment specific
        print(f"    pyenergyplus import FAILED: {exc}")
        return 1
    return 0


def _handles(args) -> int:
    from .energyplus.handles import list_available_handles

    df = list_available_handles(args.model, args.weather, energyplus_dir=args.energyplus_dir)
    if args.kind:
        df = df[df["kind"] == args.kind]
    if args.grep:
        text = args.grep.lower()
        mask = df.apply(lambda r: text in " ".join(map(str, r.values)).lower(), axis=1)
        df = df[mask]
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"wrote {len(df)} rows to {args.csv}")
    else:
        import pandas as pd

        with pd.option_context("display.max_rows", args.max_rows, "display.max_colwidth", 80, "display.width", 200):
            print(df[["kind", "spec", "unit"]].to_string(index=False))
        print(f"\n{len(df)} handles. Paste a 'spec' into EnergyPlusComponent(sensors=..., actuators=..., meters=...).")
    return 0


def _run(args) -> int:
    from .config import load_scenario

    overrides = {}
    if args.name:
        overrides["name"] = args.name
    sim = load_scenario(args.scenario, **overrides)
    results = sim.run(progress=not args.quiet)
    print(results.summary())
    if sim.save:
        print(f"results written to {sim.output_path}")
    if args.plot:
        import matplotlib.pyplot as plt

        results.plot()
        plt.show()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="pycosim", description="Py-CoSim co-simulation toolkit")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("version", help="print the version")
    sub.add_parser("doctor", help="check the installation and find EnergyPlus")
    p = sub.add_parser("handles", help="list sensors/actuators/meters available in an EnergyPlus model")
    p.add_argument("model")
    p.add_argument("-w", "--weather", required=True)
    p.add_argument("--kind", choices=["actuator", "variable", "meter", "internal_variable"])
    p.add_argument("--grep", help="case-insensitive text filter")
    p.add_argument("--csv", help="write the table to a CSV file")
    p.add_argument("--max-rows", type=int, default=500)
    p.add_argument("--energyplus-dir")
    r = sub.add_parser("run", help="run a YAML scenario")
    r.add_argument("scenario")
    r.add_argument("--name")
    r.add_argument("--plot", action="store_true")
    r.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "handles":
        return _handles(args)
    if args.command == "run":
        return _run(args)
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
