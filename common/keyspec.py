"""The two schemas — L1 and L2 — for both databases. Single source of truth.

Phase 2 asks for two *different* aggregation levels so Phase 4 has something to
compare. The two are deliberately opposite in shape:

    L1  fine-grained    730,414 small keys, one movie per key, five secondary
                        indexes. Point lookups and selective filters are one or
                        two operations; aggregates degrade to a full scan.

    L2  coarse-grained  ~1,000 keys, movies packed into ~90 KB per-year chunks
                        plus precomputed aggregates. Reports are one or a few
                        reads; point lookup by id has no entry point and
                        degrades to scanning every chunk.

Both models hold the *same* 109,222 records, byte-for-byte identical values from
``common.dataset.encode``. The only thing that differs is how they are keyed and
grouped, which is what makes the Phase 4 numbers attributable to the schema.

Three databases implement these two models: Oracle NoSQL Database CE and
FoundationDB (the two key-value stores the topic asks for) and PostgreSQL (the
relational control, added so the project can say what a relational engine costs
on the same data and the same questions, rather than assuming it).

FoundationDB keys are given here as Python tuples. The FDB loaders pack them
with ``fdb.tuple.pack`` / ``fdb.Subspace``; this module deliberately does not
import ``fdb``, because neither the Oracle nor the PostgreSQL client container
has it installed. Oracle NoSQL and PostgreSQL get the equivalent shape as DDL,
further down — and the places where the relational engine *cannot* express a
key family are marked there, because those are results.

Everything measured (key counts, chunk counts, byte sizes) is verified against
the real data by ``common/verify_schemas.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import NamedTuple

from common.dataset import YEAR_UNKNOWN, encode


class KeyRange(NamedTuple):
    """A range read: everything under ``prefix``, optionally starting part-way.

    ``prefix`` bounds the read on both sides. ``start``, when given, moves the
    lower bound forward to a longer key *inside* that prefix — which is how a
    half-open filter like ``vote_count > 500`` is expressed. A single prefix
    tuple cannot say this: ``("idx","lang","en",501)`` bounds the keys whose
    vote count is exactly 501, not the ones from 501 upward.

    FoundationDB:
        bounds = fdb.tuple.range(r.prefix)
        begin = fdb.tuple.pack(r.start) if r.start else bounds.start
        tr.get_range(begin, bounds.stop)
    """

    prefix: tuple
    start: tuple | None = None

# --------------------------------------------------------------------------
# Shared constants — both models, both databases
# --------------------------------------------------------------------------

#: Vote floor for every "top rated" query. 37.6% of the corpus has
#: ``vote_count == 0``, so an unfiltered rating ranking is noise.
MIN_VOTE_COUNT = 50

#: How many entries the L2 per-genre leaderboard precomputes (query 6 asks for
#: the top 20). L2 cannot answer a top-K request larger than this without
#: falling back to a scan — that limitation is part of the comparison.
TOP_K = 20

#: Probe parameters for the ten queries. Fixed here so every implementation and
#: every benchmark run asks the identical question of both databases and both
#: models — a latency comparison between different questions is meaningless.
PROBE_YEAR = 2017  # the largest year bucket: 7,871 movies, 56 chunks
PROBE_LANG = "en"
PROBE_VOTES = 500
PROBE_GENRE = 18  # Drama, the most common genre
PROBE_GENRE_B = 27  # Horror
REPORT_YEARS = range(2000, 2021)  # the range queries 8 and 10 report over

#: The movie queries 1, 2 and 7 are asked about — the most popular in the
#: corpus, so it is memorable and unambiguous. Its year and language are given
#: as constants rather than looked up, so that query 7 measures the same work in
#: both models (L1 could fetch them in one read; L2 could not).
PROBE_MOVIE_ID = 419704  # "Ad Astra"
PROBE_MOVIE_IMDB = "tt2935510"
PROBE_MOVIE_YEAR = 2019
PROBE_MOVIE_LANG = "en"

#: Popularity is a float, and a float cannot be negated inside an
#: order-preserving integer key. Scale to a fixed-point integer instead:
#: popularity has one decimal digit of real precision, 1000 leaves headroom.
POPULARITY_SCALE = 1000

#: L2 chunk target. FoundationDB's hard value limit is 100 KB; 90 KB leaves room
#: for the last record to overshoot the target without crossing the limit.
CHUNK_TARGET_BYTES = 90_000

#: FoundationDB limits, for the loaders to respect (values are the hard limits).
FDB_VALUE_LIMIT = 100_000
FDB_KEY_LIMIT = 10_000
FDB_TXN_BYTE_LIMIT = 10_000_000
#: Pairs per write transaction. Well under the 10 MB / 5 s transaction budget
#: for L1, whose largest value is 2,021 bytes; retry on ``transaction_too_old``.
FDB_BATCH_SIZE = 1_000

#: Bytes per write transaction. A count-only limit is not enough for L2: its
#: chunks average 85 KB, so 1,000 of them would be an 85 MB transaction against
#: a 10 MB limit. 4 MB leaves room for the last chunk plus key overhead and
#: keeps a transaction comfortably inside the 5 s budget.
FDB_TXN_TARGET_BYTES = 4_000_000


def batched(pairs: Iterable[tuple[bytes, bytes]]) -> Iterator[list[tuple[bytes, bytes]]]:
    """Group packed ``(key, value)`` pairs into transaction-sized batches.

    Bounded by both :data:`FDB_BATCH_SIZE` and :data:`FDB_TXN_TARGET_BYTES`, so
    the same call works for L1's 730,414 tiny pairs and L2's 806 large ones.
    """
    batch: list[tuple[bytes, bytes]] = []
    size = 0
    for key, value in pairs:
        if batch and (
            len(batch) >= FDB_BATCH_SIZE or size + len(key) + len(value) > FDB_TXN_TARGET_BYTES
        ):
            yield batch
            batch, size = [], 0
        batch.append((key, value))
        size += len(key) + len(value)
    if batch:
        yield batch


def scaled_popularity(popularity: float) -> int:
    """Descending-popularity sort component for an ordered key.

    FoundationDB sorts tuple-packed keys ascending, so storing the *negated*
    scaled popularity makes a plain forward range read return the most popular
    first, with no client-side sorting at all.
    """
    return -int(round(float(popularity) * POPULARITY_SCALE))


# ==========================================================================
# Model L1 — fine-grained: one movie per key, five secondary indexes
# ==========================================================================

# FoundationDB key families. Index entries carry an empty value; the key itself
# is the whole payload, which is the idiomatic FDB index and keeps the index
# subspace small enough to stay cached.
#
#   ("movie", id)                                  -> encode(record)
#   ("idx", "imdb",      imdb_id)                  -> pack((id,))
#   ("idx", "year",      year, id)                 -> b""
#   ("idx", "lang",      lang, vote_count, id)     -> b""
#   ("idx", "genre",     genre_id, id)             -> b""
#   ("idx", "genre_pop", genre_id, -pop*1000, id)  -> b""

L1_MOVIE_PREFIX = ("movie",)
L1_INDEX_PREFIX = ("idx",)


def l1_movie_key(movie_id: int) -> tuple:
    return ("movie", int(movie_id))


def l1_imdb_key(imdb_id: str) -> tuple:
    return ("idx", "imdb", imdb_id)


def l1_year_range(year: int) -> KeyRange:
    """Every movie of ``year`` — query 3 is one range read."""
    return KeyRange(("idx", "year", int(year)))


def l1_lang_range(lang: str, min_votes: int | None = None) -> KeyRange:
    """A language, optionally narrowed to a minimum vote count.

    The vote count sits *before* the id in the key, so query 4 (language X with
    ``vote_count > 500``) is a single range read from ``(…, lang, 501)`` to the
    end of the language prefix, rather than a scan of the language followed by a
    client-side filter. For English that is ~2,600 keys read instead of 58,015.
    """
    prefix = ("idx", "lang", lang)
    if min_votes is None:
        return KeyRange(prefix)
    return KeyRange(prefix, prefix + (int(min_votes),))


def l1_genre_range(genre_id: int) -> KeyRange:
    return KeyRange(("idx", "genre", int(genre_id)))


def l1_genre_pop_range(genre_id: int) -> KeyRange:
    """A genre ordered by descending popularity — query 6 reads the first 20
    keys of this range and then fetches 20 movie records."""
    return KeyRange(("idx", "genre_pop", int(genre_id)))


def l1_index_entries(record: dict) -> Iterator[tuple[tuple, tuple]]:
    """Every index entry a single movie contributes, as ``(key, value)``.

    Both halves are tuples for the FoundationDB tuple layer to pack. Four of the
    five families are pure index keys and carry an empty value — the key *is*
    the payload. The IMDb family is different: it is a mapping to a primary key,
    so its value is ``(id,)``. Emitting the value here rather than leaving it to
    each loader is deliberate; writing ``b""`` for every family looks right and
    silently breaks only query 2.

    Loaders write these alongside the movie record in the same transaction, so a
    restart never leaves an index entry without its record or vice versa.
    """
    movie_id = record["id"]

    if record.get("id_imdb"):
        yield ("idx", "imdb", record["id_imdb"]), (movie_id,)

    # 5,442 records have no derivable year and get no year index entry. They are
    # still reachable by id, genre and language, and are still in L2's bucket 0.
    if "year" in record:
        yield ("idx", "year", record["year"], movie_id), ()

    if record.get("original_language"):
        yield ("idx", "lang", record["original_language"], record["vote_count"], movie_id), ()

    pop = scaled_popularity(record["popularity"])
    for genre_id in record["genre_ids"]:
        yield ("idx", "genre", genre_id, movie_id), ()
        yield ("idx", "genre_pop", genre_id, pop, movie_id), ()


def l1_index_keys(record: dict) -> Iterator[tuple]:
    """Just the keys, for counting and for the Oracle side, which has no values."""
    for key, _ in l1_index_entries(record):
        yield key


# Oracle NoSQL gets the same model through a single table plus native secondary
# indexes. The document stays in one JSON column rather than being flattened
# into typed columns, so the stored bytes match FoundationDB's values and the
# footprint comparison is about the storage engine, not about column encoding.

ORACLE_L1_TABLE = "l1_movies"

ORACLE_L1_DDL = (
    f"""CREATE TABLE IF NOT EXISTS {ORACLE_L1_TABLE} (
          id INTEGER,
          doc JSON,
          PRIMARY KEY(id)
        )""",
)

# One index per FoundationDB index family, same column order, so the two
# databases are asked to do the same work.
ORACLE_L1_INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS idx_imdb ON {ORACLE_L1_TABLE}(doc.id_imdb AS STRING)",
    f"CREATE INDEX IF NOT EXISTS idx_year ON {ORACLE_L1_TABLE}(doc.year AS INTEGER)",
    f"""CREATE INDEX IF NOT EXISTS idx_lang_votes ON {ORACLE_L1_TABLE}(
          doc.original_language AS STRING, doc.vote_count AS INTEGER)""",
    f"CREATE INDEX IF NOT EXISTS idx_genre ON {ORACLE_L1_TABLE}(doc.genre_ids[] AS INTEGER)",
    # At most one array path per index; the scalar popularity may follow it.
    f"""CREATE INDEX IF NOT EXISTS idx_genre_pop ON {ORACLE_L1_TABLE}(
          doc.genre_ids[] AS INTEGER, doc.popularity AS DOUBLE)""",
)


# PostgreSQL — the relational control. Same one-table-plus-five-index-families
# shape as Oracle NoSQL, for the same reason: the document stays whole in one
# column so the three databases hold the same document and the comparison is
# about the engine, not about how the record was taken apart.
#
# The stored type is JSONB rather than JSON. JSON keeps the exact input text and
# would give the closest byte-for-byte match to what the two key-value stores
# hold, but it can be neither indexed by containment nor read without reparsing
# on every access, which would make PostgreSQL lose on every query for a reason
# that has nothing to do with being relational. JSONB is the choice a real
# project would make, so it is the one measured here — and the difference it
# makes to the footprint is itself reported (docs/schema-comparison.md §4.1).
# The normalization in common.dataset already sorts keys and removes duplicates,
# so JSONB's decomposition loses nothing about the document.

POSTGRES_L1_TABLE = "l1_movies"

POSTGRES_L1_DDL = (
    f"""CREATE TABLE IF NOT EXISTS {POSTGRES_L1_TABLE} (
          id  INTEGER PRIMARY KEY,
          doc JSONB NOT NULL
        )""",
)

# One index per FoundationDB key family, in the same column order — with one
# exception that is a finding, not an oversight, and is documented as such.
#
# Four of the five translate directly: PostgreSQL indexes an expression, so
# `(doc ->> 'id_imdb')` and `((doc ->> 'year')::int)` are ordinary B-trees over
# a JSON path, and the composite (language, vote_count) index gives query 4 the
# same two-sided range read that the FoundationDB key gives it.
#
# The fifth, idx_genre_pop, has no PostgreSQL equivalent. A B-tree cannot be
# built over "each element of this JSON array, paired with this scalar" — the
# index would need one entry per (movie, genre) pair, and an expression index
# produces exactly one entry per row. What PostgreSQL offers instead is a GIN
# index, which answers "which movies are in genre 18" quickly but stores no
# order, so query 6 has to sort the matches afterwards. That is the same
# position Oracle NoSQL ends up in, and the opposite of FoundationDB, where the
# ordering *is* the key. The consequence is measured in query 6.
POSTGRES_L1_INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS idx_imdb ON {POSTGRES_L1_TABLE} ((doc ->> 'id_imdb'))",
    f"CREATE INDEX IF NOT EXISTS idx_year ON {POSTGRES_L1_TABLE} (((doc ->> 'year')::int))",
    f"""CREATE INDEX IF NOT EXISTS idx_lang_votes ON {POSTGRES_L1_TABLE} (
          (doc ->> 'original_language'), ((doc ->> 'vote_count')::int))""",
    # jsonb_path_ops supports only @>, which is the single operator queries 5
    # and 6 need, and builds a smaller index than the default jsonb_ops.
    f"""CREATE INDEX IF NOT EXISTS idx_genre ON {POSTGRES_L1_TABLE}
          USING GIN ((doc -> 'genre_ids') jsonb_path_ops)""",
    # The closest thing to idx_genre_pop that PostgreSQL can express: popularity
    # alone, so the planner can at least walk the corpus in popularity order.
    # It does not combine with idx_genre — see the note above.
    f"""CREATE INDEX IF NOT EXISTS idx_pop ON {POSTGRES_L1_TABLE}
          (((doc ->> 'popularity')::float8) DESC, id)""",
)


# ==========================================================================
# Model L2 — coarse-grained: per-year chunks plus precomputed aggregates
# ==========================================================================

# FoundationDB key families:
#
#   ("year",  year, chunk_no)               -> JSON array of ~90 KB of records
#   ("stats", "genre_year", genre, year)    -> encode(genre-year aggregate)
#   ("stats", "lang", lang)                 -> encode(language aggregate)
#   ("stats", "year", year)                 -> encode(year aggregate)
#   ("top",   "genre", genre, position)     -> encode(record)   position 0..19
#
# Note what is *absent*: there is no key from a movie id, an IMDb id or a genre
# to an individual movie. That is the point of the model, not an oversight — it
# is what forces queries 1, 2 and 5 into a full scan under L2 and makes the
# L1/L2 delta measurable in both directions.

L2_YEAR_PREFIX = ("year",)
L2_STATS_PREFIX = ("stats",)
L2_TOP_PREFIX = ("top",)


def l2_chunk_key(year: int, chunk_no: int) -> tuple:
    return ("year", int(year), int(chunk_no))


def l2_year_range(year: int) -> KeyRange:
    """An entire year bucket — query 3 and query 7 read whole buckets."""
    return KeyRange(("year", int(year)))


def l2_lang_range() -> KeyRange:
    """All language stats — query 9 is one range read of 137 keys."""
    return KeyRange(("stats", "lang"))


def l2_year_stats_range() -> KeyRange:
    """All year stats — query 10 is one range read of 49 keys."""
    return KeyRange(("stats", "year"))


def l2_genre_top_range(genre_id: int) -> KeyRange:
    """A genre's precomputed leaderboard, already in rank order."""
    return KeyRange(("top", "genre", int(genre_id)))


def l2_genre_year_key(genre_id: int, year: int) -> tuple:
    return ("stats", "genre_year", int(genre_id), int(year))


def l2_lang_key(lang: str) -> tuple:
    return ("stats", "lang", lang)


def l2_year_stats_key(year: int) -> tuple:
    return ("stats", "year", int(year))


def l2_genre_top_key(genre_id: int, position: int) -> tuple:
    return ("top", "genre", int(genre_id), int(position))


def year_bucket(record: dict) -> int:
    """L2 bucket for a record. Undated movies collect under year 0."""
    return record.get("year", YEAR_UNKNOWN)


def chunk_records(records: Iterable[dict]) -> Iterator[tuple[int, list[dict], bytes]]:
    """Pack one year's records into ``(chunk_no, records, value)`` triples.

    Greedy fill to :data:`CHUNK_TARGET_BYTES`, sorted by id. Both are needed for
    determinism: sorting removes any dependence on the order the loader happened
    to read the 21 files in, and a deterministic packing means both databases
    store identical chunk boundaries and identical bytes. Without that, the
    Phase 4 chunk-read latencies would not be comparable.

    A record always lands in a chunk even if it alone exceeds the target — the
    largest single record is 2,021 bytes, so this never approaches the 100 KB
    value limit, but the loader should not silently drop data if that changes.
    """
    blobs = [(r, encode(r)) for r in sorted(records, key=lambda r: r["id"])]

    chunk_no = 0
    batch: list[dict] = []
    parts: list[bytes] = []
    size = 2  # the enclosing "[" and "]"

    for record, blob in blobs:
        added = len(blob) + (1 if parts else 0)  # +1 for the separating comma
        if parts and size + added > CHUNK_TARGET_BYTES:
            yield chunk_no, batch, b"[" + b",".join(parts) + b"]"
            chunk_no += 1
            batch, parts, size = [], [], 2
            added = len(blob)
        batch.append(record)
        parts.append(blob)
        size += added

    if parts:
        yield chunk_no, batch, b"[" + b",".join(parts) + b"]"


# The aggregates below are the *definition* of the L2 stats values. Both
# sub-teams call these, and the Phase 3 L1 implementations must reproduce the
# same numbers by full scan — that equality is the correctness check for the
# whole L1-vs-L2 comparison.


def _mean(total: float, n: int) -> float:
    """Full precision, deliberately unrounded.

    Rounding here looked harmless and was not: an L1 query rounds the raw mean
    once, while an L2 query would round an already-rounded stored value a second
    time. Measured on the real data, that double rounding moved 3 of 399
    genre-year averages in the 4th decimal — enough for L1 and L2 to return
    provably different answers to the same question. Round once, at the point of
    display.
    """
    return total / n if n else 0.0


def aggregate(records: Iterable[dict]) -> dict[str, dict]:
    """Compute every L2 aggregate in one pass over the corpus.

    Returns ``{"genre_year": {...}, "lang": {...}, "year": {...},
    "genre_top": {...}}``, keyed by the tuple/scalar that identifies each row.

    Semantics, fixed here so neither sub-team improvises:

    - ``genre_year`` — a movie counts once per genre it lists, so the genre
      counts sum to more than 109,222. No vote floor (query 8 asks for the
      plain average).
    - ``lang`` — every movie counts exactly once.
    - ``year`` — ``n_movies``/``avg_popularity`` cover every movie in the
      bucket; ``n_rated``/``avg_vote_rated`` cover only those with
      ``vote_count >= MIN_VOTE_COUNT`` (query 10).
    - ``genre_top`` — top :data:`TOP_K` by popularity, ties broken by ascending
      id so the leaderboard is deterministic.
    """
    genre_year: dict[tuple[int, int], dict] = {}
    lang: dict[str, dict] = {}
    year: dict[int, dict] = {}
    genre_pool: dict[int, list[dict]] = {}

    for record in records:
        y = year_bucket(record)
        pop = record["popularity"]
        vote = record["vote_average"]
        votes = record["vote_count"]

        for genre_id in record["genre_ids"]:
            row = genre_year.setdefault(
                (genre_id, y),
                {"n_movies": 0, "sum_vote": 0.0, "sum_popularity": 0.0, "sum_votes": 0},
            )
            row["n_movies"] += 1
            row["sum_vote"] += vote
            row["sum_popularity"] += pop
            row["sum_votes"] += votes
            genre_pool.setdefault(genre_id, []).append(record)

        code = record.get("original_language", "")
        row = lang.setdefault(code, {"n_movies": 0, "sum_popularity": 0.0})
        row["n_movies"] += 1
        row["sum_popularity"] += pop

        row = year.setdefault(
            y,
            {"n_movies": 0, "sum_popularity": 0.0, "n_rated": 0, "sum_vote_rated": 0.0},
        )
        row["n_movies"] += 1
        row["sum_popularity"] += pop
        if votes >= MIN_VOTE_COUNT:
            row["n_rated"] += 1
            row["sum_vote_rated"] += vote

    for row in genre_year.values():
        row["avg_vote"] = _mean(row["sum_vote"], row["n_movies"])
        row["avg_popularity"] = _mean(row["sum_popularity"], row["n_movies"])
    for row in lang.values():
        row["avg_popularity"] = _mean(row["sum_popularity"], row["n_movies"])
    for row in year.values():
        row["avg_popularity"] = _mean(row["sum_popularity"], row["n_movies"])
        row["avg_vote_rated"] = _mean(row["sum_vote_rated"], row["n_rated"])

    genre_top = {
        genre_id: sorted(pool, key=lambda r: (-r["popularity"], r["id"]))[:TOP_K]
        for genre_id, pool in genre_pool.items()
    }

    return {
        "genre_year": genre_year,
        "lang": lang,
        "year": year,
        "genre_top": genre_top,
    }


# Oracle NoSQL gets one table per L2 key family. The chunk and leaderboard
# tables use a composite primary key with a shard component, which is the
# closest equivalent to FoundationDB's ordered composite key: rows sharing a
# year (or a genre) land in the same shard and are read together.
#
# Column names avoid SQL keywords — hence `n_movies` rather than `count` and
# `pos` rather than `rank`.

ORACLE_L2_TABLES = (
    "l2_movies_by_year",
    "l2_genre_year_stats",
    "l2_lang_stats",
    "l2_year_stats",
    "l2_genre_top",
)

ORACLE_L2_DDL = (
    """CREATE TABLE IF NOT EXISTS l2_movies_by_year (
         year INTEGER,
         chunk INTEGER,
         n_movies INTEGER,
         movies JSON,
         PRIMARY KEY(SHARD(year), chunk)
       )""",
    """CREATE TABLE IF NOT EXISTS l2_genre_year_stats (
         genre_id INTEGER,
         year INTEGER,
         n_movies INTEGER,
         sum_vote DOUBLE,
         avg_vote DOUBLE,
         sum_popularity DOUBLE,
         avg_popularity DOUBLE,
         sum_votes LONG,
         PRIMARY KEY(SHARD(genre_id), year)
       )""",
    """CREATE TABLE IF NOT EXISTS l2_lang_stats (
         lang STRING,
         n_movies INTEGER,
         sum_popularity DOUBLE,
         avg_popularity DOUBLE,
         PRIMARY KEY(lang)
       )""",
    """CREATE TABLE IF NOT EXISTS l2_year_stats (
         year INTEGER,
         n_movies INTEGER,
         sum_popularity DOUBLE,
         avg_popularity DOUBLE,
         n_rated INTEGER,
         sum_vote_rated DOUBLE,
         avg_vote_rated DOUBLE,
         PRIMARY KEY(year)
       )""",
    """CREATE TABLE IF NOT EXISTS l2_genre_top (
         genre_id INTEGER,
         pos INTEGER,
         doc JSON,
         PRIMARY KEY(SHARD(genre_id), pos)
       )""",
)

# L2 has no secondary indexes on purpose. Adding one would quietly turn it back
# into L1 and erase the contrast the whole comparison rests on.
ORACLE_L2_INDEX_DDL: tuple[str, ...] = ()


# PostgreSQL L2 — one table per key family again, with the composite primary
# keys carrying the same column order as the FoundationDB tuples. PostgreSQL has
# no shard component to declare; a multi-column primary key is a single B-tree,
# so the rows of one year (or one genre) are already physically adjacent in the
# index and are read by one range scan. That is a closer match to FoundationDB's
# ordered keyspace than Oracle NoSQL's PRIMARY KEY(SHARD(year), chunk), which
# hashes the shard component.
#
# Worth stating explicitly for the report: the ~90 KB chunking exists because of
# FoundationDB's 100 KB value limit. PostgreSQL has no comparable limit — a
# single field may be up to 1 GB, and TOAST would store and compress a whole
# year as one value without complaint. The chunking is kept anyway, identical to
# the byte, because the three databases must hold identical chunks for the
# Phase 4 numbers to be comparable. The constraint PostgreSQL does not have is
# therefore visible in the results as a cost it pays for no reason, which is a
# fair thing to point out in its favour.

POSTGRES_L2_TABLES = ORACLE_L2_TABLES

POSTGRES_L2_DDL = (
    """CREATE TABLE IF NOT EXISTS l2_movies_by_year (
         year     INTEGER NOT NULL,
         chunk    INTEGER NOT NULL,
         n_movies INTEGER NOT NULL,
         movies   JSONB   NOT NULL,
         PRIMARY KEY (year, chunk)
       )""",
    """CREATE TABLE IF NOT EXISTS l2_genre_year_stats (
         genre_id       INTEGER NOT NULL,
         year           INTEGER NOT NULL,
         n_movies       INTEGER NOT NULL,
         sum_vote       DOUBLE PRECISION NOT NULL,
         avg_vote       DOUBLE PRECISION NOT NULL,
         sum_popularity DOUBLE PRECISION NOT NULL,
         avg_popularity DOUBLE PRECISION NOT NULL,
         sum_votes      BIGINT NOT NULL,
         PRIMARY KEY (genre_id, year)
       )""",
    """CREATE TABLE IF NOT EXISTS l2_lang_stats (
         lang           TEXT PRIMARY KEY,
         n_movies       INTEGER NOT NULL,
         sum_popularity DOUBLE PRECISION NOT NULL,
         avg_popularity DOUBLE PRECISION NOT NULL
       )""",
    """CREATE TABLE IF NOT EXISTS l2_year_stats (
         year           INTEGER PRIMARY KEY,
         n_movies       INTEGER NOT NULL,
         sum_popularity DOUBLE PRECISION NOT NULL,
         avg_popularity DOUBLE PRECISION NOT NULL,
         n_rated        INTEGER NOT NULL,
         sum_vote_rated DOUBLE PRECISION NOT NULL,
         avg_vote_rated DOUBLE PRECISION NOT NULL
       )""",
    """CREATE TABLE IF NOT EXISTS l2_genre_top (
         genre_id INTEGER NOT NULL,
         pos      INTEGER NOT NULL,
         doc      JSONB   NOT NULL,
         PRIMARY KEY (genre_id, pos)
       )""",
)

# Same rule as Oracle NoSQL: no secondary indexes on L2, deliberately.
POSTGRES_L2_INDEX_DDL: tuple[str, ...] = ()


# ==========================================================================
# Model R — the relational control, done properly
# ==========================================================================

# L1 and L2 ask PostgreSQL to behave like a key-value store: one JSONB blob per
# key, indexed through expressions. That answers "how does a relational engine
# cope with a key-value model", which is a fair question but not the obvious
# one. Model R answers the obvious one: given the same 109,222 movies, what
# does a normalized relational schema cost?
#
# So R is third normal form. The document is taken apart into typed columns,
# the repeating genre list becomes a junction table, and the two lookup values
# that repeat across rows — genre and language — become their own entities.
# Nothing is stored twice and nothing is derived twice.
#
#   genres(genre_id)            19 rows      the 19 ids that occur
#   languages(lang_code)       137 rows
#   movies(id)             109,222 rows      one row per film, typed columns
#   movie_genres(movie_id,
#                genre_id)  149,484 rows     the many-to-many
#                          ---------
#                            258,862 rows total
#
# Against L1's 730,414 keys and L2's 1,867. The three models therefore span
# two and a half orders of magnitude in how finely the same corpus is cut up,
# which is the axis the whole study is about.
#
# What R can express that L1-on-JSONB could not: `idx_genre_pop`. In L1 the
# genre list lives inside a JSON array, and no B-tree can be built over "each
# element of this array paired with this scalar". Here the pair is two real
# columns in two real tables, so the planner can join movie_genres to movies
# and walk an ordered index. Whether it actually beats FoundationDB's
# order-in-the-key is a question for the measurement, not for this comment.

POSTGRES_R_TABLES = ("movie_genres", "movies", "languages", "genres")

POSTGRES_R_DDL = (
    """CREATE TABLE IF NOT EXISTS genres (
         genre_id SMALLINT PRIMARY KEY,
         name     TEXT NOT NULL UNIQUE
       )""",
    """CREATE TABLE IF NOT EXISTS languages (
         lang_code TEXT PRIMARY KEY
       )""",
    # release_date is the fact; `year` is derived from it and declared as
    # derived, so it cannot drift out of step the way a loader-maintained
    # column can. The 5,442 films with no release date get NULL in both, which
    # is the honest relational reading of "unknown" and matches L1, where those
    # records get no entry in the year index either.
    """CREATE TABLE IF NOT EXISTS movies (
         id             INTEGER PRIMARY KEY,
         id_imdb        TEXT NOT NULL UNIQUE,
         title          TEXT NOT NULL,
         original_title TEXT,
         lang_code      TEXT REFERENCES languages(lang_code),
         release_date   DATE,
         year           SMALLINT GENERATED ALWAYS AS
                          (EXTRACT(YEAR FROM release_date)::smallint) STORED,
         overview       TEXT,
         popularity     DOUBLE PRECISION NOT NULL,
         vote_average   DOUBLE PRECISION NOT NULL,
         vote_count     INTEGER NOT NULL,
         adult          BOOLEAN NOT NULL,
         video          BOOLEAN NOT NULL,
         poster_path    TEXT,
         backdrop_path  TEXT
       )""",
    """CREATE TABLE IF NOT EXISTS movie_genres (
         movie_id INTEGER  NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
         genre_id SMALLINT NOT NULL REFERENCES genres(genre_id),
         PRIMARY KEY (movie_id, genre_id)
       )""",
)

# The same five access paths L1 provides, expressed relationally. Four are
# ordinary B-trees over typed columns instead of over JSON expressions. The
# fifth — genre together with popularity — is the one L1 could not express at
# all; here it is a join between two indexed tables.
#
# movie_genres carries its primary key (movie_id, genre_id), which answers
# "which genres does this film have"; the extra index inverts it to answer
# "which films are in this genre", which is what queries 5, 6 and 8 need.
POSTGRES_R_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS r_idx_year ON movies (year)",
    "CREATE INDEX IF NOT EXISTS r_idx_lang_votes ON movies (lang_code, vote_count)",
    "CREATE INDEX IF NOT EXISTS r_idx_pop ON movies (popularity DESC, id)",
    "CREATE INDEX IF NOT EXISTS r_idx_mg_genre ON movie_genres (genre_id, movie_id)",
)
