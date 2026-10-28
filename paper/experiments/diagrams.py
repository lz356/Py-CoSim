"""Schematic figures of the paper (architecture, EnergyPlus handshake, master algorithm)."""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from common import FIG, set_style

BLUE, ORANGE, GREEN, RED, PURPLE, SKY, GREY = "#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#7F7F7F"


def box(ax, x, y, w, h, text, fc="white", ec="black", fs=8, bold=False, lw=1.0, alpha=1.0, ha="center", va="center",
        style="round,pad=0.003,rounding_size=0.012"):
    p = FancyBboxPatch((x, y), w, h, boxstyle=style, fc=fc, ec=ec, lw=lw, alpha=alpha)
    ax.add_patch(p)
    tx = x + w / 2 if ha == "center" else x + 0.01
    ax.text(tx, y + h / 2 if va == "center" else y + h - 0.012, text, ha=ha, va=va, fontsize=fs,
            fontweight="bold" if bold else "normal", wrap=True)
    return p


def arrow(ax, x0, y0, x1, y1, color="black", lw=1.0, style="-|>", ls="-", text=None, fs=7, tx=0, ty=0, both=False):
    a = FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="<|-|>" if both else style, mutation_scale=9, color=color,
                        lw=lw, linestyle=ls, shrinkA=0, shrinkB=0)
    ax.add_patch(a)
    if text:
        ax.text((x0 + x1) / 2 + tx, (y0 + y1) / 2 + ty, text, fontsize=fs, ha="center", va="center", color=color)


def architecture():
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    # user layer
    box(ax, 0.02, 0.88, 0.96, 0.1, "", fc="#F2F2F2", ec=GREY)
    ax.text(0.04, 0.93, "Scenario definition", fontsize=8.5, fontweight="bold", va="center")
    for i, t in enumerate(["Python script", "Jupyter notebook", "YAML scenario\n(pycosim run)",
                           "Gymnasium\nRL training loop"]):
        box(ax, 0.25 + i * 0.18, 0.895, 0.16, 0.07, t, fc="white", ec=GREY, fs=7.2)
    # master
    box(ax, 0.02, 0.47, 0.96, 0.38, "", fc="#EAF2F8", ec=BLUE, lw=1.4)
    ax.text(0.04, 0.825, "CoSimulation master (main process)", fontsize=8.5, fontweight="bold", color=BLUE)
    box(ax, 0.04, 0.62, 0.21, 0.17, "Clock\nstart, end, Δt\nmulti-rate steps\n(local standard time)", fc="white", ec=BLUE, fs=7.2)
    box(ax, 0.27, 0.62, 0.23, 0.17, "Scheduler\ndata-flow graph,\ntopological order,\nalgebraic-loop check", fc="white", ec=BLUE, fs=7.2)
    box(ax, 0.52, 0.62, 0.22, 0.17, "Data bus\nlatest & history of every\nport, event log,\ntime-stamped exchange", fc="white", ec=BLUE, fs=7.2)
    box(ax, 0.76, 0.62, 0.2, 0.17, "Supervision\nerror reports, timing,\nobservers (progress,\nlive plots, recorders)", fc="white", ec=BLUE, fs=7.2)
    box(ax, 0.04, 0.495, 0.92, 0.1,
        "Each master step t → t+Δt:   (1) output() of state-based components   →   (2) step() of feedthrough "
        "components in topological order\n→   (3) update() of state-based components "
        "(all EnergyPlus workers advance in parallel)",
        fc="white", ec=BLUE, fs=6.8)
    # components
    box(ax, 0.02, 0.05, 0.43, 0.38, "", fc="#FDF2E6", ec=ORANGE, lw=1.4)
    ax.text(0.04, 0.405, "EnergyPlus components", fontsize=8.5, fontweight="bold", color="#A05A00")
    for i in range(3):
        x = 0.04 + i * 0.135
        box(ax, x, 0.08, 0.125, 0.28, "", fc="white", ec=ORANGE)
        ax.text(x + 0.0625, 0.335, ["Worker 1", "Worker 2", "Worker N"][i], ha="center", fontsize=7.2, fontweight="bold")
        ax.text(x + 0.0625, 0.22, "EnergyPlus\nprocess\n\npyenergyplus\nsession\n(lock-step\ncallbacks)",
                ha="center", va="center", fontsize=6.6)
    ax.text(0.31, 0.35, "…", fontsize=12, ha="center")
    box(ax, 0.47, 0.05, 0.51, 0.38, "", fc="#E8F6F1", ec=GREEN, lw=1.4)
    ax.text(0.49, 0.405, "Python components (main process)", fontsize=8.5, fontweight="bold", color="#00694C")
    items = [("Weather (EPW)\nsolar position", "white"), ("PV · battery\ntariff · grid meter", "white"),
             ("Controllers\nschedules · PI ·\npre-cooling · dispatch", "white"),
             ("Occupancy &\ntravel (NHTS)\nML models", "white"), ("LLM supervisor\n+ guardrails\n(Claude/OpenAI/\nOllama)", "white"),
             ("RL agent\n(ExternalAgent)\n· your own\ncomponents", "white")]
    for i, (t, fc) in enumerate(items):
        x = 0.49 + (i % 3) * 0.163
        y = 0.235 if i < 3 else 0.075
        box(ax, x, y, 0.15, 0.145, t, fc=fc, ec=GREEN, fs=6.8)
    # arrows
    arrow(ax, 0.5, 0.88, 0.5, 0.85, color=GREY, lw=1.2)
    arrow(ax, 0.235, 0.47, 0.235, 0.43, color=ORANGE, lw=1.4, both=True)
    ax.text(0.25, 0.452, "obs (t, y) / act (u) via local pipes", fontsize=6.8, color="#A05A00", va="center")
    arrow(ax, 0.725, 0.47, 0.725, 0.43, color=GREEN, lw=1.4, both=True)
    ax.text(0.735, 0.452, "in-process calls", fontsize=6.8, color="#00694C", va="center")
    fig.savefig(FIG / "fig01_architecture.png")
    plt.close(fig)


def handshake():
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.2, 3.9), gridspec_kw={"width_ratios": [1.15, 1]})
    for a in (ax, bx):
        a.set_xlim(0, 1)
        a.set_ylim(0, 1)
        a.axis("off")
    # (a) sequence diagram
    ax.text(0.0, 0.98, "(a) Lock-step handshake with an EnergyPlus worker", fontsize=8.5, fontweight="bold", va="top")
    xm, xw = 0.23, 0.74
    box(ax, xm - 0.17, 0.86, 0.34, 0.07, "Master (main process)", fc="#EAF2F8", ec=BLUE, fs=7.5, bold=True)
    box(ax, xw - 0.21, 0.86, 0.42, 0.07, "Worker (EnergyPlus callbacks)", fc="#FDF2E6", ec=ORANGE, fs=7.5, bold=True)
    for x in (xm, xw):
        ax.plot([x, x], [0.03, 0.86], color=GREY, lw=0.8, ls="--")
    y = 0.8
    steps = [
        ("w", "end of step k−1: read sensors & meters\n→ snapshot y(t_k)"),
        ("w2m", "obs: (month, day, hour, minute), y(t_k)"),
        ("m", "(1) output(t_k): check stamp = t_k"),
        ("m", "(2) other components step at t_k"),
        ("m2w", "act: u(t_k)   (None → release actuator)"),
        ("w", "begin step k: set actuators, then\nEnergyPlus simulates [t_k, t_k+Δt]"),
        ("w", "end of step k → snapshot y(t_k+1) …"),
    ]
    for kind, text in steps:
        if kind == "w":
            box(ax, xw - 0.24, y - 0.065, 0.48, 0.075, text, fc="white", ec=ORANGE, fs=6.4)
            y -= 0.11
        elif kind == "m":
            box(ax, xm - 0.22, y - 0.05, 0.44, 0.06, text, fc="white", ec=BLUE, fs=6.4)
            y -= 0.095
        elif kind == "w2m":
            arrow(ax, xw, y, xm, y, color=ORANGE, lw=1.2, text=text, ty=0.025, fs=6.4)
            y -= 0.075
        else:
            arrow(ax, xm, y, xw, y, color=BLUE, lw=1.2, text=text, ty=0.025, fs=6.4)
            y -= 0.075
    # (b) multi-rate timeline
    bx.text(0.0, 0.98, "(b) Multi-rate exchange on the master grid", fontsize=8.5, fontweight="bold", va="top")
    lanes = [("EnergyPlus (Δt = 15 min)", ORANGE, 1), ("Controller (15 min)", GREEN, 1), ("LLM supervisor (1 h)", PURPLE, 4)]
    x0, x1, n = 0.08, 0.95, 8
    dx = (x1 - x0) / n
    for i, (name, c, m) in enumerate(lanes):
        yy = 0.78 - i * 0.2
        bx.text(x0, yy + 0.065, name, fontsize=7.2, color=c, fontweight="bold")
        bx.plot([x0, x1], [yy, yy], color=GREY, lw=0.6)
        for k in range(n):
            if k % m == 0:
                bx.add_patch(FancyBboxPatch((x0 + k * dx + 0.005, yy - 0.025), dx * m - 0.01, 0.05,
                                            boxstyle="round,pad=0.002", fc=c, ec=c, alpha=0.25))
                bx.plot([x0 + k * dx], [yy], "o", color=c, ms=3.5)
        if m > 1:
            bx.text(x0 + 2 * dx, yy - 0.06, "output held (zero-order hold)", fontsize=6.4, color=c, ha="center")
    for k in range(n + 1):
        bx.text(x0 + k * dx, 0.12, f"{k * 15}", fontsize=6.4, ha="center")
        bx.plot([x0 + k * dx] * 2, [0.15, 0.85], color=GREY, lw=0.3, ls=":")
    bx.text((x0 + x1) / 2, 0.06, "master time [min]", fontsize=7, ha="center")
    bx.text(0.5, 0.0, "● = component executes; bars = interval its output/actuation applies to", fontsize=6.4,
            ha="center", color=GREY)
    fig.tight_layout()
    fig.savefig(FIG / "fig02_handshake_multirate.png")
    plt.close(fig)


def main():
    set_style()
    architecture()
    handshake()
    print("diagrams written to", FIG)


if __name__ == "__main__":
    main()
