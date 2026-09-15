"""Concurrency sweep: the same query hammered by 1 / 4 / 16 concurrent clients.

    docker exec kv-client  python -m bench.concurrency
    docker exec fdb-client python -m bench.concurrency
    docker exec pg-client  python -m bench.concurrency

Writes bench/results/concurrency-<database>.csv. Workers are separate
*processes*, not threads — with threads the Python GIL serializes the
client-side response parsing and the 16-way numbers would measure the client,
not the database. Each worker opens its own connection, warms up, waits on a
barrier so all start together, then runs the query in a closed loop for a
fixed wall-clock duration.

Only indexed access paths are swept. A path that degrades to a full scan reads
the entire corpus per request; running that 16-wide measures disk contention
on a degenerate plan, which Phase 4 already characterizes single-threaded.
"""

from __future__ import annotations

import argparse
import csv
import multiprocessing as mp
import statistics
import sys
import time
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"

#: (query, model) pairs with a real access path on ALL THREE databases, one per
#: query class: point lookup, range read, leaderboard, precomputed aggregate.
TARGETS = [
    ("q1", "l1", "Point lookup by TMDB id"),
    ("q3", "l2", "All movies of year 2017"),
    ("q6", "l2", "Top 20 of Drama by popularity"),
    ("q8", "l2", "Avg rating & count per genre per year"),
]

THREAD_LEVELS = [1, 4, 16]
DURATION_S = 8.0
WARMUP_S = 2.0


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(int(round(pct / 100 * len(ordered) + 0.5)) - 1, len(ordered) - 1)
    return ordered[max(index, 0)]


def make_backend(database: str | None = None):
    # Imported lazily so the parent process never initializes a client
    # library — the FDB network thread must not exist before fork(), and a
    # psycopg connection must never be inherited across one either.
    from bench.harness import BACKENDS
    from common.apply_schema import detect_database

    return BACKENDS[detect_database(database)]()


def worker(qid: str, model: str, barrier, queue, database=None) -> None:
    backend = make_backend(database)
    # A single-model backend (the relational one) has no L1/L2 to select
    # between, so every target runs against the model it does have.
    models = getattr(backend, "MODELS", ("l1", "l2"))
    if len(models) == 1:
        model = models[0]
    warm_end = time.perf_counter() + WARMUP_S
    while time.perf_counter() < warm_end:
        backend.run(model, qid)
    barrier.wait()
    samples: list[float] = []
    deadline = time.perf_counter() + DURATION_S
    while time.perf_counter() < deadline:
        start = time.perf_counter()
        backend.run(model, qid)
        samples.append((time.perf_counter() - start) * 1000)
    queue.put(samples)
    backend.close()


def sweep(qid: str, model: str, threads: int, database=None) -> dict:
    ctx = mp.get_context("fork" if sys.platform != "win32" else "spawn")
    barrier = ctx.Barrier(threads)
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=worker, args=(qid, model, barrier, queue, database))
        for _ in range(threads)
    ]
    for p in procs:
        p.start()
    samples: list[float] = []
    for _ in procs:
        samples.extend(queue.get())
    for p in procs:
        p.join()
    return {
        "threads": threads,
        "duration_s": DURATION_S,
        "ops": len(samples),
        "throughput_ops_s": round(len(samples) / DURATION_S, 1),
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(percentile(samples, 95), 3),
        "p99_ms": round(percentile(samples, 99), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="1/4/16-client concurrency sweep.")
    parser.add_argument("--only", help="comma-separated query ids, e.g. q1,q6")
    parser.add_argument("--tag", help="suffix for the output file, e.g. cpus1")
    parser.add_argument("--db", help="override the database detected from the "
                        "environment; use postgres-r for the relational model")
    args = parser.parse_args(argv)
    wanted = set(args.only.split(",")) if args.only else None

    # The slug must match the one bench/harness.py writes, so report.py and
    # plots.py can pair a concurrency CSV with its latency CSV.
    from bench.harness import BACKENDS
    from common.apply_schema import detect_database

    resolved = detect_database(args.db)
    database = BACKENDS[resolved].name
    model_of = getattr(BACKENDS[resolved], "MODELS", None)
    print(f"database: {database}")

    rows = []
    for qid, model, description in TARGETS:
        if wanted and qid not in wanted:
            continue
        if model_of and len(model_of) == 1:
            model = model_of[0]
        print(f"\n{qid} {model.upper()}  {description}")
        for threads in THREAD_LEVELS:
            stats = sweep(qid, model, threads, args.db)
            rows.append({
                "database": database,
                "query": qid,
                "model": model.upper(),
                "description": description,
                **stats,
            })
            print(f"   {threads:>2} clients  {stats['throughput_ops_s']:>9,.1f} ops/s   "
                  f"p50 {stats['p50_ms']:>8,.2f} ms   p95 {stats['p95_ms']:>8,.2f} ms")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"-{args.tag}" if args.tag else ""
    out = RESULTS_DIR / f"concurrency-{database}{suffix}.csv"
    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
