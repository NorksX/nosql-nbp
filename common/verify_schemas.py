"""Check both schemas against the real data before either loader is written.

Runs entirely offline — no database, no driver — so any teammate can run it on
the host with plain Python, from the repository root:

    python -m common.verify_schemas

It proves the things the schemas assume: that the corpus is intact, that every
L1 key and value fits inside FoundationDB's limits, that L2's chunking never
produces a value over the limit, and that L2's precomputed aggregates agree
with a direct count over the same records. The numbers it prints are the ones
quoted in common/schema.md and in section 4 of the елаборат.
"""

from __future__ import annotations

import sys
from collections import Counter

from common.dataset import GENRE_NAMES, YEAR_UNKNOWN, encode, iter_movies
from common.keyspec import (
    CHUNK_TARGET_BYTES,
    FDB_KEY_LIMIT,
    FDB_VALUE_LIMIT,
    MIN_VOTE_COUNT,
    TOP_K,
    aggregate,
    chunk_records,
    l1_index_keys,
    l1_movie_key,
    year_bucket,
)

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"   {'ok  ' if condition else 'FAIL'}  {message}")
    if not condition:
        failures.append(message)


def key_bytes(key: tuple) -> int:
    """Upper bound on the packed size of a tuple key.

    The FoundationDB tuple layer costs 1 type byte plus the payload per element
    (strings get a terminating null, integers at most 8 bytes). Overestimating
    is fine here — the check is only meant to catch a key family that could
    approach the 10 KB limit.
    """
    total = 0
    for part in key:
        if isinstance(part, str):
            total += len(part.encode("utf-8")) + 2
        else:
            total += 9
    return total


def main() -> int:
    print("reading data/ ...")
    movies = list(iter_movies())
    print(f"   {len(movies):,} records\n")

    # ---------------------------------------------------------------- corpus
    print("corpus")
    ids = {m["id"] for m in movies}
    check(len(movies) == 109_222, f"109,222 records (got {len(movies):,})")
    check(len(ids) == len(movies), f"ids are unique ({len(ids):,} distinct)")
    check(
        all("id_imdb" in m for m in movies),
        "every record has id_imdb (alternate key is total)",
    )
    genres_used = {g for m in movies for g in m["genre_ids"]}
    check(
        genres_used == set(GENRE_NAMES),
        f"{len(genres_used)} distinct genre ids, all named in GENRE_NAMES",
    )
    no_genre = sum(1 for m in movies if not m["genre_ids"])
    no_year = sum(1 for m in movies if "year" not in m)
    zero_votes = sum(1 for m in movies if m["vote_count"] == 0)
    print(f"   ·  {no_genre:,} ({no_genre / len(movies):.1%}) have no genre")
    print(f"   ·  {no_year:,} ({no_year / len(movies):.1%}) have no derivable year")
    print(
        f"   ·  {zero_votes:,} ({zero_votes / len(movies):.1%}) have vote_count == 0"
        f"  → MIN_VOTE_COUNT = {MIN_VOTE_COUNT}"
    )
    print()

    # -------------------------------------------------------------------- L1
    print("L1 — fine-grained")
    families = Counter()
    max_key = 0
    max_value = 0
    for movie in movies:
        max_key = max(max_key, key_bytes(l1_movie_key(movie["id"])))
        max_value = max(max_value, len(encode(movie)))
        families["movie"] += 1
        for key in l1_index_keys(movie):
            families[f"idx:{key[1]}"] += 1
            max_key = max(max_key, key_bytes(key))

    total_keys = sum(families.values())
    for name, count in sorted(families.items(), key=lambda kv: -kv[1]):
        print(f"   ·  {name:<16} {count:>9,}")
    print(f"   ·  {'TOTAL':<16} {total_keys:>9,}")
    check(total_keys == 730_414, f"730,414 keys (got {total_keys:,})")
    check(
        families["idx:year"] == len(movies) - no_year,
        "year index skips exactly the undated records",
    )
    check(
        families["idx:genre"] == families["idx:genre_pop"],
        "genre and genre_pop indexes cover the same entries",
    )
    check(max_key < FDB_KEY_LIMIT, f"largest key {max_key:,} B < {FDB_KEY_LIMIT:,} B")
    check(
        max_value < FDB_VALUE_LIMIT,
        f"largest value {max_value:,} B < {FDB_VALUE_LIMIT:,} B",
    )
    print()

    # -------------------------------------------------------------------- L2
    print("L2 — coarse-grained")
    buckets: dict[int, list[dict]] = {}
    for movie in movies:
        buckets.setdefault(year_bucket(movie), []).append(movie)

    chunks = 0
    chunked_records = 0
    chunk_bytes = 0
    largest_chunk = 0
    over_limit = 0
    biggest_year = max(buckets, key=lambda y: sum(len(encode(m)) for m in buckets[y]))
    for year, bucket in sorted(buckets.items()):
        for _, batch, value in chunk_records(bucket):
            chunks += 1
            chunked_records += len(batch)
            chunk_bytes += len(value)
            largest_chunk = max(largest_chunk, len(value))
            over_limit += len(value) > FDB_VALUE_LIMIT

    print(f"   ·  {len(buckets)} year buckets, {chunks:,} chunks, {chunk_bytes / 2**20:.2f} MiB")
    print(
        f"   ·  largest bucket: year {biggest_year} — "
        f"{len(buckets[biggest_year]):,} movies, "
        f"{sum(len(encode(m)) for m in buckets[biggest_year]) / 2**20:.2f} MiB"
    )
    print(f"   ·  largest chunk: {largest_chunk:,} B (target {CHUNK_TARGET_BYTES:,} B)")
    check(chunked_records == len(movies), "every record lands in exactly one chunk")
    check(over_limit == 0, f"no chunk exceeds {FDB_VALUE_LIMIT:,} B")
    check(
        YEAR_UNKNOWN in buckets and len(buckets[YEAR_UNKNOWN]) == no_year,
        f"undated records bucketed under year {YEAR_UNKNOWN}",
    )

    agg = aggregate(movies)
    print(
        f"   ·  aggregates: {len(agg['genre_year']):,} genre-year, "
        f"{len(agg['lang']):,} language, {len(agg['year']):,} year, "
        f"{len(agg['genre_top']):,} leaderboards"
    )
    l2_keys = (
        chunks
        + len(agg["genre_year"])
        + len(agg["lang"])
        + len(agg["year"])
        + sum(len(v) for v in agg["genre_top"].values())
    )
    print(f"   ·  {'TOTAL':<16} {l2_keys:>9,}")
    check(
        sum(r["n_movies"] for r in agg["lang"].values()) == len(movies),
        "language aggregate counts every movie exactly once",
    )
    check(
        sum(r["n_movies"] for r in agg["year"].values()) == len(movies),
        "year aggregate counts every movie exactly once",
    )
    check(
        sum(r["n_movies"] for r in agg["genre_year"].values()) == families["idx:genre"],
        "genre-year counts match the L1 genre index entry count",
    )
    check(
        all(len(v) == TOP_K for v in agg["genre_top"].values()),
        f"every genre has a full top-{TOP_K} leaderboard",
    )
    print(f"\n   L1/L2 key ratio: {total_keys / l2_keys:.0f}×")
    print()

    print("FAILED" if failures else "ALL CHECKS PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
