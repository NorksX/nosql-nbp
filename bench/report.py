"""Merge the per-database CSVs into the tables used by docs/schema-comparison.md.

    python -m bench.report

Run from the repository root on the host — it only reads bench/results/*.csv,
so it needs no database. Regenerate and paste whenever the benchmark is re-run,
so the document never drifts from the measurements.

Three databases are expected — `oracle-nosql`, `foundationdb` and `postgresql`.
A missing CSV is reported and skipped rather than being fatal, so the tables can
still be regenerated while one of the three is being re-measured; with fewer
than two there is nothing to compare and the script stops.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"
ALL_DATABASES = [
    ("oracle-nosql", "Oracle NoSQL"),
    ("foundationdb", "FoundationDB"),
    ("postgresql", "PostgreSQL"),
]
QUERIES = [f"q{i}" for i in range(1, 11)]

#: The relational model lives in its own CSV: one model, not two, so it does
#: not fit the L1/L2 column pairs the tables above are built from.
RELATIONAL = "postgresql-relational"


def load() -> tuple[dict, list[tuple[str, str]]]:
    rows: dict = {}
    present: list[tuple[str, str]] = []
    for slug, label in ALL_DATABASES:
        path = RESULTS_DIR / f"{slug}.csv"
        if not path.exists():
            print(f"<!-- missing {path.name} — skipping {label}; "
                  f"run `python -m bench.harness` on it -->", file=sys.stderr)
            continue
        present.append((slug, label))
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                row["p50_ms"] = float(row["p50_ms"])
                row["p95_ms"] = float(row["p95_ms"])
                row["scan"] = row["full_scan"] == "True"
                rows[(row["database"], row["query"], row["model"])] = row
    if len(present) < 2:
        sys.exit("need at least two databases' results to compare")
    return rows, present


def load_relational() -> dict | None:
    """Model R's per-query rows, or None when it has not been benchmarked."""
    path = RESULTS_DIR / f"{RELATIONAL}.csv"
    if not path.exists():
        print(f"<!-- no {RELATIONAL}.csv — skipping the model R table -->",
              file=sys.stderr)
        return None
    out = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            row["p50_ms"] = float(row["p50_ms"])
            row["p95_ms"] = float(row["p95_ms"])
            row["scan"] = row["full_scan"] == "True"
            out[row["query"]] = row
    return out


def ms(row: dict) -> str:
    value = row["p50_ms"]
    text = f"{value:,.2f}" if value < 1000 else f"{value:,.0f}"
    return f"**{text}**{' ᶠ' if row['scan'] else ''}"


def ratio(fast: float, slow: float) -> str:
    factor = slow / fast
    return f"{factor:,.0f}×" if factor >= 10 else f"{factor:.1f}×"


def short(label: str) -> str:
    return label.split()[0]


def main() -> int:
    rows, databases = load()

    print("### Latency by query, model and database (p50, ms)\n")
    header = " | ".join(f"{short(label)} {m}" for _, label in databases for m in ("L1", "L2"))
    align = "|".join(["--:"] * (2 * len(databases)))
    print(f"| # | Query | {header} | Faster model |")
    print(f"|---|---|{align}|---|")
    for qid in QUERIES:
        sample = rows[(databases[0][0], qid, "L1")]
        cells = [
            ms(rows[(slug, qid, model)]) for slug, _ in databases for model in ("L1", "L2")
        ]
        verdicts = []
        for slug, label in databases:
            l1 = rows[(slug, qid, "L1")]["p50_ms"]
            l2 = rows[(slug, qid, "L2")]["p50_ms"]
            winner, factor = ("L1", ratio(l1, l2)) if l1 < l2 else ("L2", ratio(l2, l1))
            verdicts.append(f"{short(label)} {winner} {factor}")
        print(
            f"| {qid.upper()[1:]} | {sample['description']} | "
            + " | ".join(cells)
            + f" | {' · '.join(verdicts)} |"
        )
    print("\nᶠ = no index path for this model; the query degrades to a full scan.")

    print("\n### Same query, same model, different database\n")
    cols = " | ".join(label for _, label in databases)
    align = "|".join(["--:"] * len(databases))
    print(f"| # | Model | {cols} | Fastest | Spread |")
    print(f"|---|---|{align}|---|---|")
    for qid in QUERIES:
        for model in ("L1", "L2"):
            got = [(slug, label, rows[(slug, qid, model)]) for slug, label in databases]
            fastest = min(got, key=lambda g: g[2]["p50_ms"])
            slowest = max(got, key=lambda g: g[2]["p50_ms"])
            spread = ratio(fastest[2]["p50_ms"], slowest[2]["p50_ms"])
            cells = " | ".join(ms(row) for _, _, row in got)
            print(
                f"| {qid.upper()[1:]} | {model} | {cells} | "
                f"{short(fastest[1])} | {spread} |"
            )

    print("\n### Where each database wins\n")
    print("| Database | Model | Fastest on |")
    print("|---|---|---|")
    for slug, label in databases:
        for model in ("L1", "L2"):
            won = [
                qid.upper()[1:]
                for qid in QUERIES
                if min(databases, key=lambda d: rows[(d[0], qid, model)]["p50_ms"])[0] == slug
            ]
            print(f"| {label} | {model} | {', '.join(won) if won else '—'} |")

    relational = load_relational()
    if relational:
        print("\n### Model R — the normalized relational schema\n")
        print("Each key-value store at its best (the faster of L1 and L2) against "
              "PostgreSQL used relationally.\n")
        cols = " | ".join(label for _, label in databases if label != "PostgreSQL")
        print(f"| # | Query | {cols} | PostgreSQL R | Fastest |")
        print("|---|---|--:|--:|--:|---|")
        for qid in QUERIES:
            best = []
            for slug, label in databases:
                if slug == "postgresql":
                    continue
                pick = min((rows[(slug, qid, m)] for m in ("L1", "L2")),
                           key=lambda r: r["p50_ms"])
                best.append((label, pick))
            r = relational[qid]
            cells = " | ".join(ms(row) for _, row in best)
            contenders = best + [("PostgreSQL R", r)]
            winner = min(contenders, key=lambda c: c[1]["p50_ms"])[0]
            print(f"| {qid.upper()[1:]} | {rows[(databases[0][0], qid, 'L1')]['description']} "
                  f"| {cells} | {ms(r)} | {short(winner)} |")
        print("\nᶠ = no index path; the query reads the whole corpus.")

    print("\n### Tail behaviour (p95 / p50)\n")
    worst = sorted(
        rows.values(), key=lambda r: -(r["p95_ms"] / r["p50_ms"] if r["p50_ms"] else 0)
    )[:6]
    print("| Database | # | Model | p50 ms | p95 ms | p95/p50 |")
    print("|---|---|---|--:|--:|--:|")
    for row in worst:
        print(
            f"| {row['database']} | {row['query'].upper()[1:]} | {row['model']} | "
            f"{row['p50_ms']:,.2f} | {row['p95_ms']:,.2f} | "
            f"{row['p95_ms'] / row['p50_ms']:.2f} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
