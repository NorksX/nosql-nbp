"""Draw the model R schema — for the presentation, not for the елаборат.

    python -m bench.schema_figure

The four tables of common/keyspec.py POSTGRES_R_DDL, their keys and their
foreign keys, in the palette bench/plots.py uses. Row counts are the ones
common/live_check.py reported for the loaded corpus; unlike the other figures
nothing here is read from bench/results/, because a schema is not a
measurement.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
CARD = "#f2efe8"
LINE = "#c3c2b7"

C_DARK = "#17262e"
C_TEAL = "#26695a"
C_ORANGE = "#c0562a"
C_MAGENTA = "#c0398e"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})

HEAD = 0.175          # header band, in axis units
ROW = 0.112           # one column line

fig, ax = plt.subplots(figsize=(12.0, 2.62))
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.axis("off")


def table(x, w, title, count, fields, colour, note=None):
    """Draw one table box centred on y = 0.52; returns its bounds."""
    h = HEAD + ROW * len(fields) + 0.05
    top = 0.52 + h / 2
    bottom = top - h

    ax.add_patch(FancyBboxPatch(
        (x, bottom), w, h, boxstyle="round,pad=0,rounding_size=0.006",
        facecolor=CARD, edgecolor=colour, linewidth=1.1, zorder=2))
    ax.add_patch(FancyBboxPatch(
        (x, top - HEAD), w, HEAD, boxstyle="round,pad=0,rounding_size=0.006",
        facecolor=colour, edgecolor=colour, linewidth=1.1, zorder=3))

    ax.text(x + 0.012, top - HEAD / 2, title, va="center", ha="left",
            fontsize=11.5, color="#ffffff", family="monospace", zorder=4)
    ax.text(x + w - 0.012, top - HEAD / 2, count, va="center", ha="right",
            fontsize=9.5, color="#ffffff", alpha=0.85, zorder=4)

    y = top - HEAD - ROW * 0.62
    for name, tag in fields:
        ax.text(x + 0.012, y, name, va="center", ha="left", fontsize=9.5,
                color=INK, family="monospace", zorder=4)
        if tag:
            ax.text(x + w - 0.012, y, tag, va="center", ha="right",
                    fontsize=8, color=colour, zorder=4)
        y -= ROW

    if note:
        ax.text(x + 0.012, bottom - 0.055, note, va="top", ha="left",
                fontsize=8, color=MUTED, zorder=4)
    return x, x + w, top, bottom


LANG = table(
    0.005, 0.145, "languages", "137",
    [("lang_code", "PK")], C_ORANGE)

MOV = table(
    0.245, 0.235, "movies", "109 222",
    [("id", "PK"),
     ("id_imdb", "UNIQUE"),
     ("lang_code", "FK"),
     ("release_date", ""),
     ("year", "GENERATED"),
     ("popularity …", "+ 8 атрибути")], C_DARK)

MG = table(
    0.575, 0.205, "movie_genres", "149 484",
    [("movie_id", "PK · FK"),
     ("genre_id", "PK · FK")], C_MAGENTA,
    note="сложен примарен клуч — секој филм ↔ жанр точно еднаш")

GEN = table(
    0.855, 0.14, "genres", "19",
    [("genre_id", "PK"),
     ("name", "UNIQUE")], C_TEAL)


def link(a, b, label_left, label_right, y=0.52):
    """Horizontal connector between the right edge of a and the left edge of b."""
    x0, x1 = a[1], b[0]
    ax.plot([x0, x1], [y, y], color=LINE, linewidth=1.2, zorder=1)
    ax.text(x0 + 0.012, y + 0.035, label_left, fontsize=9, color=INK_2,
            ha="left", va="bottom", zorder=4)
    ax.text(x1 - 0.012, y + 0.035, label_right, fontsize=9, color=INK_2,
            ha="right", va="bottom", zorder=4)


link(LANG, MOV, "1", "∞")
link(MOV, MG, "1", "∞")
link(MG, GEN, "∞", "1")

fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
OUT = Path(__file__).resolve().parent / "plots" / "fig16_model_r_schema.png"
OUT.parent.mkdir(exist_ok=True)
fig.savefig(OUT, dpi=300, bbox_inches="tight", pad_inches=0.04)
print(f"  {OUT}")
