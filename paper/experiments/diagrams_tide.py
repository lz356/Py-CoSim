"""Concept diagrams for the TIDE manuscript (Fig. 1 and Fig. 2).

Both are drawn at their printed size (6.5 in text width) so that all text is
8-10 pt in print; Fig. 1 uses inch coordinates for exact placement.
"""
from __future__ import annotations

import math

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

from common import COLORS, TEXT_WIDTH_IN, paper_style, save_paper_figure

BLUE, ORANGE, GREEN, VERMILION, PURPLE, SKY = COLORS[0], COLORS[1], COLORS[2], COLORS[3], COLORS[4], COLORS[5]
FS = 8  # text size [pt] of all annotations


def box(ax, x, y, w, h, text, fc="white", ec="k", fs=FS, lw=0.9, weight="normal", style="round,pad=0.02"):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle=style, fc=fc, ec=ec, lw=lw))
    ax.text(x, y, text, ha="center", va="center", fontsize=fs, weight=weight, linespacing=1.15)


def arrow(ax, p, q, color="k", lw=0.9, style="-|>", ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=8, color=color, lw=lw, ls=ls,
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=2, shrinkB=2))


def edge(cx, cy, w, h, tx, ty):
    """Point where the segment from the box centre (cx, cy) towards (tx, ty) leaves a w x h box."""
    dx, dy = tx - cx, ty - cy
    s = min((w / 2) / abs(dx) if dx else 1e9, (h / 2) / abs(dy) if dy else 1e9)
    return cx + dx * s, cy + dy * s


def fig_concept() -> None:
    W, H = TEXT_WIDTH_IN, 3.35
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0, 0, 1, 1])  # data coordinates = inches
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    names = ["EnergyPlus\nbuilding", "PV and\nbattery", "Tariff /\nprice API", "ML\npredictor", "LLM\nsupervisor"]
    bw, bh = 0.68, 0.38  # component box [in]
    # (a) connecting software: a mesh of pairwise interfaces
    ax.text(0.02, H - 0.06, "(a) Connecting software", ha="left", va="top", fontsize=9)
    cx, cy, r = 1.21, 1.72, 0.86
    pts = [(cx + r * math.cos(math.pi / 2 + i * 2 * math.pi / 5), cy + r * math.sin(math.pi / 2 + i * 2 * math.pi / 5))
           for i in range(5)]
    for i in range(5):
        for j in range(i + 1, 5):
            (x1, y1), (x2, y2) = pts[i], pts[j]
            ax.plot([x1, x2], [y1, y2], color="0.55", lw=0.8, zorder=1)
            ax.add_patch(Rectangle(((x1 + x2) / 2 - 0.055, (y1 + y2) / 2 - 0.035), 0.11, 0.07, fc=ORANGE,
                                   ec="none", zorder=2))
    for (x, y), n in zip(pts, names):
        box(ax, x, y, bw, bh, n, fc="#EAF2FA", ec=BLUE)
    ax.text(cx, 0.28, "10 pairwise interfaces, each with its\nown time conversion (orange)", ha="center",
            va="center", fontsize=FS, linespacing=1.15)
    # (b) Py-CoSim: every component talks to TIDE
    x0 = 2.50
    ax.text(x0, H - 0.06, "(b) Py-CoSim: messages through TIDE", ha="left", va="top", fontsize=9)
    tx, ty, tw, th = 4.50, 1.52, 2.10, 1.22  # TIDE box centre and size [in]
    ax.add_patch(FancyBboxPatch((tx - tw / 2, ty - th / 2), tw, th, boxstyle="round,pad=0.03", fc="#F4F4F4", ec="k",
                                lw=1.1))
    ax.text(tx, ty + 0.44, "TIDE", ha="center", va="center", fontsize=10, weight="bold")
    ax.text(tx - 0.56, ty - 0.02, "Management\nclock, phases,\nexecution order,\ndeadlines, replay", ha="center",
            va="center", fontsize=FS, linespacing=1.15)
    ax.text(tx + 0.50, ty - 0.02, "Messaging\nvalue + validity\n+ meaning + quality;\nalignment, history",
            ha="center", va="center", fontsize=FS, linespacing=1.15)
    ax.plot([tx - 0.04, tx - 0.04], [ty - 0.30, ty + 0.27], color="0.5", lw=0.6)
    ax.text(tx, ty - 0.48, "trace  ·  records  ·  results", ha="center", va="center", fontsize=FS, color="0.3")
    # component centre, name, label, label position and alignment
    comps = [
        ((2.98, 2.70), names[0], r"$P$: $\mathtt{average}$, $[t-h,\,t)$", (x0 + 0.03, 3.01), "left"),
        ((6.02, 2.70), names[1], r"$P_\mathrm{bat}$: $\mathtt{average}$, $[t,\,t+h)$", (6.47, 3.01), "right"),
        ((2.87, 1.30), names[2], r"price: $\mathtt{hold}$", (2.87, 0.98), "center"),
        ((6.13, 1.30), names[3], "reads 24 h history", (6.47, 0.98), "right"),
        ((tx, 0.30), names[4], r"set-point: $\mathtt{hold}$," "\n" r"async, bounded", (tx + bw / 2 + 0.10, 0.30),
         "left"),
    ]
    for (x, y), n, lab, (lx, ly), ha in comps:
        box(ax, x, y, bw, bh, n, fc="#EAF2FA", ec=BLUE)
        p = edge(x, y, bw + 0.04, bh + 0.04, tx, ty)
        q = edge(tx, ty, tw + 0.06, th + 0.06, x, y)
        arrow(ax, p, q, color=GREEN, rad=0.18)               # publish
        arrow(ax, q, p, color=VERMILION, ls="--", rad=0.18)  # subscribe
        ax.text(lx, ly, lab, ha=ha, va="center", fontsize=FS, color="0.15", linespacing=1.15)
    handles = [Line2D([], [], color=GREEN, label="publish"),
               Line2D([], [], color=VERMILION, ls="--", label="subscribe (aligned)")]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(x0 / W, 0.0), fontsize=FS, frameon=False,
              borderaxespad=0.1)
    save_paper_figure(fig, "fig01_concept.png")
    plt.close(fig)


def fig_time_meaning() -> None:
    fig = plt.figure(figsize=(TEXT_WIDTH_IN, 3.3))
    ax = fig.add_axes([0.0, 0.14, 1.0, 0.85])
    t = [0, 15, 30, 45, 60]  # minutes after 10:00
    rows = {"power": 3.0, "price": 2.0, "temp": 1.0, "consumer": 0.0}
    xl, xr = -1.2, 61.5  # anchors of the row labels (right-aligned) and of the read results (left-aligned)
    # average (past) power: bars over [t-h, t) published at t
    vals = [42, 55, 61, 48]
    base = rows["power"] - 0.35
    for i, v in enumerate(vals):
        a, b = t[i], t[i + 1]
        y = base + 0.006 * v
        ax.add_patch(Rectangle((a, base), b - a, 0.006 * v, fc="#D6E6F4", ec=BLUE, lw=0.8))
        ax.annotate("", xy=(b, rows["power"] + 0.45), xytext=(b, y),
                    arrowprops=dict(arrowstyle="-|>", color=BLUE, lw=0.7))
        ax.text((a + b) / 2, base + 0.13, f"{v} kW", ha="center", va="center", fontsize=FS)
    ax.text(14.2, rows["power"] + 0.27, "published at the end\nof its interval", ha="right", va="center",
            fontsize=FS, color=BLUE, linespacing=1.1)
    ax.text(xl, rows["power"], "EnergyPlus power\n" r"kind $\mathtt{average}$ (past)", ha="right", va="center",
            fontsize=FS)
    # hold price: steps from publication time
    prices = [0.10, 0.10, 0.30, 0.30, 0.30]
    for i in range(4):
        y = rows["price"] - 0.3 + (0.5 if prices[i] > 0.2 else 0.15)
        ax.plot([t[i], t[i + 1]], [y, y], color=ORANGE, lw=1.6)
        ax.plot(t[i], y, "o", color=ORANGE, ms=3)
        ax.text(t[i] + 7.5, y + 0.1, f"{prices[i]:.2f} $/kWh", ha="center", va="bottom", fontsize=FS)
    ax.text(xl, rows["price"], "Tariff price\n" r"kind $\mathtt{hold}$", ha="right", va="center", fontsize=FS)
    # instant temperature: samples with linear interpolation
    temps = [24.2, 24.6, 23.1, 22.4, 22.6]
    ys = [rows["temp"] - 0.3 + 0.25 * (v - 22) for v in temps]
    ax.plot(t, ys, color=GREEN, lw=0.9, ls="--")
    ax.plot(t, ys, "o", color=GREEN, ms=3.5)
    ax.text(xl, rows["temp"], "Outdoor temperature\n" r"kind $\mathtt{instant}$", ha="right", va="center",
            fontsize=FS)
    # consumer windows at t_k = 10:30 with h = 15 min
    tk = 30
    y0 = rows["consumer"]
    ax.add_patch(Rectangle((tk - 15, y0 - 0.3), 15, 0.6, fc="#F3D3C1", ec=VERMILION, lw=0.8))
    ax.text(tk - 7.5, y0, r"$\mathtt{past}$" "\n" r"$[t_k-h,\,t_k)$", ha="center", va="center", fontsize=FS)
    ax.add_patch(Rectangle((tk, y0 - 0.3), 15, 0.6, fc="#E9DCEB", ec=PURPLE, lw=0.8))
    ax.text(tk + 7.5, y0, r"$\mathtt{ahead}$" "\n" r"$[t_k,\,t_k+h)$", ha="center", va="center", fontsize=FS)
    ax.plot([tk, tk], [y0 - 0.42, 3.62], color="k", lw=0.8, ls=":")
    ax.text(tk, 3.78, r"Consumer with step $h$ = 15 min at $t_k$ = 10:30; read mode $\mathtt{at}$ takes the value "
            r"in effect at $t_k$", ha="center", va="center", fontsize=FS)
    ax.text(xl, y0, "Consumer windows", ha="right", va="center", fontsize=FS)
    # what the consumer reads
    ax.text(xr, rows["power"], r"$\mathtt{past}$ → 55 kW" "\n(10:15–10:30)", fontsize=FS, va="center", color=BLUE)
    ax.text(xr, rows["price"], r"$\mathtt{past}$ → 0.10 \$/kWh" "\n" r"$\mathtt{latest}$ → 0.30 \$/kWh",
            fontsize=FS, va="center", color=ORANGE)
    ax.text(xr, rows["temp"], r"$\mathtt{past}$ → 23.85 °C" "\n(trapezoid)", fontsize=FS, va="center", color=GREEN)
    ax.set_xlim(-17, 78)
    ax.set_ylim(-0.45, 3.95)
    ax.set_xticks(t)
    ax.set_xticklabels(["10:00", "10:15", "10:30", "10:45", "11:00"])
    ax.set_yticks([])
    ax.grid(False)
    for s in ("left", "right", "top"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_bounds(0, 60)
    ax.set_xlabel("Time")
    save_paper_figure(fig, "fig02_time_meaning.png")
    plt.close(fig)


if __name__ == "__main__":
    paper_style()
    fig_concept()
    fig_time_meaning()
