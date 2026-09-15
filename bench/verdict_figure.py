"""The four findings of the study, as the conclusion slide's body.

    python -m bench.verdict_figure


Two key-value models (L1, L2) on two key-value databases, against one
normalized relational model on PostgreSQL, over the same corpus and the same
ten queries. Each row is one finding and the measurement behind it.

Drawn on the conclusion slide's own dark ground so it reads as part of the
slide rather than as a pasted picture. Numbers come from bench/results/*.csv,
each key-value database at its better model — the same rule Слика 13 uses:

  11 203×   Oracle NoSQL, query 1: L1 0,59 ms against L2 6 609,64 ms
  7 / 10    queries 1–7 go to model R, 8–10 to L2
  83×       query 5: FoundationDB L1 131,62 ms against R 1,59 ms
  2–7×      queries 8, 9, 10: L2 against R
  31×       query 1 at 16 clients: 220 436 against 7 002 requests/s
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

GROUND = "#13262d"          # the conclusion slide's background
PAPER = "#f4f2ec"
DIM = "#8fa3ab"
RULE = "#243a42"

C_TEAL = "#3e9d84"
C_ORANGE = "#eb6834"
C_MAGENTA = "#d4519f"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
    "figure.facecolor": GROUND,
    "axes.facecolor": GROUND,
    "savefig.facecolor": GROUND,
})

#: (number, colour, finding, the measurement behind it)
FINDINGS = [
    ("01", C_TEAL,
     "Во неструктурирана база моделот е планот",
     "нема оптимизатор — 11 203× за погрешен модел"),
    ("02", C_ORANGE,
     "Релациониот модел беше побрз на 7 од 10 прашања",
     "до 83×, наспроти подобриот од L1 и L2"),
    ("03", C_MAGENTA,
     "Трите што ги губи се веќе пресметани во L2",
     "2–7× — победува претпресметката, не базата"),
    ("04", C_TEAL,
     "Под оптоварување разликата расте",
     "220 436 наспроти 7 002 барања/s при 16 клиенти"),
]

W, H = 11.77, 2.45
ROW = H / len(FINDINGS)

fig, ax = plt.subplots(figsize=(W, H))
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")

for i, (number, colour, finding, evidence) in enumerate(FINDINGS):
    y = H - (i + 0.5) * ROW
    if i:
        ax.plot([0.02, W - 0.02], [y + ROW / 2] * 2, color=RULE, linewidth=0.9)
    ax.text(0.04, y, number, va="center", ha="left",
            fontsize=13, color=colour, fontweight="bold")
    ax.text(0.72, y, finding, va="center", ha="left",
            fontsize=13.5, color=PAPER)
    ax.text(W - 0.04, y, evidence, va="center", ha="right",
            fontsize=11, color=DIM)

fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
OUT = Path(__file__).resolve().parent / "plots" / "fig18_verdict.png"
OUT.parent.mkdir(exist_ok=True)
fig.savefig(OUT, dpi=300, facecolor=GROUND)
print(f"  {OUT}")
