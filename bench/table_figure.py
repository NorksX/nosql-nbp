"""The five queries where the comparison actually turns, as a table.

    python -m bench.table_figure

Слика 13 plots all ten; this reads out the five that decide the verdict — the
two complex queries the relational model wins outright and the three aggregates
it loses. Every key-value column is that database with its better model, the
same rule that figure uses. Values are p50 in milliseconds from
bench/results/*.csv.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
RULE = "#dedcd3"
BAND = "#f2efe8"

C_ORACLE = "#2a78d6"
C_FDB = "#eb6834"
C_PG = "#c0398e"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})

#: (label, oracle, foundationdb, postgres-R). The winner is simply the minimum.
ROWS = [
    ("група", "R победува · сложени прашања", None, None),
    ("5 · два жанра", 161.85, 131.62, 1.59),
    ("7 · ±1 година", 289.59, 157.43, 2.10),
    ("група", "key-value победува · агрегати", None, None),
    ("8 · жанр × година", 9.93, 9.03, 17.90),
    ("9 · топ-10 јазици", 3.01, 4.03, 8.43),
    ("10 · годишен тренд", 1.24, 2.00, 8.14),
]

COLS = [(0.50, "Oracle NoSQL", C_ORACLE),
        (0.70, "FoundationDB", C_FDB),
        (0.93, "PostgreSQL R", C_PG)]


def mk(value):
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


fig, ax = plt.subplots(figsize=(8.26, 4.35))
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.axis("off")

ROW_H = 0.115          # height of a value row's band
ROW_GAP = 0.016        # between two bands of the same group
GROUP_GAP = 0.055      # above a group label
LABEL_GAP = 0.030      # between a group label and its first band

# column headers, each with its series swatch
HEAD = 0.965
for x, label, colour in COLS:
    ax.plot([x - 0.125], [HEAD], marker="s", markersize=6.5,
            color=colour, clip_on=False)
    ax.text(x - 0.105, HEAD, label, va="center", ha="left",
            fontsize=10, color=INK_2)
ax.plot([0, 1], [HEAD - 0.045] * 2, color=INK, linewidth=1.0)

y = HEAD - 0.045       # y of the *top edge* of whatever comes next
for label, a, b, c in ROWS:
    if label == "група":
        y -= GROUP_GAP
        ax.text(0.005, y, a.upper(), va="top", ha="left",
                fontsize=8.5, color=MUTED)
        y -= LABEL_GAP
        continue

    values = [a, b, c]
    best = min(values)
    mid = y - ROW_H / 2
    ax.add_patch(plt.Rectangle((0, y - ROW_H), 1, ROW_H,
                               facecolor=BAND, edgecolor="none", zorder=0))
    ax.text(0.005, mid, label, va="center", ha="left", fontsize=11.5, color=INK)
    for (x, _, colour), value in zip(COLS, values):
        won = value == best
        ax.text(x, mid, mk(value), va="center", ha="right",
                fontsize=13.5 if won else 12,
                color=colour if won else MUTED,
                fontweight="bold" if won else "normal")
    y -= ROW_H + ROW_GAP

ax.text(1.0, y - 0.02, "p50, ms · подебелената вредност е победникот во редот",
        va="top", ha="right", fontsize=8.5, color=MUTED)

fig.subplots_adjust(left=0.01, right=0.99, top=1, bottom=0)
OUT = Path(__file__).resolve().parent / "plots" / "fig17_model_r_table.png"
OUT.parent.mkdir(exist_ok=True)
fig.savefig(OUT, dpi=300, bbox_inches="tight", pad_inches=0.06)
print(f"  {OUT}")
