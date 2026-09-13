"""Generate the Phase 4 figures for the елаборат from bench/results/*.csv.

    python -m bench.plots

Run from the repository root on the host — like bench/report.py it only reads
bench/results/*.csv, so it needs neither database. Writes 300 dpi PNGs (for
Word) to bench/plots/.

Figures produced (numbering follows the елаборат):
  fig7_load_time      — Време на вчитување по модел и база
  fig8_latency_p50    — Латенција по прашање, модел и база (p50, log скала)
  fig9_aggregates_l1  — Агрегатни прашања врз модел L1
  fig10_concurrency   — Пропусност при 1 / 4 / 16 конкурентни клиенти
  fig11_cpus          — 1 наспроти 4 процесори

Every figure draws one series per database that has a results CSV, so the set
renders with two databases or with three. A database whose CSV is missing is
reported on stdout and left out rather than crashing the run.

Load times are not in the CSVs (the harness measures queries only); they are
the measured values from docs/schema-comparison.md §4.1. Update LOAD_SECONDS
when the load is re-run — including the PostgreSQL entries, which start as None
and must be filled in from that database's own `common.live_check` output.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows console defaults to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MplPath

RESULTS_DIR = Path(__file__).resolve().parent / "results"
OUT_DIR = Path(__file__).resolve().parent / "plots"

# docs/schema-comparison.md §4.1 — measured wall-clock load time, seconds.
# None means "not measured yet"; that database is simply left out of Слика 7.
# Measured 2026-09-13 on macOS 15 / Apple Silicon, Docker 29.3.1, by
# `common.live_check` on the real data/ corpus — all three databases in one
# sitting, which is the only way these are comparable. The earlier Oracle NoSQL
# and FoundationDB values (83.3 / 6.9 and 16.0 / 0.5) came from a different
# machine and were replaced rather than mixed with the PostgreSQL run.
LOAD_SECONDS = {
    ("oracle-nosql", "L1"): 56.8,
    ("oracle-nosql", "L2"): 4.7,
    ("foundationdb", "L1"): 6.8,
    ("foundationdb", "L2"): 0.4,
    ("postgresql", "L1"): 2.9,
    ("postgresql", "L2"): 1.2,
}

ALL_DATABASES = [
    ("oracle-nosql", "Oracle NoSQL"),
    ("foundationdb", "FoundationDB"),
    ("postgresql", "PostgreSQL"),
]

# Short Macedonian query labels, matching Табела 5 in the елаборат.
QUERY_LABELS = {
    "q1": "Точкесто барање по TMDB id",
    "q2": "Барање по IMDb id",
    "q3": "Сите филмови од 2017",
    "q4": "Јазик „en“, vote_count > 500",
    "q5": "Drama и Horror",
    "q6": "Топ 20 Drama по популарност",
    "q7": "Ист јазик, ±1 год. (Ad Astra)",
    "q8": "Оценка и број по жанр/година",
    "q9": "Топ 10 јазици",
    "q10": "Годишен тренд, vote_count ≥ 50",
}

# Palette — series colors follow the database everywhere, in a fixed order that
# never changes when a database is missing from a figure.
#
# The third hue was chosen by running the pair through a colour-vision check
# rather than by eye: against #2a78d6 and #eb6834, #c0398e keeps a separation of
# ΔE 13 under protanopia and 11 under tritanopia and stays above 3:1 contrast on
# this surface, where the obvious green choice drops to ΔE 7 against the orange
# and would have depended on the value labels to stay readable in print.
C_ORACLE = "#2a78d6"
C_FDB = "#eb6834"
C_PG = "#c0398e"
COLORS = {"oracle-nosql": C_ORACLE, "foundationdb": C_FDB, "postgresql": C_PG}
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "text.color": INK,
        "axes.edgecolor": BASELINE,
        "axes.labelcolor": INK_2,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.linewidth": 0.8,
        "svg.fonttype": "none",
    }
)


def mk_num(value: float, decimals: int = 1) -> str:
    """Macedonian number style: decimal comma, thin space thousands (9 525)."""
    if value >= 1000:
        text = f"{value:,.0f}".replace(",", " ")
    else:
        text = f"{value:.{decimals}f}".rstrip("0").rstrip(".").replace(".", ",")
    return text


def fmt_ms(value: float) -> str:
    if value < 10:
        return mk_num(value, 2)
    return mk_num(round(value))


def available() -> list[tuple[str, str]]:
    """The databases that have a latency CSV, in the fixed series order."""
    present = []
    for slug, label in ALL_DATABASES:
        if (RESULTS_DIR / f"{slug}.csv").exists():
            present.append((slug, label))
        else:
            print(f"  (нема {slug}.csv — {label} се изостава од графиците)")
    if len(present) < 2:
        raise SystemExit("потребни се резултати од барем две бази")
    return present


def lanes(n: int, pitch: float) -> list[float]:
    """Symmetric offsets for ``n`` series in one group, centred on the tick."""
    return [(i - (n - 1) / 2) * pitch for i in range(n)]


def load_rows(databases: list[tuple[str, str]]) -> dict:
    rows = {}
    for slug, _ in databases:
        with open(RESULTS_DIR / f"{slug}.csv", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rows[(row["database"], row["query"], row["model"])] = {
                    "p50": float(row["p50_ms"]),
                    "scan": row["full_scan"] == "True",
                }
    return rows


def strip_chrome(ax, y_grid: bool = True) -> None:
    for side in ("top", "right", "left" if y_grid else "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    if y_grid:
        ax.grid(axis="y", color=GRID, linewidth=0.8)
    else:
        ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def rounded_bar(ax, x: float, height: float, width: float, color: str) -> None:
    """Bar with a rounded data-end and a square baseline (4px cap at 300 dpi)."""
    x0, x1 = x - width / 2, x + width / 2
    trans = ax.transData.inverted()
    origin = ax.transData.transform((x0, 0))
    r = trans.transform(origin + 4.5) - trans.transform(origin)  # ≈4px in data units
    rx, ry = abs(r[0]), abs(r[1])
    rx, ry = min(rx, width / 2), min(ry, height / 2)
    verts = [
        (x0, 0), (x0, height - ry), (x0, height), (x0 + rx, height),
        (x1 - rx, height), (x1, height), (x1, height - ry), (x1, 0), (x0, 0),
    ]
    codes = [
        MplPath.MOVETO, MplPath.LINETO, MplPath.CURVE3, MplPath.CURVE3,
        MplPath.LINETO, MplPath.CURVE3, MplPath.CURVE3, MplPath.LINETO,
        MplPath.CLOSEPOLY,
    ]
    ax.add_patch(PathPatch(MplPath(verts, codes), facecolor=color, edgecolor="none"))


def save(fig, name: str) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    fig.savefig(OUT_DIR / f"{name}.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  {OUT_DIR / name}.png")


def fig7_load_time(databases: list[tuple[str, str]]) -> None:
    """Слика 7 — Време на вчитување по модел и база."""
    series = [(slug, label) for slug, label in databases
              if LOAD_SECONDS.get((slug, "L1")) is not None]
    missing = [label for slug, label in databases if (slug, label) not in series]
    for label in missing:
        print(f"  (нема измерено време на вчитување за {label} — "
              f"пополни LOAD_SECONDS во bench/plots.py)")
    if len(series) < 2:
        print("  (премалку измерени времиња — прескокнувам Слика 7)")
        return

    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    groups = ["L1", "L2"]
    offsets = lanes(len(series), 0.18)
    top = max(LOAD_SECONDS[(slug, m)] for slug, _ in series for m in groups)
    ax.set_xlim(-0.5, 1.5)
    ax.set_ylim(0, top * 1.15)
    for gi, model in enumerate(groups):
        for (slug, _), off in zip(series, offsets):
            secs = LOAD_SECONDS[(slug, model)]
            x = gi + off
            rounded_bar(ax, x, secs, 0.11, COLORS[slug])
            ax.text(x, secs + top * 0.022, f"{mk_num(secs)} s", ha="center",
                    va="bottom", fontsize=9, color=INK)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([f"Модел {m}" for m in groups], fontsize=10, color=INK_2)
    ax.set_ylabel("Време на вчитување (секунди)", fontsize=9)
    strip_chrome(ax)
    ax.legend(
        handles=[
            Line2D([], [], marker="s", linestyle="", markersize=9,
                   markerfacecolor=COLORS[slug], markeredgecolor="none", label=label)
            for slug, label in series
        ],
        loc="upper right", frameon=False, fontsize=9, labelcolor=INK_2,
    )
    save(fig, "fig7_load_time")


def fig8_latency_p50(rows: dict, databases: list[tuple[str, str]]) -> None:
    """Слика 8 — Латенција по прашање, модел и база (p50, log скала).

    Two panels (Модел L1 | Модел L2), one horizontal bar per database per query
    on a shared log axis; hollow bars mark paths that degrade to a full scan.
    """
    n = len(databases)
    pitch = 0.26 if n == 3 else 0.36
    offsets = lanes(n, pitch)
    height = 0.22 if n == 3 else 0.3
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 6.4 if n == 3 else 5.8), sharey=True)
    left = 0.09  # bars grow from the axis minimum on the log scale
    biggest = max(r["p50"] for r in rows.values())

    for ax, model in zip(axes, ("L1", "L2")):
        for qi in range(1, 11):
            for (slug, _), off in zip(databases, offsets):
                r = rows[(slug, f"q{qi}", model)]
                y = qi + off
                if r["scan"]:  # hollow = no access path, degrades to full scan
                    ax.barh(y, r["p50"] - left, left=left, height=height,
                            facecolor=SURFACE, edgecolor=COLORS[slug],
                            linewidth=1.1, zorder=2)
                else:
                    ax.barh(y, r["p50"] - left, left=left, height=height,
                            facecolor=COLORS[slug], edgecolor="none", zorder=2)
                ax.text(r["p50"] * 1.25, y, fmt_ms(r["p50"]), va="center",
                        ha="left", fontsize=7 if n == 3 else 7.5, color=INK_2)
        ax.set_xscale("log")
        ax.set_xlim(left, biggest * 9)  # headroom so the longest labels fit
        ticks = [t for t in (0.1, 1, 10, 100, 1000, 10000) if t <= biggest * 9]
        ax.set_xticks(ticks)
        ax.set_xticklabels([mk_num(t) for t in ticks], fontsize=8.5)
        ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.set_title(f"Модел {model}", fontsize=10.5, color=INK, pad=10)
        strip_chrome(ax, y_grid=False)

    axes[0].set_ylim(10.75, 0.25)  # q1 on top
    axes[0].set_yticks(range(1, 11))
    axes[0].set_yticklabels(
        [f"{i}. {QUERY_LABELS[f'q{i}']}" for i in range(1, 11)],
        fontsize=9, color=INK_2,
    )
    fig.supxlabel("Латенција p50 (ms, логаритамска скала)", fontsize=9,
                  color=INK_2, y=0.015)
    fig.legend(
        handles=[Patch(facecolor=COLORS[slug], edgecolor="none", label=label)
                 for slug, label in databases]
        + [Patch(facecolor=SURFACE, edgecolor=MUTED, linewidth=1.3,
                 label="целосно скенирање (нема пристапен пат)")],
        loc="upper center", bbox_to_anchor=(0.5, 1.05), ncol=n + 1,
        frameon=False, fontsize=9, labelcolor=INK_2,
    )
    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    save(fig, "fig8_latency_p50")


def fig9_aggregates_l1(rows: dict, databases: list[tuple[str, str]]) -> None:
    """Слика 9 — Агрегатни прашања врз модел L1.

    On both key-value stores all three of these degrade to a full scan. Whether
    that is also true of the relational engine is the point of the figure, so
    the bars that are *not* a scan are drawn hollow-free and the caption in the
    елаборат says which is which.
    """
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    queries = ["q8", "q9", "q10"]
    labels = ["8. Жанр по година", "9. Топ 10 јазици", "10. Годишен тренд"]
    offsets = lanes(len(databases), 0.18)
    top = max(rows[(slug, q, "L1")]["p50"] for slug, _ in databases for q in queries)
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(0, top * 1.18)
    for gi, qid in enumerate(queries):
        for (slug, _), off in zip(databases, offsets):
            ms = rows[(slug, qid, "L1")]["p50"]
            x = gi + off
            rounded_bar(ax, x, ms, 0.11, COLORS[slug])
            ax.text(x, ms + top * 0.02, mk_num(round(ms)), ha="center", va="bottom",
                    fontsize=9, color=INK)
    ax.set_xticks(range(len(queries)))
    ax.set_xticklabels(labels, fontsize=10, color=INK_2)
    ax.set_ylabel("Латенција p50 (ms)", fontsize=9)
    strip_chrome(ax)
    ax.legend(
        handles=[
            Line2D([], [], marker="s", linestyle="", markersize=9,
                   markerfacecolor=COLORS[slug], markeredgecolor="none", label=label)
            for slug, label in databases
        ],
        loc="lower center", bbox_to_anchor=(0.5, 1.01), ncol=len(databases),
        frameon=False, fontsize=9, labelcolor=INK_2,
    )
    save(fig, "fig9_aggregates_l1")


def load_concurrency(databases: list[tuple[str, str]]) -> tuple[list[dict], list] | None:
    """Rows from concurrency-<db>.csv, plus the databases that had one."""
    rows: list[dict] = []
    present = []
    for slug, label in databases:
        path = RESULTS_DIR / f"concurrency-{slug}.csv"
        if not path.exists():
            continue
        present.append((slug, label))
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                row["threads"] = int(row["threads"])
                row["throughput_ops_s"] = float(row["throughput_ops_s"])
                row["p50_ms"] = float(row["p50_ms"])
                rows.append(row)
    return (rows, present) if len(present) >= 2 else None


CONCURRENCY_TITLES = {
    "q1": "1. Точкесто барање (L1)",
    "q3": "3. Сите филмови од 2017 (L2)",
    "q6": "6. Топ 20 Drama (L2)",
    "q8": "8. Агрегат жанр/година (L2)",
}


def fig10_concurrency(conc: list[dict], databases: list[tuple[str, str]]) -> None:
    """Слика 10 — Пропусност при 1 / 4 / 16 конкурентни клиенти."""
    fig, axes = plt.subplots(2, 2, figsize=(9.2, 6.2))
    levels = [1, 4, 16]
    xpos = {1: 0, 4: 1, 16: 2}

    for ax, qid in zip(axes.flat, CONCURRENCY_TITLES):
        series = {}
        for slug, _ in databases:
            pts = sorted(
                (r for r in conc if r["database"] == slug and r["query"] == qid),
                key=lambda r: r["threads"],
            )
            xs = [xpos[r["threads"]] for r in pts]
            ys = [r["throughput_ops_s"] for r in pts]
            series[slug] = dict(zip(xs, ys))
            ax.plot(xs, ys, color=COLORS[slug], linewidth=2, marker="o",
                    markersize=8, markeredgecolor=SURFACE, markeredgewidth=1.6,
                    solid_capstyle="round", zorder=3)
        # Lines run close together, so a label is placed by the point's rank at
        # that x: highest above, lowest below, anything in between to the side.
        floor = ax.get_ylim()[1] * 0.15  # a below-label here would hit the axis
        for x in range(3):
            column = sorted(
                ((slug, series[slug][x]) for slug, _ in databases if x in series[slug]),
                key=lambda p: -p[1],
            )
            for rank, (slug, y) in enumerate(column):
                if rank == 0:
                    offset, ha, va = (0, 9), "center", "bottom"
                elif rank == len(column) - 1 and y >= floor:
                    offset, ha, va = (0, -9), "center", "top"
                else:
                    offset, ha, va = (11, -4), "left", "center"
                ax.annotate(mk_num(round(y) if y >= 100 else y), (x, y),
                            textcoords="offset points", xytext=offset,
                            ha=ha, va=va, fontsize=8, color=INK_2)
        ax.set_title(CONCURRENCY_TITLES[qid], fontsize=10, color=INK, pad=8)
        ax.set_xticks(range(3))
        ax.set_xticklabels([str(l) for l in levels], fontsize=9)
        ax.set_xlim(-0.35, 2.35)
        ax.set_ylim(0, ax.get_ylim()[1] * 1.22)  # air for the point labels
        strip_chrome(ax)
        ax.tick_params(axis="y", labelsize=8)

    for ax in axes[1]:
        ax.set_xlabel("Конкурентни клиенти", fontsize=9)
    for ax in axes[:, 0]:
        ax.set_ylabel("Пропусност (барања/s)", fontsize=9)
    fig.legend(
        handles=[
            Line2D([], [], color=COLORS[slug], linewidth=2, marker="o",
                   markersize=7, markeredgecolor=SURFACE, label=label)
            for slug, label in databases
        ],
        loc="upper center", bbox_to_anchor=(0.5, 1.03), ncol=len(databases),
        frameon=False, fontsize=9.5, labelcolor=INK_2,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    save(fig, "fig10_concurrency")


def load_cpu_data(databases: list[tuple[str, str]]) -> tuple[dict, list] | None:
    """p50 per CPU count (harness runs) + 16-client throughput (sweeps)."""
    data = {"latency": {}, "throughput": {}}
    present = []
    for slug, label in databases:
        paths = [
            (RESULTS_DIR / f"{slug}-cpus{c}.csv", RESULTS_DIR / f"concurrency-{slug}-cpus{c}.csv")
            for c in (1, 4)
        ]
        if not all(h.exists() and c.exists() for h, c in paths):
            continue
        present.append((slug, label))
        for cpus, (hpath, cpath) in zip((1, 4), paths):
            with open(hpath, encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    data["latency"][(slug, cpus, row["query"], row["model"])] = float(row["p50_ms"])
            with open(cpath, encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if row["threads"] == "16":
                        data["throughput"][(slug, cpus, row["query"])] = float(row["throughput_ops_s"])
    return (data, present) if len(present) >= 2 else None


def fig11_cpus(cpu: dict, databases: list[tuple[str, str]]) -> None:
    """Слика 11 — 1 наспроти 4 процесори: едно барање vs 16 клиенти."""
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(9.6, 4.4))
    combos = [(slug, cpus) for slug, _ in databases for cpus in (1, 4)]
    labels = [label for _, label in databases for _ in (1, 4)]
    pitch = 0.16 if len(combos) <= 4 else 0.115
    offsets = lanes(len(combos), pitch)
    width = pitch * 0.8

    def bars(ax, groups, value_of, fmt):
        top = 0.0
        for gi, group in enumerate(groups):
            for (slug, cpus), off in zip(combos, offsets):
                v = value_of(slug, cpus, group)
                top = max(top, v)
                ax.bar(gi + off, v, width=width, color=COLORS[slug],
                       alpha=0.45 if cpus == 1 else 1.0, edgecolor="none",
                       zorder=2)
                ax.annotate(fmt(v), (gi + off, v), textcoords="offset points",
                            xytext=(0, 3), ha="center", va="bottom",
                            fontsize=6.5 if len(combos) > 4 else 7.5,
                            color=INK_2, rotation=90 if len(combos) > 4 else 0)
        return top

    # (а) the heaviest single-query paths: the three L1 aggregate scans
    agg = ["q8", "q9", "q10"]
    top = bars(ax_a, agg,
               lambda s, c, q: cpu["latency"][(s, c, q, "L1")],
               lambda v: mk_num(round(v)))
    ax_a.set_title("Едно барање — агрегати врз L1 (p50, ms)",
                   fontsize=10, color=INK, pad=10)
    ax_a.set_xticks(range(len(agg)))
    ax_a.set_xticklabels(["8. Жанр/година", "9. Јазици", "10. Тренд"],
                         fontsize=9, color=INK_2)
    ax_a.set_ylim(0, top * (1.35 if len(combos) > 4 else 1.15))
    ax_a.set_ylabel("Латенција p50 (ms)", fontsize=9)
    strip_chrome(ax_a)

    # (б) throughput at 16 concurrent clients
    conc_q = ["q1", "q8"]
    top = bars(ax_b, conc_q,
               lambda s, c, q: cpu["throughput"][(s, c, q)],
               lambda v: mk_num(round(v)))
    ax_b.set_title("16 конкурентни клиенти (барања/s)",
                   fontsize=10, color=INK, pad=10)
    ax_b.set_xticks(range(len(conc_q)))
    ax_b.set_xticklabels(["1. Точкесто барање (L1)", "8. Агрегат (L2)"],
                         fontsize=9, color=INK_2)
    ax_b.set_yscale("log")
    ax_b.set_ylim(10, top * 12)
    ticks = [t for t in (10, 100, 1000, 10000, 100000) if t <= top * 12]
    ax_b.set_yticks(ticks)
    ax_b.set_yticklabels([mk_num(t) for t in ticks])
    ax_b.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax_b.set_ylabel("Пропусност (барања/s, log)", fontsize=9)
    strip_chrome(ax_b)

    fig.legend(
        handles=[
            Patch(facecolor=COLORS[slug], alpha=0.45 if cpus == 1 else 1.0,
                  label=f"{label} · {cpus} CPU")
            for (slug, cpus), label in zip(combos, labels)
        ],
        loc="upper center", bbox_to_anchor=(0.5, 1.07), ncol=len(combos),
        frameon=False, fontsize=8.5, labelcolor=INK_2, handletextpad=0.5,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    save(fig, "fig11_cpus")


def main() -> int:
    print("Пишувам графици во", OUT_DIR)
    databases = available()
    print("  бази:", ", ".join(label for _, label in databases))
    rows = load_rows(databases)
    fig7_load_time(databases)
    fig8_latency_p50(rows, databases)
    fig9_aggregates_l1(rows, databases)
    conc = load_concurrency(databases)
    if conc:
        fig10_concurrency(*conc)
    else:
        print("  (нема доволно concurrency-*.csv — прескокнувам Слика 10)")
    cpu = load_cpu_data(databases)
    if cpu:
        fig11_cpus(*cpu)
    else:
        print("  (нема доволно *-cpus{1,4}.csv — прескокнувам Слика 11)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
