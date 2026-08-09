"""Merge the per-database CSVs into the tables used by docs/schema-comparison.md.

    python -m bench.report

Run from the repository root on the host — it only reads bench/results/*.csv,
so it needs neither database. Regenerate and paste whenever the benchmark is
re-run, so the document never drifts from the measurements.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DATABASES = [("oracle-nosql", "Oracle NoSQL"), ("foundationdb", "FoundationDB")]
QUERIES = [f"q{i}" for i in range(1, 11)]


def load() -> dict:
    rows = {}
    for slug, _ in DATABASES:
        path = RESULTS_DIR / f"{slug}.csv"
        if not path.exists():
            sys.exit(f"missing {path} — run `python -m bench.harness` on that database first")
        for row in csv.DictReader(open(path)):
            row["p50_ms"] = float(row["p50_ms"])
            row["p95_ms"] = float(row["p95_ms"])
            row["scan"] = row["full_scan"] == "True"
            rows[(row["database"], row["query"], row["model"])] = row
    return rows


def ms(row: dict) -> str:
    value = row["p50_ms"]
    text = f"{value:,.2f}" if value < 1000 else f"{value:,.0f}"
    return f"**{text}**{' ᶠ' if row['scan'] else ''}"


def ratio(fast: float, slow: float) -> str:
    factor = slow / fast
    return f"{factor:,.0f}×" if factor >= 10 else f"{factor:.1f}×"


def main() -> int:
    rows = load()

    print("### Latency by query, model and database (p50, ms)\n")
    print("| # | Query | Oracle L1 | Oracle L2 | FDB L1 | FDB L2 | Faster model |")
    print("|---|---|--:|--:|--:|--:|---|")
    for qid in QUERIES:
        sample = rows[("oracle-nosql", qid, "L1")]
        cells = [
            ms(rows[(slug, qid, model)]) for slug, _ in DATABASES for model in ("L1", "L2")
        ]
        verdicts = []
        for slug, label in DATABASES:
            l1 = rows[(slug, qid, "L1")]["p50_ms"]
            l2 = rows[(slug, qid, "L2")]["p50_ms"]
            winner, factor = ("L1", ratio(l1, l2)) if l1 < l2 else ("L2", ratio(l2, l1))
            verdicts.append(f"{label.split()[0]} {winner} {factor}")
        print(
            f"| {qid.upper()[1:]} | {sample['description']} | "
            + " | ".join(cells)
            + f" | {' · '.join(verdicts)} |"
        )
    print("\nᶠ = no index path for this model; the query degrades to a full scan.")

    print("\n### Same query, same model, different database\n")
    print("| # | Model | Oracle NoSQL | FoundationDB | Ratio |")
    print("|---|---|--:|--:|---|")
    for qid in QUERIES:
        for model in ("L1", "L2"):
            o = rows[("oracle-nosql", qid, model)]
            f = rows[("foundationdb", qid, model)]
            faster = "FDB" if f["p50_ms"] < o["p50_ms"] else "Oracle"
            factor = ratio(min(o["p50_ms"], f["p50_ms"]), max(o["p50_ms"], f["p50_ms"]))
            print(
                f"| {qid.upper()[1:]} | {model} | {ms(o)} | {ms(f)} | {faster} {factor} |"
            )

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
