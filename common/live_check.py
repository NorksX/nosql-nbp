"""End-to-end conformance test for both schemas against a live database.

Loads the full 109,222-record corpus into L1 and L2, times it, then reads back
through every key family and checks the answers against values computed
independently from the data files. It is the proof that the design in
schema.md survives contact with a real store.

    docker exec kv-client  python -m common.live_check
    docker exec fdb-client python -m common.live_check
    docker exec fdb-client python -m common.live_check --model l2 --skip-load

The loader here is deliberately the simplest thing that works — no progress
bars, no resume, no tuning. The restartable production loaders are still each
sub-team's Phase 2 deliverable (`oracle/load_l*.py`, `fdb/load_l*.py`); this
exists to validate the *schema*, and to give those loaders a reference timing
to beat.

Assumes the schema already exists — run `python -m common.apply_schema` first.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from common import keyspec as ks
from common.dataset import encode, iter_movies

# Probe parameters live in keyspec so live_check and the benchmark ask the
# identical questions of both databases.
PROBE_YEAR = ks.PROBE_YEAR
PROBE_LANG = ks.PROBE_LANG
PROBE_VOTES = ks.PROBE_VOTES
PROBE_GENRE = ks.PROBE_GENRE
PROBE_GENRE_B = ks.PROBE_GENRE_B

failures: list[str] = []
_t0 = time.perf_counter()


def log(message: str = "") -> None:
    print(message, flush=True)


def check(condition: bool, message: str, detail: str = "") -> None:
    log(f"   {'ok  ' if condition else 'FAIL'}  {message}{f'  [{detail}]' if detail else ''}")
    if not condition:
        failures.append(message)


def timed(label: str, fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    elapsed = time.perf_counter() - start
    log(f"   {label}: {elapsed:.1f} s")
    return result, elapsed


# --------------------------------------------------------------------------
# Expected answers, computed from the data files rather than from either DB
# --------------------------------------------------------------------------


def expectations(movies: list[dict]) -> dict:
    probe = max(movies, key=lambda m: (m["popularity"], m["id"]))
    agg = ks.aggregate(movies)

    genre_pop_top = sorted(
        (m for m in movies if PROBE_GENRE in m["genre_ids"]),
        key=lambda m: (-m["popularity"], m["id"]),
    )[: ks.TOP_K]

    return {
        "probe": probe,
        "agg": agg,
        "n_total": len(movies),
        "n_year": sum(1 for m in movies if m.get("year") == PROBE_YEAR),
        "n_lang_votes": sum(
            1
            for m in movies
            if m.get("original_language") == PROBE_LANG and m["vote_count"] > PROBE_VOTES
        ),
        "n_genre": sum(1 for m in movies if PROBE_GENRE in m["genre_ids"]),
        "n_both_genres": sum(
            1
            for m in movies
            if PROBE_GENRE in m["genre_ids"] and PROBE_GENRE_B in m["genre_ids"]
        ),
        "top_ids": [m["id"] for m in genre_pop_top],
        "n_chunks_year": sum(
            1 for _ in ks.chunk_records([m for m in movies if m.get("year") == PROBE_YEAR])
        ),
    }


def l2_rows(movies: list[dict], agg: dict):
    """Every L2 (key, value) the loaders must write, as tuple keys."""
    buckets: dict[int, list[dict]] = {}
    for movie in movies:
        buckets.setdefault(ks.year_bucket(movie), []).append(movie)

    for year, bucket in sorted(buckets.items()):
        for chunk_no, batch, value in ks.chunk_records(bucket):
            yield ks.l2_chunk_key(year, chunk_no), value, len(batch)

    for (genre_id, year), row in agg["genre_year"].items():
        yield ks.l2_genre_year_key(genre_id, year), encode(row), None
    for lang, row in agg["lang"].items():
        yield ks.l2_lang_key(lang), encode(row), None
    for year, row in agg["year"].items():
        yield ks.l2_year_stats_key(year), encode(row), None
    for genre_id, top in agg["genre_top"].items():
        for position, record in enumerate(top):
            yield ks.l2_genre_top_key(genre_id, position), encode(record), None


# ==========================================================================
# FoundationDB
# ==========================================================================


def run_fdb(models: list[str], movies: list[dict], exp: dict, skip_load: bool) -> None:
    import fdb

    fdb.api_version(730)
    db = fdb.open(os.environ.get("FDB_CLUSTER_FILE", "/etc/foundationdb/fdb.cluster"))
    log(f"FoundationDB via {os.environ.get('FDB_CLUSTER_FILE')}\n")

    @fdb.transactional
    def write(tr, batch):
        for key, value in batch:
            tr[key] = value

    @fdb.transactional
    def get(tr, key):
        return tr[fdb.tuple.pack(key)].value

    @fdb.transactional
    def scan(tr, key_range, limit=0):
        # get_range pages transparently inside the transaction; the largest
        # range here is 149k index keys, comfortably inside the 5 s limit.
        bounds = fdb.tuple.range(key_range.prefix)
        begin = fdb.tuple.pack(key_range.start) if key_range.start else bounds.start
        return list(tr.get_range(begin, bounds.stop, limit=limit))

    def load(pairs) -> int:
        n = 0
        for batch in ks.batched(pairs):
            write(db, batch)
            n += len(batch)
        return n

    if "l1" in models:
        log("L1 — load")

        def l1_pairs():
            for movie in movies:
                yield fdb.tuple.pack(ks.l1_movie_key(movie["id"])), encode(movie)
                for key, value in ks.l1_index_entries(movie):
                    yield fdb.tuple.pack(key), fdb.tuple.pack(value)

        if not skip_load:
            written, seconds = timed("wrote 730,414 keys", load, l1_pairs())
            log(f"   {written / seconds:,.0f} keys/s, {len(movies) / seconds:,.0f} movies/s")
            check(written == 730_414, f"730,414 keys written (got {written:,})")

        log("L1 — read back")
        probe = exp["probe"]
        stored = get(db, ks.l1_movie_key(probe["id"]))
        check(stored is not None and encode(probe) == bytes(stored),
              "Q1 point lookup returns byte-identical record", probe["title"])

        imdb = get(db, ks.l1_imdb_key(probe["id_imdb"]))
        check(imdb is not None and fdb.tuple.unpack(bytes(imdb))[0] == probe["id"],
              "Q2 IMDb index resolves to the right id", probe["id_imdb"])

        n = len(scan(db, ks.l1_year_range(PROBE_YEAR)))
        check(n == exp["n_year"], f"Q3 year {PROBE_YEAR} index range", f"{n:,} == {exp['n_year']:,}")

        rows = scan(db, ks.l1_lang_range(PROBE_LANG, PROBE_VOTES + 1))
        check(len(rows) == exp["n_lang_votes"],
              f"Q4 {PROBE_LANG} + votes>{PROBE_VOTES} as one range read",
              f"{len(rows):,} == {exp['n_lang_votes']:,}")

        a = {fdb.tuple.unpack(k)[-1] for k, _ in scan(db, ks.l1_genre_range(PROBE_GENRE))}
        b = {fdb.tuple.unpack(k)[-1] for k, _ in scan(db, ks.l1_genre_range(PROBE_GENRE_B))}
        check(len(a) == exp["n_genre"], "Q5 genre index range", f"{len(a):,} == {exp['n_genre']:,}")
        check(len(a & b) == exp["n_both_genres"], "Q5 two-genre intersection",
              f"{len(a & b):,} == {exp['n_both_genres']:,}")

        top = scan(db, ks.l1_genre_pop_range(PROBE_GENRE), limit=ks.TOP_K)
        got = [fdb.tuple.unpack(k)[-1] for k, _ in top]
        check(got == exp["top_ids"],
              f"Q6 top-{ks.TOP_K} arrives pre-sorted, no client sort", f"{len(got)} ids")
        log()

    if "l2" in models:
        log("L2 — load")
        rows = list(l2_rows(movies, exp["agg"]))
        if not skip_load:
            pairs = ((fdb.tuple.pack(k), v) for k, v, _ in rows)
            written, seconds = timed(f"wrote {len(rows):,} keys", load, pairs)
            log(f"   {written / seconds:,.0f} keys/s")
            check(written == len(rows), f"{len(rows):,} keys written")

        log("L2 — read back")
        chunks = scan(db, ks.l2_year_range(PROBE_YEAR))
        total = sum(len(json_loads(v)) for _, v in chunks)
        check(len(chunks) == exp["n_chunks_year"],
              f"Q3 year {PROBE_YEAR} is {exp['n_chunks_year']} chunks", f"got {len(chunks)}")
        check(total == exp["n_year"], "Q3 chunks hold every movie of the year",
              f"{total:,} == {exp['n_year']:,}")

        largest = max(len(v) for _, v in chunks)
        check(largest <= ks.FDB_VALUE_LIMIT, "stored chunks stay under the 100 KB value limit",
              f"largest {largest:,} B")

        stat = get(db, ks.l2_genre_year_key(PROBE_GENRE, PROBE_YEAR))
        want = exp["agg"]["genre_year"][(PROBE_GENRE, PROBE_YEAR)]
        check(stat is not None and json_loads(bytes(stat)) == want,
              "Q8 genre-year stat matches the recomputed aggregate",
              f"n={want['n_movies']}, avg_vote={want['avg_vote']}")

        langs = scan(db, ks.l2_lang_range())
        check(len(langs) == len(exp["agg"]["lang"]),
              "Q9 language stats readable in one range read", f"{len(langs)} languages")

        years = scan(db, ks.l2_year_stats_range())
        check(len(years) == len(exp["agg"]["year"]),
              "Q10 year stats readable in one range read", f"{len(years)} years")

        top = scan(db, ks.l2_genre_top_range(PROBE_GENRE))
        got = [json_loads(v)["id"] for _, v in top]
        check(got == exp["top_ids"], f"Q6 precomputed top-{ks.TOP_K} matches L1's ordering")
        log()


def json_loads(blob):
    import json

    return json.loads(bytes(blob))


# ==========================================================================
# Oracle NoSQL
# ==========================================================================


def run_oracle(models: list[str], movies: list[dict], exp: dict, skip_load: bool) -> None:
    from borneo import GetRequest, NoSQLHandle, NoSQLHandleConfig, PutRequest, QueryRequest
    from borneo.kv import StoreAccessTokenProvider

    endpoint = os.environ["NOSQL_ENDPOINT"]
    log(f"Oracle NoSQL at {endpoint}\n")
    handle = NoSQLHandle(
        NoSQLHandleConfig(endpoint).set_authorization_provider(StoreAccessTokenProvider())
    )

    def query(statement: str) -> list[dict]:
        request = QueryRequest().set_statement(statement)
        rows: list[dict] = []
        while True:
            rows.extend(handle.query(request).get_results())
            if request.is_done():
                break
        return rows

    def scalar(statement: str):
        rows = query(statement)
        return next(iter(rows[0].values())) if rows else None

    def put_all(table: str, values) -> int:
        request = PutRequest().set_table_name(table)
        n = 0
        for value in values:
            handle.put(request.set_value(value))
            n += 1
        return n

    def genre_predicate(genre_id: int) -> str:
        """kvlite's accepted syntax for "this array contains this value".

        Oracle NoSQL offers sequence-comparison (`=any`) and an existential
        filter step; which one a given release accepts is exactly the sort of
        thing the report should state from observation rather than from docs.
        """
        for candidate in (
            f"t.doc.genre_ids[] =any {genre_id}",
            f"EXISTS t.doc.genre_ids[$element = {genre_id}]",
            f"{genre_id} IN t.doc.genre_ids[]",
        ):
            try:
                query(f"SELECT t.id FROM l1_movies t WHERE {candidate} LIMIT 1")
                return candidate
            except Exception:
                continue
        raise RuntimeError("no accepted array-containment syntax")

    try:
        if "l1" in models:
            log("L1 — load")
            if not skip_load:
                values = ({"id": m["id"], "doc": m} for m in movies)
                written, seconds = timed(
                    f"put {len(movies):,} rows", put_all, "l1_movies", values
                )
                log(f"   {written / seconds:,.0f} rows/s (indexes maintained server-side)")
                check(written == len(movies), f"{len(movies):,} rows written")

            log("L1 — read back")
            n = scalar("SELECT count(*) AS n FROM l1_movies")
            check(n == exp["n_total"], "row count", f"{n:,} == {exp['n_total']:,}")

            probe = exp["probe"]
            got = handle.get(GetRequest().set_table_name("l1_movies").set_key({"id": probe["id"]}))
            doc = got.get_value()["doc"] if got.get_value() else None
            check(doc is not None and doc.get("title") == probe["title"],
                  "Q1 point lookup by primary key", probe["title"])

            rows = query(
                f"SELECT t.id FROM l1_movies t WHERE t.doc.id_imdb = '{probe['id_imdb']}'"
            )
            check(len(rows) == 1 and rows[0]["id"] == probe["id"],
                  "Q2 idx_imdb resolves the alternate key", probe["id_imdb"])

            n = scalar(f"SELECT count(*) AS n FROM l1_movies t WHERE t.doc.year = {PROBE_YEAR}")
            check(n == exp["n_year"], f"Q3 year {PROBE_YEAR} via idx_year",
                  f"{n:,} == {exp['n_year']:,}")

            n = scalar(
                "SELECT count(*) AS n FROM l1_movies t "
                f"WHERE t.doc.original_language = '{PROBE_LANG}' "
                f"AND t.doc.vote_count > {PROBE_VOTES}"
            )
            check(n == exp["n_lang_votes"], "Q4 language + vote floor via idx_lang_votes",
                  f"{n:,} == {exp['n_lang_votes']:,}")

            predicate = genre_predicate(PROBE_GENRE)
            log(f"   ·  array containment syntax accepted: {predicate!r}")
            n = scalar(f"SELECT count(*) AS n FROM l1_movies t WHERE {predicate}")
            check(n == exp["n_genre"], "Q5 genre membership via idx_genre",
                  f"{n:,} == {exp['n_genre']:,}")

            n = scalar(
                "SELECT count(*) AS n FROM l1_movies t "
                f"WHERE {predicate} AND {genre_predicate(PROBE_GENRE_B)}"
            )
            check(n == exp["n_both_genres"], "Q5 two-genre intersection",
                  f"{n:,} == {exp['n_both_genres']:,}")

            try:
                rows = query(
                    f"SELECT t.id FROM l1_movies t WHERE {predicate} "
                    f"ORDER BY t.doc.popularity DESC LIMIT {ks.TOP_K}"
                )
                got = [r["id"] for r in rows]
                check(got == exp["top_ids"], f"Q6 top-{ks.TOP_K} ordered by idx_genre_pop",
                      f"{len(got)} ids")
            except Exception as exc:
                check(False, f"Q6 ORDER BY ... DESC on idx_genre_pop", str(exc)[:120])

            try:
                rows = query(
                    "SELECT t.doc.original_language AS lang, count(*) AS n "
                    "FROM l1_movies t GROUP BY t.doc.original_language"
                )
                check(len(rows) == len(exp["agg"]["lang"]),
                      "Q9 server-side GROUP BY (no FoundationDB equivalent)",
                      f"{len(rows)} languages")
            except Exception as exc:
                check(False, "Q9 server-side GROUP BY", str(exc)[:120])
            log()

        if "l2" in models:
            log("L2 — load")
            rows = list(l2_rows(movies, exp["agg"]))
            if not skip_load:
                import json

                chunk_values = [
                    {"year": k[1], "chunk": k[2], "n_movies": n, "movies": json.loads(v)}
                    for k, v, n in rows
                    if k[0] == "year"
                ]
                stat_tables = {
                    ("stats", "genre_year"): (
                        "l2_genre_year_stats",
                        lambda k, v: {"genre_id": k[2], "year": k[3], **json.loads(v)},
                    ),
                    ("stats", "lang"): (
                        "l2_lang_stats",
                        lambda k, v: {"lang": k[2], **json.loads(v)},
                    ),
                    ("stats", "year"): (
                        "l2_year_stats",
                        lambda k, v: {"year": k[2], **json.loads(v)},
                    ),
                    ("top", "genre"): (
                        "l2_genre_top",
                        lambda k, v: {"genre_id": k[2], "pos": k[3], "doc": json.loads(v)},
                    ),
                }

                start = time.perf_counter()
                written = put_all("l2_movies_by_year", chunk_values)
                for family, (table, build) in stat_tables.items():
                    written += put_all(
                        table,
                        (build(k, v) for k, v, _ in rows if k[: len(family)] == family),
                    )
                seconds = time.perf_counter() - start
                log(f"   put {written:,} rows: {seconds:.1f} s  ({written / seconds:,.0f} rows/s)")
                check(written == len(rows), f"{len(rows):,} rows written (got {written:,})")

            log("L2 — read back")
            n = scalar(f"SELECT count(*) AS n FROM l2_movies_by_year WHERE year = {PROBE_YEAR}")
            check(n == exp["n_chunks_year"], f"Q3 year {PROBE_YEAR} chunk count",
                  f"{n} == {exp['n_chunks_year']}")

            n = scalar("SELECT sum(n_movies) AS n FROM l2_movies_by_year")
            check(n == exp["n_total"], "every movie is in exactly one chunk",
                  f"{n:,} == {exp['n_total']:,}")

            want = exp["agg"]["genre_year"][(PROBE_GENRE, PROBE_YEAR)]
            rows_ = query(
                "SELECT n_movies, avg_vote FROM l2_genre_year_stats "
                f"WHERE genre_id = {PROBE_GENRE} AND year = {PROBE_YEAR}"
            )
            check(
                len(rows_) == 1
                and rows_[0]["n_movies"] == want["n_movies"]
                and abs(rows_[0]["avg_vote"] - want["avg_vote"]) < 1e-6,
                "Q8 genre-year stat matches the recomputed aggregate",
                f"n={want['n_movies']}, avg_vote={want['avg_vote']}",
            )

            n = scalar("SELECT count(*) AS n FROM l2_lang_stats")
            check(n == len(exp["agg"]["lang"]), "Q9 language stats present", f"{n} languages")

            n = scalar("SELECT count(*) AS n FROM l2_year_stats")
            check(n == len(exp["agg"]["year"]), "Q10 year stats present", f"{n} years")

            rows_ = query(
                f"SELECT t.doc.id AS id FROM l2_genre_top t WHERE t.genre_id = {PROBE_GENRE} "
                "ORDER BY t.genre_id, t.pos"
            )
            got = [r["id"] for r in rows_]
            check(got == exp["top_ids"], f"Q6 precomputed top-{ks.TOP_K} matches L1's ordering")
            log()
    finally:
        handle.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load the full corpus into L1/L2 and verify every key family."
    )
    parser.add_argument("--model", action="append", choices=("l1", "l2"))
    parser.add_argument("--db", choices=("oracle", "fdb"))
    parser.add_argument("--skip-load", action="store_true", help="verify without reloading")
    args = parser.parse_args(argv)

    models = args.model or ["l1", "l2"]
    database = args.db or ("oracle" if os.environ.get("NOSQL_ENDPOINT") else "fdb")

    log("reading data/ ...")
    movies = list(iter_movies())
    exp = expectations(movies)
    log(f"   {len(movies):,} records; probe movie {exp['probe']['id']} "
        f"({exp['probe']['title']!r})\n")

    (run_oracle if database == "oracle" else run_fdb)(models, movies, exp, args.skip_load)

    log(f"{'FAILED: ' + str(len(failures)) + ' check(s)' if failures else 'ALL CHECKS PASSED'}"
        f"   ({time.perf_counter() - _t0:.1f} s total)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
