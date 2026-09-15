"""End-to-end conformance test for both schemas against a live database.

Loads the full 109,222-record corpus into L1 and L2, times it, then reads back
through every key family and checks the answers against values computed
independently from the data files. It is the proof that the design in
schema.md survives contact with a real store.

    docker exec kv-client  python -m common.live_check
    docker exec fdb-client python -m common.live_check
    docker exec pg-client  python -m common.live_check
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
from pathlib import Path

from common import keyspec as ks
from common.apply_schema import detect_database
from common.dataset import GENRE_NAMES, encode, iter_movies

# Probe parameters live in keyspec so live_check and the benchmark ask the
# identical questions of both databases.
PROBE_YEAR = ks.PROBE_YEAR
PROBE_LANG = ks.PROBE_LANG
PROBE_VOTES = ks.PROBE_VOTES
PROBE_GENRE = ks.PROBE_GENRE
PROBE_GENRE_B = ks.PROBE_GENRE_B

failures: list[str] = []
_t0 = time.perf_counter()

#: Where load timings are recorded. bench/plots.py reads this instead of
#: making someone copy a number out of this script's output into a constant,
#: which is the kind of step that gets skipped and then silently drops a
#: series from Слика 7 and Слика 14.
LOAD_TIMES_PATH = (
    Path(__file__).resolve().parent.parent / "bench" / "results" / "load-times.json"
)


def record_load(database: str, model: str, seconds: float, rows: int) -> None:
    """Merge one measured load time into bench/results/load-times.json."""
    import json

    LOAD_TIMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(LOAD_TIMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {}
    existing[f"{database}|{model.upper()}"] = {
        "seconds": round(seconds, 2),
        "rows": rows,
    }
    LOAD_TIMES_PATH.write_text(
        json.dumps(existing, indent=1, sort_keys=True), encoding="utf-8"
    )
    log(f"   recorded in {LOAD_TIMES_PATH.name}: {database} {model.upper()} "
        f"= {seconds:.1f} s")


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


# ==========================================================================
# PostgreSQL
# ==========================================================================


def run_postgres(models: list[str], movies: list[dict], exp: dict, skip_load: bool) -> None:
    """Load and verify the relational control.

    The loader batches inserts into transactions the same size FoundationDB
    uses (``keyspec.FDB_BATCH_SIZE``), rather than reaching for ``COPY``.
    ``COPY`` is PostgreSQL's real bulk-load path and is several times faster,
    but it has no counterpart in either key-value store, so using it here would
    make the load-time comparison a comparison of loading strategies. The
    batched ``INSERT ... ON CONFLICT`` is the honest middle: it is an upsert,
    like FoundationDB's ``tr[k] = v`` and Oracle NoSQL's ``put``, so the loader
    is idempotent and restartable in exactly the same sense theirs are.

    Indexes exist before the load, so index maintenance is paid during it — the
    same arrangement as Oracle NoSQL, and the same as FoundationDB, whose loader
    writes its index keys in the same transaction as the record.
    """
    import json

    import psycopg

    dsn = os.environ["PG_DSN"]
    log(f"PostgreSQL at {dsn.rsplit('@', 1)[-1]}\n")

    with psycopg.connect(dsn) as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
        log(f"   server_version {version}\n")

        def query(statement: str, params: tuple = ()) -> list[tuple]:
            with conn.cursor() as cur:
                cur.execute(statement, params)
                rows = cur.fetchall()
            conn.commit()
            return rows

        def scalar(statement: str, params: tuple = ()):
            rows = query(statement, params)
            return rows[0][0] if rows else None

        def insert_batched(statement: str, rows) -> int:
            """Upsert ``rows`` in transactions of FDB_BATCH_SIZE."""
            written = 0
            batch: list[tuple] = []
            with conn.cursor() as cur:
                for row in rows:
                    batch.append(row)
                    if len(batch) >= ks.FDB_BATCH_SIZE:
                        cur.executemany(statement, batch)
                        conn.commit()
                        written += len(batch)
                        batch = []
                if batch:
                    cur.executemany(statement, batch)
                    conn.commit()
                    written += len(batch)
            return written

        if "l1" in models:
            log("L1 — load")
            if not skip_load:
                rows = ((m["id"], encode(m).decode("utf-8")) for m in movies)
                written, seconds = timed(
                    f"inserted {len(movies):,} rows",
                    insert_batched,
                    "INSERT INTO l1_movies (id, doc) VALUES (%s, %s::jsonb) "
                    "ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc",
                    rows,
                )
                log(f"   {written / seconds:,.0f} rows/s "
                    f"(5 indexes maintained server-side)")
                record_load("postgresql", "L1", seconds, written)
                check(written == len(movies), f"{len(movies):,} rows written")

            log("L1 — read back")
            n = scalar("SELECT count(*) FROM l1_movies")
            check(n == exp["n_total"], "row count", f"{n:,} == {exp['n_total']:,}")

            probe = exp["probe"]
            doc = scalar("SELECT doc FROM l1_movies WHERE id = %s", (probe["id"],))
            check(doc is not None and doc.get("title") == probe["title"],
                  "Q1 point lookup by primary key", probe["title"])
            # JSONB is a decomposed binary form, so the stored bytes are not the
            # stored bytes of the other two databases — but the document must
            # still round-trip identically, or the comparison is not about the
            # same records. That is what is actually checked here.
            check(doc == json.loads(encode(probe)),
                  "JSONB round-trips the record unchanged (keys sorted, no nulls)")

            got = scalar("SELECT id FROM l1_movies WHERE doc ->> 'id_imdb' = %s",
                         (probe["id_imdb"],))
            check(got == probe["id"], "Q2 idx_imdb resolves the alternate key",
                  probe["id_imdb"])

            n = scalar("SELECT count(*) FROM l1_movies WHERE (doc ->> 'year')::int = %s",
                       (PROBE_YEAR,))
            check(n == exp["n_year"], f"Q3 year {PROBE_YEAR} via idx_year",
                  f"{n:,} == {exp['n_year']:,}")

            n = scalar(
                "SELECT count(*) FROM l1_movies "
                "WHERE doc ->> 'original_language' = %s "
                "AND (doc ->> 'vote_count')::int > %s",
                (PROBE_LANG, PROBE_VOTES),
            )
            check(n == exp["n_lang_votes"], "Q4 language + vote floor via idx_lang_votes",
                  f"{n:,} == {exp['n_lang_votes']:,}")

            n = scalar(
                "SELECT count(*) FROM l1_movies WHERE doc -> 'genre_ids' @> %s::jsonb",
                (str(PROBE_GENRE),),
            )
            check(n == exp["n_genre"], "Q5 genre membership via GIN idx_genre",
                  f"{n:,} == {exp['n_genre']:,}")

            n = scalar(
                "SELECT count(*) FROM l1_movies "
                "WHERE doc -> 'genre_ids' @> %s::jsonb AND doc -> 'genre_ids' @> %s::jsonb",
                (str(PROBE_GENRE), str(PROBE_GENRE_B)),
            )
            check(n == exp["n_both_genres"], "Q5 two-genre intersection",
                  f"{n:,} == {exp['n_both_genres']:,}")

            rows = query(
                "SELECT id FROM l1_movies WHERE doc -> 'genre_ids' @> %s::jsonb "
                "ORDER BY (doc ->> 'popularity')::float8 DESC, id ASC LIMIT %s",
                (str(PROBE_GENRE), ks.TOP_K),
            )
            got = [r[0] for r in rows]
            check(got == exp["top_ids"],
                  f"Q6 top-{ks.TOP_K} — GIN finds the genre, the sort does the order",
                  f"{len(got)} ids")

            rows = query(
                "SELECT doc ->> 'original_language', count(*) FROM l1_movies "
                "GROUP BY 1"
            )
            check(len(rows) == len(exp["agg"]["lang"]),
                  "Q9 server-side GROUP BY (no FoundationDB equivalent)",
                  f"{len(rows)} languages")

            # The thing neither NoSQL store can do in one statement: group by an
            # element of the JSON array. Oracle NoSQL needs one query per genre
            # (19 of them); FoundationDB has no server-side aggregation at all.
            rows = query(
                "SELECT g.value::int, count(*) FROM l1_movies, "
                "LATERAL jsonb_array_elements_text(doc -> 'genre_ids') g GROUP BY 1"
            )
            check(len(rows) == len(exp["agg"]["genre_top"]),
                  "GROUP BY an element of the JSON array, in ONE statement",
                  f"{len(rows)} genres")
            log()

        if "l2" in models:
            log("L2 — load")
            rows = list(l2_rows(movies, exp["agg"]))
            if not skip_load:
                chunk_rows = [
                    (k[1], k[2], n, v.decode("utf-8")) for k, v, n in rows if k[0] == "year"
                ]
                stat_tables = [
                    (("stats", "genre_year"),
                     "INSERT INTO l2_genre_year_stats (genre_id, year, n_movies, "
                     "sum_vote, avg_vote, sum_popularity, avg_popularity, sum_votes) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                     "ON CONFLICT (genre_id, year) DO UPDATE SET n_movies = EXCLUDED.n_movies",
                     lambda k, d: (k[2], k[3], d["n_movies"], d["sum_vote"], d["avg_vote"],
                                   d["sum_popularity"], d["avg_popularity"], d["sum_votes"])),
                    (("stats", "lang"),
                     "INSERT INTO l2_lang_stats (lang, n_movies, sum_popularity, "
                     "avg_popularity) VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (lang) DO UPDATE SET n_movies = EXCLUDED.n_movies",
                     lambda k, d: (k[2], d["n_movies"], d["sum_popularity"],
                                   d["avg_popularity"])),
                    (("stats", "year"),
                     "INSERT INTO l2_year_stats (year, n_movies, sum_popularity, "
                     "avg_popularity, n_rated, sum_vote_rated, avg_vote_rated) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                     "ON CONFLICT (year) DO UPDATE SET n_movies = EXCLUDED.n_movies",
                     lambda k, d: (k[2], d["n_movies"], d["sum_popularity"],
                                   d["avg_popularity"], d["n_rated"],
                                   d["sum_vote_rated"], d["avg_vote_rated"])),
                ]

                start = time.perf_counter()
                written = insert_batched(
                    "INSERT INTO l2_movies_by_year (year, chunk, n_movies, movies) "
                    "VALUES (%s,%s,%s,%s::jsonb) "
                    "ON CONFLICT (year, chunk) DO UPDATE SET movies = EXCLUDED.movies",
                    chunk_rows,
                )
                for family, statement, build in stat_tables:
                    written += insert_batched(
                        statement,
                        (build(k, json.loads(v)) for k, v, _ in rows
                         if k[: len(family)] == family),
                    )
                written += insert_batched(
                    "INSERT INTO l2_genre_top (genre_id, pos, doc) VALUES (%s,%s,%s::jsonb) "
                    "ON CONFLICT (genre_id, pos) DO UPDATE SET doc = EXCLUDED.doc",
                    ((k[2], k[3], v.decode("utf-8")) for k, v, _ in rows
                     if k[:2] == ("top", "genre")),
                )
                seconds = time.perf_counter() - start
                log(f"   inserted {written:,} rows: {seconds:.1f} s  "
                    f"({written / seconds:,.0f} rows/s)")
                record_load("postgresql", "L2", seconds, written)
                check(written == len(rows), f"{len(rows):,} rows written (got {written:,})")

            log("L2 — read back")
            n = scalar("SELECT count(*) FROM l2_movies_by_year WHERE year = %s",
                       (PROBE_YEAR,))
            check(n == exp["n_chunks_year"], f"Q3 year {PROBE_YEAR} chunk count",
                  f"{n} == {exp['n_chunks_year']}")

            n = scalar("SELECT sum(n_movies) FROM l2_movies_by_year")
            check(n == exp["n_total"], "every movie is in exactly one chunk",
                  f"{n:,} == {exp['n_total']:,}")

            # PostgreSQL has no 100 KB value limit, so there is no constraint to
            # check as there is on FoundationDB. What matters instead is that
            # the chunk *boundaries* are the identical ones the other two
            # databases hold — otherwise the three are not reading the same
            # thing and none of the Phase 4 chunk latencies compare.
            #
            # The boundaries are compared, not the byte sizes, because JSONB
            # does not store the text it was given: it reprints with a space
            # after every ':' and ',', so the same 89,999-byte chunk measures
            # several KB larger through `movies::text`. That difference is real
            # and is reported in the footprint section; it is not a defect.
            stored = {
                (year, chunk): n
                for year, chunk, n in query(
                    "SELECT year, chunk, n_movies FROM l2_movies_by_year"
                )
            }
            wanted = {(k[1], k[2]): n for k, _, n in rows if k[0] == "year"}
            check(stored == wanted,
                  "chunk boundaries are identical to the key-value stores'",
                  f"{len(stored):,} chunks")

            want = exp["agg"]["genre_year"][(PROBE_GENRE, PROBE_YEAR)]
            row = query(
                "SELECT n_movies, avg_vote FROM l2_genre_year_stats "
                "WHERE genre_id = %s AND year = %s",
                (PROBE_GENRE, PROBE_YEAR),
            )
            check(
                len(row) == 1
                and row[0][0] == want["n_movies"]
                and abs(row[0][1] - want["avg_vote"]) < 1e-6,
                "Q8 genre-year stat matches the recomputed aggregate",
                f"n={want['n_movies']}, avg_vote={want['avg_vote']}",
            )

            n = scalar("SELECT count(*) FROM l2_lang_stats")
            check(n == len(exp["agg"]["lang"]), "Q9 language stats present",
                  f"{n} languages")

            n = scalar("SELECT count(*) FROM l2_year_stats")
            check(n == len(exp["agg"]["year"]), "Q10 year stats present", f"{n} years")

            rows_ = query(
                "SELECT (doc ->> 'id')::int FROM l2_genre_top WHERE genre_id = %s "
                "ORDER BY pos",
                (PROBE_GENRE,),
            )
            got = [r[0] for r in rows_]
            check(got == exp["top_ids"],
                  f"Q6 precomputed top-{ks.TOP_K} matches L1's ordering")
            log()

        if "r" in models:
            log("R — load")
            genre_rows = sorted(GENRE_NAMES.items())
            lang_rows = sorted({m["original_language"] for m in movies
                                if m.get("original_language")})
            movie_rows = [
                (m["id"], m["id_imdb"], m["title"], m.get("original_title"),
                 m.get("original_language"), m.get("release_date") or None,
                 m.get("overview"), m["popularity"], m["vote_average"],
                 m["vote_count"], m["adult"], m["video"],
                 m.get("poster_path"), m.get("backdrop_path"))
                for m in movies
            ]
            pair_rows = [(m["id"], g) for m in movies for g in m["genre_ids"]]

            if not skip_load:
                start = time.perf_counter()
                # Parents before children, or the foreign keys reject the rows.
                written = insert_batched(
                    "INSERT INTO genres (genre_id, name) VALUES (%s,%s) "
                    "ON CONFLICT (genre_id) DO UPDATE SET name = EXCLUDED.name",
                    genre_rows)
                written += insert_batched(
                    "INSERT INTO languages (lang_code) VALUES (%s) "
                    "ON CONFLICT DO NOTHING", [(c,) for c in lang_rows])
                written += insert_batched(
                    "INSERT INTO movies (id, id_imdb, title, original_title, "
                    "lang_code, release_date, overview, popularity, "
                    "vote_average, vote_count, adult, video, poster_path, "
                    "backdrop_path) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title",
                    movie_rows)
                written += insert_batched(
                    "INSERT INTO movie_genres (movie_id, genre_id) VALUES (%s,%s) "
                    "ON CONFLICT DO NOTHING", pair_rows)
                seconds = time.perf_counter() - start
                log(f"   inserted {written:,} rows: {seconds:.1f} s  "
                    f"({written / seconds:,.0f} rows/s)")
                record_load("postgresql-relational", "R", seconds, written)
                expected = len(genre_rows) + len(lang_rows) + len(movie_rows) + len(pair_rows)
                check(written == expected,
                      f"{expected:,} rows written across four tables",
                      f"got {written:,}")

                # Model R is the only one of the three whose access paths are
                # chosen by a planner, and a planner with no statistics guesses.
                # Straight after a bulk load autovacuum has not run yet, so
                # without this the first benchmark measures PostgreSQL working
                # from default estimates — which is neither its real behaviour
                # nor reproducible.
                with conn.cursor() as cur:
                    cur.execute("ANALYZE genres, languages, movies, movie_genres")
                conn.commit()
                log("   ANALYZE done — the planner has statistics")

            log("R — read back")
            for table, want in (("genres", len(genre_rows)),
                                ("languages", len(lang_rows)),
                                ("movies", len(movie_rows)),
                                ("movie_genres", len(pair_rows))):
                got_n = scalar(f"SELECT count(*) FROM {table}")
                check(got_n == want, f"{table} row count", f"{got_n:,} == {want:,}")

            probe = exp["probe"]
            row = query("SELECT title, year, lang_code FROM movies WHERE id = %s",
                        (probe["id"],))
            check(len(row) == 1 and row[0][0] == probe["title"],
                  "Q1 point lookup by primary key", probe["title"])
            # The generated column derives `year` from release_date inside the
            # database. If PostgreSQL and common.dataset.derive_year disagree
            # about any record, every year-based count below diverges, so it is
            # worth checking on the probe before trusting the aggregates.
            check(row and row[0][1] == probe.get("year"),
                  "generated `year` matches derive_year()",
                  f"{row[0][1]} == {probe.get('year')}")

            got = scalar("SELECT id FROM movies WHERE id_imdb = %s", (probe["id_imdb"],))
            check(got == probe["id"], "Q2 unique constraint on id_imdb resolves the "
                  "alternate key", probe["id_imdb"])

            n = scalar("SELECT count(*) FROM movies WHERE year = %s", (PROBE_YEAR,))
            check(n == exp["n_year"], f"Q3 year {PROBE_YEAR}",
                  f"{n:,} == {exp['n_year']:,}")

            n = scalar("SELECT count(*) FROM movies WHERE lang_code = %s "
                       "AND vote_count > %s", (PROBE_LANG, PROBE_VOTES))
            check(n == exp["n_lang_votes"], "Q4 language + vote floor",
                  f"{n:,} == {exp['n_lang_votes']:,}")

            n = scalar("SELECT count(*) FROM movie_genres WHERE genre_id = %s",
                       (PROBE_GENRE,))
            check(n == exp["n_genre"], "Q5 genre membership via the junction table",
                  f"{n:,} == {exp['n_genre']:,}")

            # The two-genre intersection is a self-join on movie_genres — the
            # thing the JSONB models had to do with two containment lookups.
            n = scalar("SELECT count(*) FROM movie_genres a JOIN movie_genres b "
                       "USING (movie_id) WHERE a.genre_id = %s AND b.genre_id = %s",
                       (PROBE_GENRE, PROBE_GENRE_B))
            check(n == exp["n_both_genres"], "Q5 two-genre intersection (self-join)",
                  f"{n:,} == {exp['n_both_genres']:,}")

            rows_ = query(
                "SELECT m.id FROM movies m JOIN movie_genres mg ON mg.movie_id = m.id "
                "WHERE mg.genre_id = %s ORDER BY m.popularity DESC, m.id LIMIT %s",
                (PROBE_GENRE, ks.TOP_K))
            got = [r[0] for r in rows_]
            check(got == exp["top_ids"],
                  f"Q6 top-{ks.TOP_K} — the index family L1 could not express",
                  f"{len(got)} ids")

            rows_ = query(
                "SELECT mg.genre_id, m.year, count(*), avg(m.vote_average) "
                "FROM movies m JOIN movie_genres mg ON mg.movie_id = m.id "
                "WHERE m.year BETWEEN %s AND %s GROUP BY 1, 2",
                (min(ks.REPORT_YEARS), max(ks.REPORT_YEARS)))
            want = {(g, y): row for (g, y), row in exp["agg"]["genre_year"].items()
                    if y in ks.REPORT_YEARS}
            check(len(rows_) == len(want),
                  "Q8 genre x year as a join + GROUP BY, one statement",
                  f"{len(rows_)} == {len(want)} rows")
            log()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load the full corpus into L1/L2 and verify every key family."
    )
    parser.add_argument("--model", action="append", choices=("l1", "l2", "r"))
    parser.add_argument("--db", choices=("oracle", "fdb", "postgres"))
    parser.add_argument("--skip-load", action="store_true", help="verify without reloading")
    args = parser.parse_args(argv)

    models = args.model or ["l1", "l2"]
    database = detect_database(args.db)

    log("reading data/ ...")
    movies = list(iter_movies())
    exp = expectations(movies)
    log(f"   {len(movies):,} records; probe movie {exp['probe']['id']} "
        f"({exp['probe']['title']!r})\n")

    {"oracle": run_oracle, "postgres": run_postgres, "fdb": run_fdb}[database](
        models, movies, exp, args.skip_load
    )

    log(f"{'FAILED: ' + str(len(failures)) + ' check(s)' if failures else 'ALL CHECKS PASSED'}"
        f"   ({time.perf_counter() - _t0:.1f} s total)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
