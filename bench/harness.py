"""Benchmark the ten queries across both schemas, on one database.

    docker exec kv-client  python -m bench.harness
    docker exec fdb-client python -m bench.harness

Writes bench/results/<database>.csv and prints a summary. Run it once per
database; bench/report.py then merges the CSVs into the comparison document.

Protocol
--------
Correctness first: every query is executed once under L1 and once under L2 and
the two results must be identical before either is timed. A query whose two
models disagree is reported and *not* benchmarked — a latency for a wrong
answer would be worse than no number at all.

Then, per (query, model): discard warm-up runs, take `iterations` timed runs,
report p50 / p95 / p99. Iteration counts are per query class, scaled down for
the paths that degrade to a full scan — 1,000 iterations of a query that reads
68 MB would take hours and tell us nothing that 5 runs do not.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from pathlib import Path

from bench.queries import FdbBackend, OracleBackend

RESULTS_DIR = Path(__file__).resolve().parent / "results"

#: (id, category, description, iterations, warmup). Iteration counts follow
#: PLAN.md §Phase 4, reduced where a model has no access path and must scan.
QUERIES = [
    ("q1", "simple", "Point lookup by TMDB id", 200, 10),
    ("q2", "simple", "Lookup by IMDb id (alternate key)", 200, 10),
    ("q3", "simple", "All movies of year 2017", 50, 5),
    ("q4", "simple", "Language 'en' with vote_count > 500", 50, 5),
    ("q5", "complex", "Movies in both Drama and Horror", 20, 3),
    ("q6", "complex", "Top 20 of Drama by popularity", 50, 5),
    ("q7", "complex", "Same language, +/-1 year of Ad Astra", 20, 3),
    ("q8", "aggregate", "Avg rating & count per genre per year, 2000-2020", 5, 1),
    ("q9", "aggregate", "Top 10 languages by count, mean popularity", 5, 1),
    ("q10", "aggregate", "Yearly trend, vote_count >= 50", 5, 1),
]

#: Paths with no index at all read and parse the whole corpus. Cap them harder.
SCAN_ITERATIONS = 3
SCAN_WARMUP = 1


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(int(round(pct / 100 * len(ordered) + 0.5)) - 1, len(ordered) - 1)
    return ordered[max(index, 0)]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def results_match(a, b, tol: float = 1e-6) -> bool:
    """Structural equality, with a tolerance on floats.

    L1 sums vote averages in key order while L2 summed them in file order, and
    float addition is not associative — so two correct implementations can
    differ in the last bits. Exact equality would report that as a bug; a
    tolerance this tight would still catch any real disagreement.
    """
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tol
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(results_match(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(results_match(a[k], b[k], tol) for k in a)
    return a == b


def measure(fn, iterations: int, warmup: int) -> dict:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return {
        "n": len(samples),
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(percentile(samples, 95), 3),
        "p99_ms": round(percentile(samples, 99), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
        "min_ms": round(min(samples), 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark L1 vs L2 on one database.")
    parser.add_argument("--db", choices=("oracle", "fdb"))
    parser.add_argument("--only", help="comma-separated query ids, e.g. q1,q8")
    args = parser.parse_args(argv)

    database = args.db or ("oracle" if os.environ.get("NOSQL_ENDPOINT") else "fdb")
    backend = OracleBackend() if database == "oracle" else FdbBackend()
    wanted = set(args.only.split(",")) if args.only else None

    print(f"database: {backend.name}")
    print(f"probe: movie {backend.probe_id} / {backend.probe_imdb}, "
          f"year {backend.probe_year}, lang {backend.probe_lang!r}\n")

    rows = []
    mismatches = []

    for qid, category, description, iterations, warmup in QUERIES:
        if wanted and qid not in wanted:
            continue

        print(f"{qid}  {description}")
        results = {}
        for model in ("l1", "l2"):
            start = time.perf_counter()
            results[model] = backend.run(model, qid)
            elapsed = (time.perf_counter() - start) * 1000
            # A first run slower than a quarter second means there is no index
            # behind it; treat it as a scan and cap the iteration count.
            results[f"{model}_slow"] = elapsed > 250

        if not results_match(results["l1"], results["l2"]):
            mismatches.append(qid)
            print(f"   MISMATCH — not benchmarked")
            print(f"      L1: {canonical(results['l1'])[:160]}")
            print(f"      L2: {canonical(results['l2'])[:160]}")
            continue

        answer = canonical(results["l1"])
        print(f"   = {answer[:100]}{'...' if len(answer) > 100 else ''}")

        for model in ("l1", "l2"):
            scan = results[f"{model}_slow"]
            n = SCAN_ITERATIONS if scan else iterations
            w = SCAN_WARMUP if scan else warmup
            stats = measure(lambda: backend.run(model, qid), n, w)
            rows.append({
                "database": backend.name,
                "query": qid,
                "category": category,
                "description": description,
                "model": model.upper(),
                "full_scan": scan,
                **stats,
            })
            print(f"   {model.upper()}  p50 {stats['p50_ms']:>10,.2f} ms   "
                  f"p95 {stats['p95_ms']:>10,.2f} ms   n={stats['n']}"
                  f"{'   [full scan]' if scan else ''}")
        print()

    backend.close()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{backend.name}.csv"
    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")

    if mismatches:
        print(f"\nFAILED: L1/L2 disagree on {', '.join(mismatches)}")
        return 1
    print("\nL1 and L2 agreed on every query.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
