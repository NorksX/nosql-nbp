"""Prove that the three databases answer the ten queries identically.

    docker exec kv-client  python -m bench.answers          # writes answers-oracle-nosql.json
    docker exec fdb-client python -m bench.answers
    docker exec pg-client  python -m bench.answers
    python -m bench.answers --compare                       # on the host, no DB needed

``bench/harness.py`` already refuses to time a query whose L1 and L2 results
disagree, but that check is local to one database: it cannot see that Oracle
NoSQL and PostgreSQL returned different numbers for the same question. With
three databases and two models there are six implementations of every query,
and the whole comparison rests on all six answering the same thing.

So each database writes its canonical answers to
``bench/results/answers-<database>.json``, and ``--compare`` checks every file
against every other — and, when ``data/`` is present, against the answers
computed directly from the source files by ``DatasetBackend``, which is the
only implementation in the project that involves no database at all.

Floats are compared with the same tolerance the harness uses: L1 sums in key
order and L2 in file order, and float addition is not associative, so two
correct implementations can differ in the last bits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bench.harness import BACKENDS, QUERIES, results_match
from common.apply_schema import detect_database

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def collect(database: str) -> dict:
    backend = BACKENDS[database]()
    answers: dict[str, dict] = {}
    try:
        for qid, _, description, _, _ in QUERIES:
            answers[qid] = {}
            for model in ("l1", "l2"):
                answers[qid][model.upper()] = backend.run(model, qid)
            print(f"   {qid:>3}  {description}")
    finally:
        backend.close()
    return {"database": backend.name, "answers": answers}


def ground_truth() -> dict | None:
    """Answers computed from data/ with no database involved."""
    if not DATA_DIR.exists():
        return None
    from bench.queries import DatasetBackend

    backend = DatasetBackend()
    return {
        "database": "dataset (computed from data/)",
        "answers": {
            qid: {"L1": backend.run("l1", qid), "L2": backend.run("l1", qid)}
            for qid, *_ in QUERIES
        },
    }


def compare() -> int:
    sources = []
    truth = ground_truth()
    if truth:
        sources.append(truth)
        print(f"reference: {truth['database']}\n")
    else:
        print("no data/ — comparing the databases against each other only\n")

    for path in sorted(RESULTS_DIR.glob("answers-*.json")):
        with open(path, encoding="utf-8") as fh:
            sources.append(json.load(fh))

    if len(sources) < 2:
        sys.exit("need at least two sets of answers — run this on each database first")

    names = [s["database"] for s in sources]
    print("comparing:", ", ".join(names), "\n")

    disagreements = 0
    for qid, _, description, _, _ in QUERIES:
        verdicts = []
        reference = None
        for source in sources:
            for model in ("L1", "L2"):
                value = source["answers"].get(qid, {}).get(model)
                if value is None:
                    continue
                if reference is None:
                    reference = (source["database"], model, value)
                elif not results_match(reference[2], value):
                    verdicts.append(
                        f"{source['database']} {model} != "
                        f"{reference[0]} {reference[1]}"
                    )
        if verdicts:
            disagreements += 1
            print(f"MISMATCH  {qid:>3}  {description}")
            for v in verdicts:
                print(f"            {v}")
        else:
            n = sum(1 for s in sources for m in ("L1", "L2")
                    if s["answers"].get(qid, {}).get(m) is not None)
            print(f"ok        {qid:>3}  {description}  ({n} implementations agree)")

    if disagreements:
        print(f"\nFAILED: {disagreements} quer(ies) disagree across databases")
        return 1
    print("\nEvery implementation of every query returns the same answer.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", choices=tuple(BACKENDS))
    parser.add_argument("--compare", action="store_true",
                        help="compare the answer files already written")
    args = parser.parse_args(argv)

    if args.compare:
        return compare()

    database = detect_database(args.db)
    print(f"collecting answers from {database}\n")
    payload = collect(database)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"answers-{payload['database']}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())