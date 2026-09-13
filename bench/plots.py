"""Generate the Phase 4 figures for the елаборат from bench/results/*.csv.

    python -m bench.plots

Run from the repository root on the host — like bench/report.py it only reads
bench/results/*.csv, so it needs neither database. Writes 300 dpi PNGs (for
Word) to bench/plots/.

Figures produced (numbering follows the елаборат):
  fig7_load_time      — Време на вчитување по модел и база
  fig8_latency_p50    — Латенција по прашање, модел и база (p50, log скала)
  fig9_aggregates_l1  — Агрегатни прашања врз модел L1

Load times are not in the CSVs (the harness measures queries only); they are
the measured values from docs/schema-comparison.md §4.1. Update LOAD_SECONDS
when the load is re-run. The CPU-count and concurrency figures will be added
here once those sweeps produce CSVs.
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
LOAD_SECONDS = {
    ("oracle-nosql", "L1"): 83.3,
    ("oracle-nosql", "L2"): 6.9,
    ("foundationdb", "L1"): 16.0,
    ("foundationdb", "L2"): 0.5,
}

DATABASES = [("oracle-nosql", "Oracle NoSQL"), ("foundationdb", "FoundationDB")]

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

# Palette — series colors follow the database everywhere.
C_ORACLE = "#2a78d6"
C_FDB = "#eb6834"
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


def load_rows() -> dict:
    rows = {}
    for slug, _ in DATABASES:
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


def fig7_load_time() -> None:
    """Слика 7 — Време на вчитување по модел и база."""
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    groups = ["L1", "L2"]
    offsets = {"oracle-nosql": -0.09, "foundationdb": 0.09}
    colors = {"oracle-nosql": C_ORACLE, "foundationdb": C_FDB}
    ax.set_xlim(-0.5, 1.5)
    ax.set_ylim(0, 92)
    for gi, model in enumerate(groups):
        for slug, _ in DATABASES:
            secs = LOAD_SECONDS[(slug, model)]
            x = gi + offsets[slug]
            rounded_bar(ax, x, secs, 0.11, colors[slug])
            ax.text(x, secs + 1.8, f"{mk_num(secs)} s", ha="center",
                    va="bottom", fontsize=9, color=INK)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([f"Модел {m}" for m in groups], fontsize=10, color=INK_2)
    ax.set_yticks([0, 20, 40, 60, 80])
    ax.set_ylabel("Време на вчитување (секунди)", fontsize=9)
    strip_chrome(ax)
    ax.legend(
        handles=[
            Line2D([], [], marker="s", linestyle="", markersize=9,
                   markerfacecolor=colors[slug], markeredgecolor="none", label=label)
            for slug, label in DATABASES
        ],
        loc="upper right", frameon=False, fontsize=9, labelcolor=INK_2,
    )
    save(fig, "fig7_load_time")


def fig8_latency_p50(rows: dict) -> None:
    """Слика 8 — Латенција по прашање, модел и база (p50, log скала).

    Two panels (Модел L1 | Модел L2), horizontal bars Oracle vs FDB per query
    on a shared log axis; hollow bars mark paths that degrade to a full scan.
    """
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 5.8), sharey=True)
    colors = {"oracle-nosql": C_ORACLE, "foundationdb": C_FDB}
    lane = {"oracle-nosql": -0.18, "foundationdb": 0.18}
    left = 0.09  # bars grow from the axis minimum on the log scale

    for ax, model in zip(axes, ("L1", "L2")):
        for qi in range(1, 11):
            for slug, _ in DATABASES:
                r = rows[(slug, f"q{qi}", model)]
                y = qi + lane[slug]
                if r["scan"]:  # hollow = no access path, degrades to full scan
                    ax.barh(y, r["p50"] - left, left=left, height=0.3,
                            facecolor=SURFACE, edgecolor=colors[slug],
                            linewidth=1.3, zorder=2)
                else:
                    ax.barh(y, r["p50"] - left, left=left, height=0.3,
                            facecolor=colors[slug], edgecolor="none", zorder=2)
                ax.text(r["p50"] * 1.25, y, fmt_ms(r["p50"]), va="center",
                        ha="left", fontsize=7.5, color=INK_2)
        ax.set_xscale("log")
        ax.set_xlim(left, 90000)  # headroom so the longest value labels fit
        ticks = [0.1, 1, 10, 100, 1000, 10000]
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
        handles=[
            Patch(facecolor=C_ORACLE, edgecolor="none", label="Oracle NoSQL"),
            Patch(facecolor=C_FDB, edgecolor="none", label="FoundationDB"),
            Patch(facecolor=SURFACE, edgecolor=MUTED, linewidth=1.3,
                  label="целосно скенирање (нема пристапен пат)"),
        ],
        loc="upper center", bbox_to_anchor=(0.5, 1.06), ncol=3,
        frameon=False, fontsize=9, labelcolor=INK_2,
    )
    fig.tight_layout(rect=(0, 0.02, 1, 0.98))
    save(fig, "fig8_latency_p50")


def fig9_aggregates_l1(rows: dict) -> None:
    """Слика 9 — Агрегатни прашања врз модел L1 (сите се целосни скенирања)."""
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    queries = ["q8", "q9", "q10"]
    labels = ["8. Жанр по година", "9. Топ 10 јазици", "10. Годишен тренд"]
    offsets = {"oracle-nosql": -0.09, "foundationdb": 0.09}
    colors = {"oracle-nosql": C_ORACLE, "foundationdb": C_FDB}
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(0, 1400)
    for gi, qid in enumerate(queries):
        for slug, _ in DATABASES:
            ms = rows[(slug, qid, "L1")]["p50"]
            x = gi + offsets[slug]
            rounded_bar(ax, x, ms, 0.11, colors[slug])
            ax.text(x, ms + 28, mk_num(round(ms)), ha="center", va="bottom",
                    fontsize=9, color=INK)
    ax.set_xticks(range(len(queries)))
    ax.set_xticklabels(labels, fontsize=10, color=INK_2)
    ax.set_yticks([0, 400, 800, 1200])
    ax.set_yticklabels(["0", "400", "800", "1 200"])
    ax.set_ylabel("Латенција p50 (ms)", fontsize=9)
    strip_chrome(ax)
    ax.legend(
        handles=[
            Line2D([], [], marker="s", linestyle="", markersize=9,
                   markerfacecolor=colors[slug], markeredgecolor="none", label=label)
            for slug, label in DATABASES
        ],
        loc="lower center", bbox_to_anchor=(0.5, 1.01), ncol=2,
        frameon=False, fontsize=9, labelcolor=INK_2,
    )
    save(fig, "fig9_aggregates_l1")


def load_concurrency() -> list[dict] | None:
    """Rows from concurrency-<db>.csv, or None while the sweep hasn't run."""
    rows = []
    for slug, _ in DATABASES:
        path = RESULTS_DIR / f"concurrency-{slug}.csv"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                row["threads"] = int(row["threads"])
                row["throughput_ops_s"] = float(row["throughput_ops_s"])
                row["p50_ms"] = float(row["p50_ms"])
                rows.append(row)
    return rows


CONCURRENCY_TITLES = {
    "q1": "1. Точкесто барање (L1)",
    "q3": "3. Сите филмови од 2017 (L2)",
    "q6": "6. Топ 20 Drama (L2)",
    "q8": "8. Агрегат жанр/година (L2)",
}


def fig10_concurrency(conc: list[dict]) -> None:
    """Слика 10 — Пропусност при 1 / 4 / 16 конкурентни клиенти."""
    fig, axes = plt.subplots(2, 2, figsize=(9.2, 6.2))
    colors = {"oracle-nosql": C_ORACLE, "foundationdb": C_FDB}
    levels = [1, 4, 16]
    xpos = {1: 0, 4: 1, 16: 2}

    for ax, qid in zip(axes.flat, CONCURRENCY_TITLES):
        series = {}
        for slug, _ in DATABASES:
            pts = sorted(
                (r for r in conc if r["database"] == slug and r["query"] == qid),
                key=lambda r: r["threads"],
            )
            xs = [xpos[r["threads"]] for r in pts]
            ys = [r["throughput_ops_s"] for r in pts]
            series[slug] = dict(zip(xs, ys))
            ax.plot(xs, ys, color=colors[slug], linewidth=2, marker="o",
                    markersize=8, markeredgecolor=SURFACE, markeredgewidth=1.6,
                    solid_capstyle="round", zorder=3)
        # Labels collide where the lines run close: the higher point of each
        # pair is labeled above itself, the lower one below.
        floor = ax.get_ylim()[1] * 0.15  # a below-label here would hit the axis
        for slug, _ in DATABASES:
            other = next(s for s, _ in DATABASES if s != slug)
            for x, y in series[slug].items():
                above = y >= series[other].get(x, float("-inf"))
                if above:
                    offset, ha, va = (0, 9), "center", "bottom"
                elif y < floor:
                    offset, ha, va = (11, -4), "left", "center"
                else:
                    offset, ha, va = (0, -9), "center", "top"
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
            Line2D([], [], color=colors[slug], linewidth=2, marker="o",
                   markersize=7, markeredgecolor=SURFACE, label=label)
            for slug, label in DATABASES
        ],
        loc="upper center", bbox_to_anchor=(0.5, 1.03), ncol=2,
        frameon=False, fontsize=9.5, labelcolor=INK_2,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    save(fig, "fig10_concurrency")


def load_cpu_data() -> dict | None:
    """p50 per CPU count (harness runs) + 16-client throughput (sweeps)."""
    data = {"latency": {}, "throughput": {}}
    for slug, _ in DATABASES:
        for cpus in (1, 4):
            hpath = RESULTS_DIR / f"{slug}-cpus{cpus}.csv"
            cpath = RESULTS_DIR / f"concurrency-{slug}-cpus{cpus}.csv"
            if not (hpath.exists() and cpath.exists()):
                return None
            with open(hpath, encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    data["latency"][(slug, cpus, row["query"], row["model"])] = float(row["p50_ms"])
            with open(cpath, encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if row["threads"] == "16":
                        data["throughput"][(slug, cpus, row["query"])] = float(row["throughput_ops_s"])
    return data


def fig11_cpus(cpu: dict) -> None:
    """Слика 11 — 1 наспроти 4 процесори: едно барање vs 16 клиенти."""
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(9.6, 4.4))
    colors = {"oracle-nosql": C_ORACLE, "foundationdb": C_FDB}
    offsets = [-0.24, -0.08, 0.08, 0.24]
    combos = [("oracle-nosql", 1), ("oracle-nosql", 4),
              ("foundationdb", 1), ("foundationdb", 4)]

    def bars(ax, groups, value_of, fmt):
        for gi, group in enumerate(groups):
            for (slug, cpus), off in zip(combos, offsets):
                v = value_of(slug, cpus, group)
                ax.bar(gi + off, v, width=0.13, color=colors[slug],
                       alpha=0.45 if cpus == 1 else 1.0, edgecolor="none",
                       zorder=2)
                ax.annotate(fmt(v), (gi + off, v), textcoords="offset points",
                            xytext=(0, 3), ha="center", va="bottom",
                            fontsize=7.5, color=INK_2)

    # (а) the heaviest single-query paths: the three L1 aggregate scans
    agg = ["q8", "q9", "q10"]
    bars(ax_a, agg,
         lambda s, c, q: cpu["latency"][(s, c, q, "L1")],
         lambda v: mk_num(round(v)))
    ax_a.set_title("Едно барање — агрегати врз L1 (p50, ms)",
                   fontsize=10, color=INK, pad=10)
    ax_a.set_xticks(range(len(agg)))
    ax_a.set_xticklabels(["8. Жанр/година", "9. Јазици", "10. Тренд"],
                         fontsize=9, color=INK_2)
    ax_a.set_ylim(0, 1650)
    ax_a.set_yticks([0, 500, 1000, 1500])
    ax_a.set_ylabel("Латенција p50 (ms)", fontsize=9)
    strip_chrome(ax_a)

    # (б) throughput at 16 concurrent clients
    conc_q = ["q1", "q8"]
    bars(ax_b, conc_q,
         lambda s, c, q: cpu["throughput"][(s, c, q)],
         lambda v: mk_num(round(v)))
    ax_b.set_title("16 конкурентни клиенти (барања/s)",
                   fontsize=10, color=INK, pad=10)
    ax_b.set_xticks(range(len(conc_q)))
    ax_b.set_xticklabels(["1. Точкесто барање (L1)", "8. Агрегат (L2)"],
                         fontsize=9, color=INK_2)
    ax_b.set_yscale("log")
    ax_b.set_ylim(10, 40000)
    ax_b.set_yticks([10, 100, 1000, 10000])
    ax_b.set_yticklabels(["10", "100", "1 000", "10 000"])
    ax_b.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax_b.set_ylabel("Пропусност (барања/s, log)", fontsize=9)
    strip_chrome(ax_b)

    fig.legend(
        handles=[
            Patch(facecolor=colors[slug], alpha=0.45 if cpus == 1 else 1.0,
                  label=f"{label} · {cpus} CPU")
            for (slug, cpus), label in zip(
                combos, ["Oracle NoSQL", "Oracle NoSQL",
                         "FoundationDB", "FoundationDB"])
        ],
        loc="upper center", bbox_to_anchor=(0.5, 1.07), ncol=4,
        frameon=False, fontsize=8.5, labelcolor=INK_2, handletextpad=0.5,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    save(fig, "fig11_cpus")


def main() -> int:
    rows = load_rows()
    print("Пишувам графици во", OUT_DIR)
    fig7_load_time()
    fig8_latency_p50(rows)
    fig9_aggregates_l1(rows)
    conc = load_concurrency()
    if conc:
        fig10_concurrency(conc)
    else:
        print("  (нема concurrency-*.csv — прескокнувам Слика 10)")
    cpu = load_cpu_data()
    if cpu:
        fig11_cpus(cpu)
    else:
        print("  (нема *-cpus{1,4}.csv — прескокнувам Слика 11)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
