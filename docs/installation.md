# Installation

Py-CoSim has two parts that you install separately:

1. the **Python package** `py-cosim` (imported as `pycosim`), and
2. **EnergyPlus** (only needed for EnergyPlus buildings — the RC building model, PV,
   batteries, tariffs, LLM and RL tools all work without it).

## 1. Requirements

| Item | Version |
|---|---|
| Python | 3.9 or newer (developed and tested with 3.12) |
| Required packages | numpy ≥ 1.21, pandas ≥ 1.3, matplotlib ≥ 3.4, pyyaml ≥ 5.4 (installed automatically) |
| EnergyPlus | 9.4 or newer (the first version with a Python API); **tested with 22.2**. The example models are EnergyPlus 22.2 files |
| Operating system | Windows, macOS or Linux |

## 2. Get the code and create an environment

Install Py-CoSim from a clone of the repository so that you also get the examples:

```bash
git clone https://github.com/lz356/Py-CoSim.git
cd Py-CoSim
git checkout v2          # only needed while v2 is not yet the default branch
```

Create an isolated Python environment (recommended; it keeps Py-CoSim's packages away
from your other projects). Pick **one** of the options:

**venv — Windows (PowerShell)**

```powershell
py -m venv .venv
.venv\Scripts\activate
```

**venv — macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**conda (any OS)**

```bash
conda create -n pycosim python=3.12 pip
conda activate pycosim
```

## 3. Install the package

From the repository root (the folder that contains `pyproject.toml`):

```bash
pip install -e .
```

`-e` ("editable") means Python uses the source files in `src/pycosim/` directly, so a
`git pull` or your own edits take effect without reinstalling.

### Optional extras

Extras add optional dependencies. Quote the argument (zsh on macOS needs the quotes):

| Command | Adds | Needed for |
|---|---|---|
| `pip install -e ".[ml]"` | scikit-learn | `OccupancyMLP` (data-driven occupancy model) |
| `pip install -e ".[rl]"` | gymnasium | `pycosim.gym` (`CoSimEnv`, reinforcement learning) |
| `pip install -e ".[fmu]"` | fmpy | wrapping FMUs in your own component (see the [FAQ](faq.md#can-i-use-modelica-models-or-fmus)) |
| `pip install -e ".[dev]"` | pytest, pytest-cov, scikit-learn, gymnasium, ruff | running the test-suite, contributing |
| `pip install -e ".[all]"` | scikit-learn, gymnasium, fmpy | everything optional except the developer tools |

Packages that are *not* part of any extra but that some features use:

| Command | Needed for |
|---|---|
| `pip install anthropic` | the Claude backend of the LLM supervisor (`AnthropicBackend`) |
| `pip install stable-baselines3` | ready-made RL algorithms (PPO, SAC, …) on top of `CoSimEnv` |
| `pip install ipympl` | live plots inside Jupyter (`%matplotlib widget`) |

The OpenAI-compatible and Ollama LLM backends need no extra package (they use plain HTTP).

## 4. Install EnergyPlus

1. Download an installer from <https://energyplus.net/downloads> (older versions are on the
   EnergyPlus GitHub releases page). Version **22.2** matches the example models.
2. Run the installer with its default options. Typical install locations:

   | OS | Default folder |
   |---|---|
   | Windows | `C:\EnergyPlusV22-2-0` |
   | macOS | `/Applications/EnergyPlus-22-2-0` |
   | Linux | `/usr/local/EnergyPlus-22-2-0` |

3. That's it. You do **not** need `sys.path.insert(0, 'C:/EnergyPlusV22-2-0/')` or any
   other path hack — Py-CoSim finds EnergyPlus and its Python API (`pyenergyplus`) itself.

### How Py-CoSim finds EnergyPlus

Py-CoSim picks the installation in this order — the first rule that applies wins:

1. **An explicit folder** — `EnergyPlusComponent(..., energyplus_dir="...")` or
   `pycosim handles ... --energyplus-dir ...`.
2. **An environment variable** — the first of `PYCOSIM_ENERGYPLUS_DIR`, `ENERGYPLUS_DIR`,
   `EPLUS_DIR`, `ENERGYPLUS_INSTALL_DIR` that points to a valid installation (with the
   Python API). It *pins* that installation, even if newer versions are installed.
3. **Otherwise the newest version found** (that ships the Python API) among the
   `energyplus` executable on your `PATH` and the standard folders: `C:\EnergyPlusV*` and
   `C:\Program Files\EnergyPlusV*` (Windows), `/Applications/EnergyPlus-*` (macOS),
   `/usr/local/EnergyPlus-*`, `/opt/EnergyPlus*`, `~/EnergyPlus*` (Linux).

So:

* If EnergyPlus is in a non-standard place (e.g. a zip/tarball extracted somewhere), set
  `ENERGYPLUS_DIR`.
* If you have **several versions** installed and want a specific one (for example 22.2 for
  the example models while 24.x is also installed), set `ENERGYPLUS_DIR` to it once, or pass
  `energyplus_dir=` in your script.

Setting `ENERGYPLUS_DIR`:

```powershell
# Windows, PowerShell: current session only
$env:ENERGYPLUS_DIR = "D:\Tools\EnergyPlusV22-2-0"
# Windows: permanently for your user (open a new terminal afterwards)
setx ENERGYPLUS_DIR "D:\Tools\EnergyPlusV22-2-0"
```

```bash
# macOS / Linux: add this line to ~/.bashrc or ~/.zshrc
export ENERGYPLUS_DIR="$HOME/EnergyPlus-22.2.0"
```

## 5. Check the installation: `pycosim doctor`

```bash
pycosim version
pycosim doctor
```

`pycosim doctor` lists the Python packages and every EnergyPlus installation it can find.
The installation marked with `*` is the newest one, which Py-CoSim uses unless an
environment variable such as `ENERGYPLUS_DIR` (or `energyplus_dir=`) selects another:

```text
Py-CoSim 2.0.0
Python  3.12.10 (Windows-11-10.0.26200-SP0)
  [ok] numpy       2.5.4
  [ok] pandas      3.1.0
  [ok] matplotlib  3.11.2
  [ok] yaml        6.0.3
  [ok] sklearn     1.9.1
  [ok] gymnasium   1.4.0
  [--] anthropic   not installed (optional)
  [--] fmpy        not installed (optional)
 * EnergyPlus 22.2.0   C:\EnergyPlusV22-2-0  (Python API ok)
    pyenergyplus import ok (API version 0.2)
```

* `[!!] ... MISSING` next to a required package: re-run `pip install -e .` in the active
  environment.
* `[--] ... not installed (optional)`: fine unless you need that feature.
* `EnergyPlus: none found`: see [the FAQ](faq.md#energyplus-not-found).

Then run the first two examples from the repository root:

```bash
python examples/01_quickstart_no_energyplus/run.py     # no EnergyPlus, ~1 s
python examples/02_energyplus_basics/run.py --days 2   # EnergyPlus, ~10 s
```

To run the test-suite (after `pip install -e ".[dev]"`):

```bash
pytest            # tests marked "energyplus" are skipped when EnergyPlus is missing
```

## 6. Notes per operating system

### Windows

* Use the **same** terminal (with the environment activated) for `pip install` and for
  running scripts. If PowerShell refuses to run `activate`, use `cmd.exe` and
  `.venv\Scripts\activate.bat` instead, or call the environment's Python directly
  (`.venv\Scripts\python examples\01_quickstart_no_energyplus\run.py`).
* EnergyPlus workers run as background `python.exe` processes without a console window;
  you can see them in Task Manager while a co-simulation runs.
* If your model files live on OneDrive or a network drive and conversions are slow, copy
  the project to a local folder.

### macOS

* EnergyPlus and Python must be built for the **same CPU architecture**. On Apple Silicon
  (M1/M2/…), an `x86_64` EnergyPlus cannot be loaded from an `arm64` Python and vice versa;
  install the EnergyPlus build that matches `python -c "import platform; print(platform.machine())"`.
* EnergyPlus installs into `/Applications/EnergyPlus-<version>`, which is searched
  automatically.

### Linux

* The `.sh` installer puts EnergyPlus in `/usr/local/EnergyPlus-<version>` (searched
  automatically). If you extract a `.tar.gz` elsewhere, set `ENERGYPLUS_DIR`.
* Headless servers: live plots need a display. Use `Results.plot()` and save figures
  with `fig.savefig(...)` instead, or set `MPLBACKEND=Agg`.

## 7. Where Py-CoSim writes files

| What | Where |
|---|---|
| Results of a run | `<output_dir>/<name>/` — by default `pycosim_runs/<name>/` relative to the folder you start Python from (see [Results](scenarios-and-cli.md#results)) |
| EnergyPlus files of a building | `<output_dir>/<name>/<component>/eplus/` (see [EnergyPlus](energyplus.md#where-the-files-go)) |
| IDF → epJSON conversion cache | the system temp folder (`pycosim_cache`), or the folder in the `PYCOSIM_CACHE_DIR` environment variable |

## 8. Optional: credentials for cloud LLMs

Never write API keys into scripts or notebooks. Set them as environment variables:

| Backend | Variable |
|---|---|
| `AnthropicBackend` (Claude) | `ANTHROPIC_API_KEY` (the `anthropic` SDK also accepts a profile created with `ant auth login`) |
| `OpenAIBackend` | `OPENAI_API_KEY` (or pass `api_key=` for self-hosted servers) |
| `OllamaBackend` | none (local server) |

See [AI and RL](ai-and-rl.md) for details.
