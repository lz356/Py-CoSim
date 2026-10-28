"""Concept diagrams for the TIDE manuscript (Fig. 1 and Fig. 2)."""
from __future__ import annotations

import math

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

from common import COLORS, FIG, set_style

BLUE, ORANGE, GREEN, VERMILION, PURPLE, SKY = COLORS[0], COLORS[1], COLORS[2], COLORS[3], COLORS[4], COLORS[5]


def box(ax, x, y, w, h, text, fc="white", ec="k", fs=7.5, lw=0.9, weight="normal", style="round,pad=0.02"):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle=style, fc=fc, ec=ec, lw=lw))
    ax.text(x, y, text, ha="center", va="center", fontsize=fs, weight=weight, linespacing=1.15)


def arrow(ax, p, q, color="k", lw=0.9, style="-|>", ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=8, color=color, lw=lw, ls=ls,
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=2, shrinkB=2))


def fig_concept() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.35), gridspec_kw={"width_ratios": [1, 1.35]})
    names = ["EnergyPlus\nbuilding", "PV and\nbattery", "Tariff /\nprice API", "ML\npredictor", "LLM\nsupervisor"]
    # (a) connecting software: a mesh of pairwise interfaces
    ax = axes[0]
    ax.set_xlim(-1.35, 1.35)
    ax.set_ylim(-1.3, 1.25)
    pts = [(math.cos(math.pi / 2 + i * 2 * math.pi / 5), math.sin(math.pi / 2 + i * 2 * math.pi / 5)) for i in range(5)]
    for i in range(5):
        for j in range(i + 1, 5):
            (x1, y1), (x2, y2) = pts[i], pts[j]
            ax.plot([x1, x2], [y1, y2], color="0.55", lw=0.8, zorder=1)
            ax.add_patch(Rectangle(((x1 + x2) / 2 - 0.05, (y1 + y2) / 2 - 0.035), 0.1, 0.07, fc=ORANGE, ec="none",
                                   zorder=2))
    for (x, y), n in zip(pts, names):
        box(ax, x, y, 0.62, 0.36, n, fc="#EAF2FA", ec=BLUE)
    ax.text(0, -1.22, "10 pairwise interfaces, each with its own\ntime conversion (orange)", ha="center", va="center",
            fontsize=7)
    ax.set_title("(a) Connecting software", loc="left")
    ax.axis("off")
    # (b) TIDE
    ax = axes[1]
    ax.set_xlim(-1.75, 1.75)
    ax.set_ylim(-1.3, 1.25)
    ax.add_patch(FancyBboxPatch((-0.78, -0.52), 1.56, 1.04, boxstyle="round,pad=0.03", fc="#F4F4F4", ec="k", lw=1.1))
    ax.text(0, 0.40, "TIDE", ha="center", va="center", fontsize=9, weight="bold")
    ax.text(-0.39, 0.06, "Management\nclock, phases,\nexecution order,\ndeadlines, replay", ha="center", va="center",
            fontsize=6.5, linespacing=1.15)
    ax.text(0.39, 0.06, "Messaging\nvalue + validity\n+ meaning + quality;\nalignment, history", ha="center",
            va="center", fontsize=6.5, linespacing=1.15)
    ax.plot([0, 0], [-0.28, 0.28], color="0.5", lw=0.6)
    ax.text(0, -0.42, "trace  ·  records  ·  results", ha="center", va="center", fontsize=6, color="0.3")
    comps = [
        ((-1.25, 0.85), names[0], "P: average [t−h, t)", (0, 0.27)),
        ((1.25, 0.85), names[1], "P_bat: average [t, t+h)", (0, 0.27)),
        ((-1.32, -0.25), names[2], "price: hold", (0, -0.27)),
        ((1.32, -0.25), names[3], "reads 24 h history", (0, -0.27)),
        ((0.0, -1.02), names[4], "set-point: hold,\nasync, bounded", (0.62, 0.0)),
    ]

    def edge(cx, cy, w, h, tx, ty):
        """Point where the segment from the box centre towards (tx, ty) leaves the box."""
        dx, dy = tx - cx, ty - cy
        s = min((w / 2) / abs(dx) if dx else 1e9, (h / 2) / abs(dy) if dy else 1e9)
        return cx + dx * s, cy + dy * s

    for (x, y), n, lab, (ox, oy) in comps:
        box(ax, x, y, 0.62, 0.36, n, fc="#EAF2FA", ec=BLUE)
        p = edge(x, y, 0.66, 0.40, 0.0, 0.0)
        q = edge(0.0, 0.0, 1.62, 1.10, x, y)
        arrow(ax, p, q, color=GREEN, rad=0.18)          # publish
        arrow(ax, q, p, color=VERMILION, ls="--", rad=0.18)  # subscribe
        ax.text(x + ox, y + oy, lab, ha="center" if not ox else "left", va="center", fontsize=6.2, color="0.15",
                style="italic")
    ax.plot([], [], color=GREEN, label="publish")
    ax.plot([], [], color=VERMILION, ls="--", label="subscribe (aligned)")
    ax.legend(loc="lower right", fontsize=6.5, frameon=False, bbox_to_anchor=(1.03, -0.05))
    ax.set_title("(b) Py-CoSim: messages through TIDE", loc="left")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(FIG / "fig01_concept.png")
    plt.close(fig)


def fig_time_meaning() -> None:
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    t = [0, 15, 30, 45, 60]  # minutes after 10:00
    rows = {"power": 3.0, "price": 2.0, "temp": 1.0, "consumer": 0.0}
    # average (past) power: bars over [t-h, t) published at t
    vals = [42, 55, 61, 48]
    for i, v in enumerate(vals):
        a, b = t[i], t[i + 1]
        y = rows["power"] - 0.35 + 0.006 * v
        ax.add_patch(Rectangle((a, rows["power"] - 0.35), b - a, 0.006 * v, fc="#D6E6F4", ec=BLUE, lw=0.8))
        ax.annotate("", xy=(b, rows["power"] + 0.42), xytext=(b, y), arrowprops=dict(arrowstyle="-|>", color=BLUE,
                                                                                     lw=0.7))
        ax.text((a + b) / 2, rows["power"] - 0.22, f"{v} kW", ha="center", va="center", fontsize=6.5)
    ax.text(15.6, rows["power"] + 0.5, "published at the end of its interval", fontsize=6.2, color=BLUE, va="center")
    ax.text(-1.5, rows["power"], "EnergyPlus power\nkind: average (past)", ha="right", va="center", fontsize=7)
    # hold price: steps from publication time
    prices = [0.10, 0.10, 0.30, 0.30, 0.30]
    for i in range(4):
        y = rows["price"] - 0.3 + (0.5 if prices[i] > 0.2 else 0.15)
        ax.plot([t[i], t[i + 1]], [y, y], color=ORANGE, lw=1.6)
        ax.plot(t[i], y, "o", color=ORANGE, ms=3)
        ax.text(t[i] + 7.5, y + 0.12, f"{prices[i]:.2f} $/kWh", ha="center", fontsize=6.2)
    ax.text(-1.5, rows["price"], "Tariff price\nkind: hold", ha="right", va="center", fontsize=7)
    # instant temperature: samples with linear interpolation
    temps = [24.2, 24.6, 23.1, 22.4, 22.6]
    ys = [rows["temp"] - 0.3 + 0.25 * (v - 22) for v in temps]
    ax.plot(t, ys, color=GREEN, lw=0.9, ls="--")
    ax.plot(t, ys, "o", color=GREEN, ms=3.5)
    ax.text(-1.5, rows["temp"], "Outdoor temperature\nkind: instant", ha="right", va="center", fontsize=7)
    # consumer windows at tk = 10:30 with h = 15 min
    tk = 30
    y0 = rows["consumer"]
    ax.add_patch(Rectangle((tk - 15, y0 - 0.25), 15, 0.5, fc="#F3D3C1", ec=VERMILION, lw=0.8))
    ax.text(tk - 7.5, y0, "past\n[t−h, t)", ha="center", va="center", fontsize=6.5)
    ax.add_patch(Rectangle((tk, y0 - 0.25), 15, 0.5, fc="#E9DCEB", ec=PURPLE, lw=0.8))
    ax.text(tk + 7.5, y0, "ahead\n[t, t+h)", ha="center", va="center", fontsize=6.5)
    ax.axvline(tk, color="k", lw=0.8, ls=":")
    ax.text(tk, 3.62, "consumer at t = 10:30 (h = 15 min); read \"at\" takes the value in effect here", ha="center",
            fontsize=6.5)
    ax.text(-1.5, y0, "Consumer windows", ha="right", va="center", fontsize=7)
    ax.text(62, rows["power"], "past → 55 kW\n(10:15–10:30)", fontsize=6.3, va="center", color=BLUE)
    ax.text(62, rows["price"], "past → 0.10 $/kWh\nlatest → 0.30 $/kWh", fontsize=6.3, va="center", color=ORANGE)
    ax.text(62, rows["temp"], "past → 23.85 °C\n(trapezoid)", fontsize=6.3, va="center", color=GREEN)
    ax.set_xlim(-22, 76)
    ax.set_ylim(-0.5, 3.8)
    ax.set_xticks(t)
    ax.set_xticklabels(["10:00", "10:15", "10:30", "10:45", "11:00"])
    ax.set_yticks([])
    ax.grid(False)
    for s in ("left", "right", "top"):
        ax.spines[s].set_visible(False)
    ax.set_xlabel("Time")
    fig.tight_layout()
    fig.savefig(FIG / "fig02_time_meaning.png")
    plt.close(fig)


if __name__ == "__main__":
    set_style()
    fig_concept()
    fig_time_meaning()
