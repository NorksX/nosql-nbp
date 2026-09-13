"""The ten queries, implemented six times: L1 and L2, on each of three databases.

Every implementation returns a *canonical result* — a plain JSON-comparable
value — so the harness can assert that they agree before it times any of them.
A fast wrong answer is worth nothing, and the L1-vs-L2 comparison is only
meaningful if both models answer the same question identically.

Query parameters come from `common.keyspec` so nothing drifts between the
databases.

The three implementations of a query are deliberately *not* transliterations of
each other. Each database is asked the same question in the way that database
is meant to be asked — SQL with `GROUP BY` where there is a query planner,
hand-built range reads where there is an ordered keyspace — because a
comparison of deliberately hobbled implementations would measure nothing. Where
that choice changes what work happens on which side of the connection, it is
called out in a comment, and the consequences are discussed in
docs/schema-comparison.md.
"""

from __future__ import annotations

import json
import os

from common import keyspec as ks
from common.dataset import GENRE_NAMES, decode

YEARS = list(ks.REPORT_YEARS)


def top_languages(rows: list[tuple[str, int, float]], k: int = 10) -> list:
    """Query 9's shared tail: rank identically everywhere.

    Deliberately does not round. Rounding a mean before comparison made two
    correct implementations disagree — L1 sums in key order and L2 summed in
    file order, and at a .00005 boundary those land on different 4-decimal
    values. The harness compares floats with a tolerance instead.
    """
    ranked = sorted(rows, key=lambda r: (-r[1], r[0]))[:k]
    return [[lang, n, pop] for lang, n, pop in ranked]


class Backend:
    """Common surface. Subclasses implement l1_qN / l2_qN for N in 1..10."""

    name: str

    probe_id = ks.PROBE_MOVIE_ID
    probe_imdb = ks.PROBE_MOVIE_IMDB
    probe_year = ks.PROBE_MOVIE_YEAR
    probe_lang = ks.PROBE_MOVIE_LANG
    genre_ids = sorted(GENRE_NAMES)

    def run(self, model: str, qid: str):
        return getattr(self, f"{model}_{qid}")()

    def supports(self, model: str, qid: str) -> bool:
        return hasattr(self, f"{model}_{qid}")

    def close(self) -> None:
        pass


# ==========================================================================
# FoundationDB
# ==========================================================================


class FdbBackend(Backend):
    name = "foundationdb"

    #: Keys per paged scan transaction. A full L1 scan reads 68 MB, far past the
    #: 5 s transaction limit, so long scans must be paged across transactions —
    #: which also means they are *not* a consistent snapshot. That trade is a
    #: genuine finding, not an implementation detail.
    PAGE = 20_000

    def __init__(self):
        import fdb

        fdb.api_version(730)
        self.fdb = fdb
        self.db = fdb.open(os.environ.get("FDB_CLUSTER_FILE", "/etc/foundationdb/fdb.cluster"))

        @fdb.transactional
        def _get(tr, key):
            return tr[key].value

        @fdb.transactional
        def _range(tr, begin, end, limit):
            return list(tr.get_range(begin, end, limit=limit))

        @fdb.transactional
        def _get_many(tr, keys):
            futures = [tr[k] for k in keys]
            return [f.value for f in futures]

        self._get, self._range, self._get_many = _get, _range, _get_many

    # -- primitives --------------------------------------------------------

    def get(self, key: tuple):
        return self._get(self.db, self.fdb.tuple.pack(key))

    def bounds(self, key_range: ks.KeyRange):
        r = self.fdb.tuple.range(key_range.prefix)
        begin = self.fdb.tuple.pack(key_range.start) if key_range.start else r.start
        return begin, r.stop

    def scan(self, key_range: ks.KeyRange, limit: int = 0):
        begin, end = self.bounds(key_range)
        return self._range(self.db, begin, end, limit)

    def scan_paged(self, key_range: ks.KeyRange):
        """Page a large range across transactions, staying inside the 5 s limit."""
        begin, end = self.bounds(key_range)
        while True:
            rows = self._range(self.db, begin, end, self.PAGE)
            if not rows:
                return
            yield from rows
            begin = self.fdb.KeySelector.first_greater_than(rows[-1].key)

    def fetch(self, ids):
        """Fetch many movie records, pipelined, in transaction-sized batches."""
        ids = list(ids)
        out = []
        for i in range(0, len(ids), 5_000):
            keys = [self.fdb.tuple.pack(ks.l1_movie_key(x)) for x in ids[i : i + 5_000]]
            out.extend(decode(v) for v in self._get_many(self.db, keys) if v is not None)
        return out

    def ids_in(self, key_range: ks.KeyRange):
        return [self.fdb.tuple.unpack(k)[-1] for k, _ in self.scan(key_range)]

    def all_movies(self):
        return (decode(v) for _, v in self.scan_paged(ks.KeyRange(("movie",))))

    def all_chunked(self):
        for _, value in self.scan_paged(ks.KeyRange(("year",))):
            yield from json.loads(bytes(value))

    # -- L1 ----------------------------------------------------------------

    def l1_q1(self):
        return decode(self.get(ks.l1_movie_key(self.probe_id)))["title"]

    def l1_q2(self):
        return self.fdb.tuple.unpack(bytes(self.get(ks.l1_imdb_key(self.probe_imdb))))[0]

    def l1_q3(self):
        return len(self.ids_in(ks.l1_year_range(ks.PROBE_YEAR)))

    def l1_q4(self):
        return len(self.scan(ks.l1_lang_range(ks.PROBE_LANG, ks.PROBE_VOTES + 1)))

    def l1_q5(self):
        a = set(self.ids_in(ks.l1_genre_range(ks.PROBE_GENRE)))
        b = set(self.ids_in(ks.l1_genre_range(ks.PROBE_GENRE_B)))
        return len(a & b)

    def l1_q6(self):
        rows = self.scan(ks.l1_genre_pop_range(ks.PROBE_GENRE), limit=ks.TOP_K)
        ids = [self.fdb.tuple.unpack(k)[-1] for k, _ in rows]
        return [m["id"] for m in sorted(self.fetch(ids), key=lambda m: (-m["popularity"], m["id"]))]

    def l1_q7(self):
        # No (lang, year) index: read the three year buckets, fetch every record,
        # then filter by language. This is L1's documented weak spot.
        ids = []
        for year in (self.probe_year - 1, self.probe_year, self.probe_year + 1):
            ids.extend(self.ids_in(ks.l1_year_range(year)))
        return sum(
            1
            for m in self.fetch(ids)
            if m.get("original_language") == self.probe_lang and m["id"] != self.probe_id
        )

    def l1_q8(self):
        return aggregate_genre_year(self.all_movies())

    def l1_q9(self):
        return aggregate_languages(self.all_movies())

    def l1_q10(self):
        return aggregate_years(self.all_movies())

    # -- L2 ----------------------------------------------------------------

    def l2_q1(self):
        for movie in self.all_chunked():
            if movie["id"] == self.probe_id:
                return movie["title"]
        return None

    def l2_q2(self):
        for movie in self.all_chunked():
            if movie.get("id_imdb") == self.probe_imdb:
                return movie["id"]
        return None

    def l2_q3(self):
        return sum(
            len(json.loads(bytes(v))) for _, v in self.scan(ks.l2_year_range(ks.PROBE_YEAR))
        )

    def l2_q4(self):
        return sum(
            1
            for m in self.all_chunked()
            if m.get("original_language") == ks.PROBE_LANG and m["vote_count"] > ks.PROBE_VOTES
        )

    def l2_q5(self):
        return sum(
            1
            for m in self.all_chunked()
            if ks.PROBE_GENRE in m["genre_ids"] and ks.PROBE_GENRE_B in m["genre_ids"]
        )

    def l2_q6(self):
        return [
            decode(bytes(v))["id"] for _, v in self.scan(ks.l2_genre_top_range(ks.PROBE_GENRE))
        ]

    def l2_q7(self):
        total = 0
        for year in (self.probe_year - 1, self.probe_year, self.probe_year + 1):
            for _, value in self.scan(ks.l2_year_range(year)):
                total += sum(
                    1
                    for m in json.loads(bytes(value))
                    if m.get("original_language") == self.probe_lang and m["id"] != self.probe_id
                )
        return total

    def l2_q8(self):
        out = []
        for key, value in self.scan(ks.KeyRange(("stats", "genre_year"))):
            _, _, genre_id, year = self.fdb.tuple.unpack(key)
            if year in ks.REPORT_YEARS:
                row = decode(bytes(value))
                out.append([genre_id, year, row["n_movies"], row["avg_vote"]])
        return sorted(out)

    def l2_q9(self):
        rows = []
        for key, value in self.scan(ks.l2_lang_range()):
            row = decode(bytes(value))
            rows.append((self.fdb.tuple.unpack(key)[-1], row["n_movies"], row["avg_popularity"]))
        return top_languages(rows)

    def l2_q10(self):
        out = []
        for key, value in self.scan(ks.l2_year_stats_range()):
            year = self.fdb.tuple.unpack(key)[-1]
            if year in ks.REPORT_YEARS:
                row = decode(bytes(value))
                out.append(
                    [year, row["n_movies"], row["avg_popularity"], row["avg_vote_rated"]]
                )
        return sorted(out)


# ==========================================================================
# Oracle NoSQL
# ==========================================================================


class OracleBackend(Backend):
    name = "oracle-nosql"

    def __init__(self):
        from borneo import GetRequest, NoSQLHandle, NoSQLHandleConfig, QueryRequest
        from borneo.kv import StoreAccessTokenProvider

        self.GetRequest, self.QueryRequest = GetRequest, QueryRequest
        self.handle = NoSQLHandle(
            NoSQLHandleConfig(os.environ["NOSQL_ENDPOINT"]).set_authorization_provider(
                StoreAccessTokenProvider()
            )
        )
        self.genre_pred = self._genre_predicate()

    def close(self) -> None:
        self.handle.close()

    def query(self, statement: str) -> list[dict]:
        request = self.QueryRequest().set_statement(statement)
        rows: list[dict] = []
        while True:
            rows.extend(self.handle.query(request).get_results())
            if request.is_done():
                break
        return rows

    def scalar(self, statement: str):
        rows = self.query(statement)
        return next(iter(rows[0].values())) if rows else None

    def _genre_predicate(self) -> str:
        for candidate in ("t.doc.genre_ids[] =any {g}", "EXISTS t.doc.genre_ids[$element = {g}]"):
            try:
                self.query(
                    f"SELECT t.id FROM l1_movies t WHERE {candidate.format(g=18)} LIMIT 1"
                )
                return candidate
            except Exception:
                continue
        raise RuntimeError("no accepted array-containment syntax")

    def genre(self, genre_id: int) -> str:
        return self.genre_pred.format(g=genre_id)

    def chunks(self, where: str = ""):
        """Stream every movie out of the L2 chunk table."""
        for row in self.query(f"SELECT movies FROM l2_movies_by_year {where}"):
            yield from row["movies"]

    # -- L1 ----------------------------------------------------------------

    def l1_q1(self):
        result = self.handle.get(
            self.GetRequest().set_table_name("l1_movies").set_key({"id": self.probe_id})
        )
        return result.get_value()["doc"]["title"]

    def l1_q2(self):
        rows = self.query(
            f"SELECT t.id FROM l1_movies t WHERE t.doc.id_imdb = '{self.probe_imdb}'"
        )
        return rows[0]["id"]

    def l1_q3(self):
        return self.scalar(
            f"SELECT count(*) AS n FROM l1_movies t WHERE t.doc.year = {ks.PROBE_YEAR}"
        )

    def l1_q4(self):
        return self.scalar(
            "SELECT count(*) AS n FROM l1_movies t "
            f"WHERE t.doc.original_language = '{ks.PROBE_LANG}' "
            f"AND t.doc.vote_count > {ks.PROBE_VOTES}"
        )

    def l1_q5(self):
        return self.scalar(
            "SELECT count(*) AS n FROM l1_movies t "
            f"WHERE {self.genre(ks.PROBE_GENRE)} AND {self.genre(ks.PROBE_GENRE_B)}"
        )

    def l1_q6(self):
        rows = self.query(
            f"SELECT t.id FROM l1_movies t WHERE {self.genre(ks.PROBE_GENRE)} "
            f"ORDER BY t.doc.popularity DESC LIMIT {ks.TOP_K}"
        )
        return [r["id"] for r in rows]

    def l1_q7(self):
        return self.scalar(
            "SELECT count(*) AS n FROM l1_movies t "
            f"WHERE t.doc.original_language = '{self.probe_lang}' "
            f"AND t.doc.year >= {self.probe_year - 1} AND t.doc.year <= {self.probe_year + 1} "
            f"AND t.id != {self.probe_id}"
        )

    def l1_q8(self):
        out = []
        for genre_id in sorted(self.genre_ids):
            rows = self.query(
                "SELECT t.doc.year AS y, count(*) AS n, avg(t.doc.vote_average) AS a "
                f"FROM l1_movies t WHERE {self.genre(genre_id)} "
                f"AND t.doc.year >= {YEARS[0]} AND t.doc.year <= {YEARS[-1]} "
                "GROUP BY t.doc.year"
            )
            out.extend([genre_id, r["y"], r["n"], r["a"]] for r in rows)
        return sorted(out)

    def l1_q9(self):
        rows = self.query(
            "SELECT t.doc.original_language AS lang, count(*) AS n, "
            "avg(t.doc.popularity) AS p FROM l1_movies t GROUP BY t.doc.original_language"
        )
        return top_languages([(r["lang"], r["n"], r["p"]) for r in rows])

    def l1_q10(self):
        overall = {
            r["y"]: (r["n"], r["p"])
            for r in self.query(
                "SELECT t.doc.year AS y, count(*) AS n, avg(t.doc.popularity) AS p "
                f"FROM l1_movies t WHERE t.doc.year >= {YEARS[0]} AND t.doc.year <= {YEARS[-1]} "
                "GROUP BY t.doc.year"
            )
        }
        rated = {
            r["y"]: r["a"]
            for r in self.query(
                "SELECT t.doc.year AS y, avg(t.doc.vote_average) AS a FROM l1_movies t "
                f"WHERE t.doc.year >= {YEARS[0]} AND t.doc.year <= {YEARS[-1]} "
                f"AND t.doc.vote_count >= {ks.MIN_VOTE_COUNT} GROUP BY t.doc.year"
            )
        }
        return sorted(
            [y, n, p, rated.get(y, 0.0)] for y, (n, p) in overall.items()
        )

    # -- L2 ----------------------------------------------------------------

    def l2_q1(self):
        for movie in self.chunks():
            if movie["id"] == self.probe_id:
                return movie["title"]
        return None

    def l2_q2(self):
        for movie in self.chunks():
            if movie.get("id_imdb") == self.probe_imdb:
                return movie["id"]
        return None

    def l2_q3(self):
        return self.scalar(
            f"SELECT sum(n_movies) AS n FROM l2_movies_by_year WHERE year = {ks.PROBE_YEAR}"
        )

    def l2_q4(self):
        return sum(
            1
            for m in self.chunks()
            if m.get("original_language") == ks.PROBE_LANG and m["vote_count"] > ks.PROBE_VOTES
        )

    def l2_q5(self):
        return sum(
            1
            for m in self.chunks()
            if ks.PROBE_GENRE in m["genre_ids"] and ks.PROBE_GENRE_B in m["genre_ids"]
        )

    def l2_q6(self):
        rows = self.query(
            f"SELECT t.doc.id AS id FROM l2_genre_top t WHERE t.genre_id = {ks.PROBE_GENRE} "
            "ORDER BY t.genre_id, t.pos"
        )
        return [r["id"] for r in rows]

    def l2_q7(self):
        where = f"WHERE year >= {self.probe_year - 1} AND year <= {self.probe_year + 1}"
        return sum(
            1
            for m in self.chunks(where)
            if m.get("original_language") == self.probe_lang and m["id"] != self.probe_id
        )

    def l2_q8(self):
        rows = self.query(
            "SELECT genre_id, year, n_movies, avg_vote FROM l2_genre_year_stats "
            f"WHERE year >= {YEARS[0]} AND year <= {YEARS[-1]}"
        )
        return sorted(
            [r["genre_id"], r["year"], r["n_movies"], r["avg_vote"]] for r in rows
        )

    def l2_q9(self):
        rows = self.query("SELECT lang, n_movies, avg_popularity FROM l2_lang_stats")
        return top_languages([(r["lang"], r["n_movies"], r["avg_popularity"]) for r in rows])

    def l2_q10(self):
        rows = self.query(
            "SELECT year, n_movies, avg_popularity, avg_vote_rated FROM l2_year_stats "
            f"WHERE year >= {YEARS[0]} AND year <= {YEARS[-1]}"
        )
        return sorted(
            [r["year"], r["n_movies"], r["avg_popularity"], r["avg_vote_rated"]]
            for r in rows
        )


# ==========================================================================
# PostgreSQL — the relational control
# ==========================================================================


class PostgresBackend(Backend):
    """The same two models, in a relational engine.

    Two implementation choices shape every number this class produces, and both
    are deliberate:

    **The L2 fallback runs inside the server.** When L2 has no access path —
    queries 1, 2, 4, 5 — Oracle NoSQL and FoundationDB both ship all 806 chunks
    to the client and scan them in Python, because neither can look inside a
    stored blob. PostgreSQL can: ``jsonb_array_elements`` unnests a chunk in the
    executor, so the scan happens next to the data and only the answer crosses
    the connection. Writing it as a client-side scan instead would have been
    easy and would have measured nothing except how fast Python parses JSON.
    The comparison is between idiomatic implementations, which is the same rule
    that already lets Oracle NoSQL use ``GROUP BY`` where FoundationDB scans.

    **Prepared statements are left on.** psycopg promotes a statement to a
    server-side prepared statement after five executions, so the benchmark's
    steady state has the parse and plan cached. That is what a real application
    gets, and it is the counterpart of the client-side conveniences the other
    two backends keep — borneo materializing rows, the FDB binding pipelining
    futures. It is stated here because it is part of what the numbers measure.
    """

    name = "postgresql"

    def __init__(self):
        import psycopg

        self.conn = psycopg.connect(
            os.environ.get("PG_DSN", "postgresql://nbp:nbp@pg:5432/tmdb"),
            autocommit=True,
        )
        self.server_version = self.conn.execute("SHOW server_version").fetchone()[0]

    def close(self) -> None:
        self.conn.close()

    # -- primitives --------------------------------------------------------

    def rows(self, statement: str, params: tuple = ()) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(statement, params, prepare=True)
            return cur.fetchall()

    def scalar(self, statement: str, params: tuple = ()):
        rows = self.rows(statement, params)
        return rows[0][0] if rows else None

    # -- L1 ----------------------------------------------------------------

    def l1_q1(self):
        return self.scalar("SELECT doc ->> 'title' FROM l1_movies WHERE id = %s",
                           (self.probe_id,))

    def l1_q2(self):
        return self.scalar("SELECT id FROM l1_movies WHERE doc ->> 'id_imdb' = %s",
                           (self.probe_imdb,))

    def l1_q3(self):
        return self.scalar(
            "SELECT count(*) FROM l1_movies WHERE (doc ->> 'year')::int = %s",
            (ks.PROBE_YEAR,),
        )

    def l1_q4(self):
        # idx_lang_votes is a composite B-tree, so this is one index range scan
        # starting inside the language — the same shape as FoundationDB's
        # two-sided range read, expressed as a predicate instead of as bounds.
        return self.scalar(
            "SELECT count(*) FROM l1_movies WHERE doc ->> 'original_language' = %s "
            "AND (doc ->> 'vote_count')::int > %s",
            (ks.PROBE_LANG, ks.PROBE_VOTES),
        )

    def l1_q5(self):
        # Two GIN lookups the planner intersects itself; FoundationDB does the
        # same intersection in Python over two scanned key ranges.
        return self.scalar(
            "SELECT count(*) FROM l1_movies "
            "WHERE doc -> 'genre_ids' @> %s::jsonb AND doc -> 'genre_ids' @> %s::jsonb",
            (str(ks.PROBE_GENRE), str(ks.PROBE_GENRE_B)),
        )

    def l1_q6(self):
        # The query the L1 index set cannot serve properly in a relational
        # engine: GIN finds the genre but carries no order, so the matching rows
        # must be sorted afterwards. See keyspec.POSTGRES_L1_INDEX_DDL.
        return [
            r[0]
            for r in self.rows(
                "SELECT id FROM l1_movies WHERE doc -> 'genre_ids' @> %s::jsonb "
                "ORDER BY (doc ->> 'popularity')::float8 DESC, id ASC LIMIT %s",
                (str(ks.PROBE_GENRE), ks.TOP_K),
            )
        ]

    def l1_q7(self):
        # L1's documented weak spot — no (lang, year) index. PostgreSQL still
        # gets to pick the cheaper of the two single-column indexes and filter
        # the rest server-side, which is the Oracle NoSQL situation, not the
        # FoundationDB one (where the fallback means fetching every candidate).
        return self.scalar(
            "SELECT count(*) FROM l1_movies "
            "WHERE doc ->> 'original_language' = %s "
            "AND (doc ->> 'year')::int BETWEEN %s AND %s AND id <> %s",
            (self.probe_lang, self.probe_year - 1, self.probe_year + 1, self.probe_id),
        )

    def l1_q8(self):
        # One statement. Oracle NoSQL cannot group by an element of a JSON array
        # and needs 19 separate queries; FoundationDB has no server-side
        # aggregation at all. This is the clearest single advantage the
        # relational engine has on this query set.
        return sorted(
            [g, y, n, a]
            for g, y, n, a in self.rows(
                "SELECT g.value::int AS genre_id, (doc ->> 'year')::int AS y, "
                "count(*) AS n, avg((doc ->> 'vote_average')::float8) AS a "
                "FROM l1_movies, LATERAL jsonb_array_elements_text(doc -> 'genre_ids') g "
                "WHERE (doc ->> 'year')::int BETWEEN %s AND %s "
                "GROUP BY 1, 2",
                (YEARS[0], YEARS[-1]),
            )
        )

    def l1_q9(self):
        return top_languages(
            self.rows(
                "SELECT doc ->> 'original_language', count(*), "
                "avg((doc ->> 'popularity')::float8) FROM l1_movies GROUP BY 1"
            )
        )

    def l1_q10(self):
        # FILTER does the vote floor in the same pass, so this is one scan where
        # Oracle NoSQL needs two GROUP BY statements joined client-side.
        return sorted(
            [y, n, p, a]
            for y, n, p, a in self.rows(
                "SELECT (doc ->> 'year')::int AS y, count(*) AS n, "
                "avg((doc ->> 'popularity')::float8) AS p, "
                "coalesce(avg((doc ->> 'vote_average')::float8) "
                "  FILTER (WHERE (doc ->> 'vote_count')::int >= %s), 0.0) AS a "
                "FROM l1_movies WHERE (doc ->> 'year')::int BETWEEN %s AND %s "
                "GROUP BY 1",
                (ks.MIN_VOTE_COUNT, YEARS[0], YEARS[-1]),
            )
        )

    # -- L2 ----------------------------------------------------------------

    def l2_q1(self):
        # No path from an id to a movie, so every chunk is a candidate. The
        # LIMIT 1 lets the executor stop at the first match, which is the same
        # early exit the other two backends make in Python.
        return self.scalar(
            "SELECT m ->> 'title' FROM l2_movies_by_year, "
            "LATERAL jsonb_array_elements(movies) m "
            "WHERE (m ->> 'id')::int = %s LIMIT 1",
            (self.probe_id,),
        )

    def l2_q2(self):
        return self.scalar(
            "SELECT (m ->> 'id')::int FROM l2_movies_by_year, "
            "LATERAL jsonb_array_elements(movies) m "
            "WHERE m ->> 'id_imdb' = %s LIMIT 1",
            (self.probe_imdb,),
        )

    def l2_q3(self):
        return self.scalar(
            "SELECT sum(n_movies) FROM l2_movies_by_year WHERE year = %s",
            (ks.PROBE_YEAR,),
        )

    def l2_q4(self):
        return self.scalar(
            "SELECT count(*) FROM l2_movies_by_year, "
            "LATERAL jsonb_array_elements(movies) m "
            "WHERE m ->> 'original_language' = %s AND (m ->> 'vote_count')::int > %s",
            (ks.PROBE_LANG, ks.PROBE_VOTES),
        )

    def l2_q5(self):
        return self.scalar(
            "SELECT count(*) FROM l2_movies_by_year, "
            "LATERAL jsonb_array_elements(movies) m "
            "WHERE m -> 'genre_ids' @> %s::jsonb AND m -> 'genre_ids' @> %s::jsonb",
            (str(ks.PROBE_GENRE), str(ks.PROBE_GENRE_B)),
        )

    def l2_q6(self):
        return [
            r[0]
            for r in self.rows(
                "SELECT (doc ->> 'id')::int FROM l2_genre_top WHERE genre_id = %s "
                "ORDER BY pos",
                (ks.PROBE_GENRE,),
            )
        ]

    def l2_q7(self):
        return self.scalar(
            "SELECT count(*) FROM l2_movies_by_year, "
            "LATERAL jsonb_array_elements(movies) m "
            "WHERE year BETWEEN %s AND %s AND m ->> 'original_language' = %s "
            "AND (m ->> 'id')::int <> %s",
            (self.probe_year - 1, self.probe_year + 1, self.probe_lang, self.probe_id),
        )

    def l2_q8(self):
        return sorted(
            [g, y, n, a]
            for g, y, n, a in self.rows(
                "SELECT genre_id, year, n_movies, avg_vote FROM l2_genre_year_stats "
                "WHERE year BETWEEN %s AND %s",
                (YEARS[0], YEARS[-1]),
            )
        )

    def l2_q9(self):
        return top_languages(
            self.rows("SELECT lang, n_movies, avg_popularity FROM l2_lang_stats")
        )

    def l2_q10(self):
        return sorted(
            [y, n, p, a]
            for y, n, p, a in self.rows(
                "SELECT year, n_movies, avg_popularity, avg_vote_rated "
                "FROM l2_year_stats WHERE year BETWEEN %s AND %s",
                (YEARS[0], YEARS[-1]),
            )
        )


# ==========================================================================
# Client-side aggregation — what FoundationDB has to do for queries 8-10
# ==========================================================================


class DatasetBackend(Backend):
    """The answers, computed from ``data/`` by brute force. No database.

    This is the ground truth the three databases are checked against by
    ``bench/answers.py``. Every method reads the corpus and does the obvious
    slow thing, because the point is to be obviously correct rather than fast —
    if a hand-built FoundationDB key range, an Oracle NoSQL `=any` predicate and
    a PostgreSQL GIN lookup all agree with this, they are all right.

    It answers under the name of either model; L1 and L2 are properties of how
    the data is stored, and the corpus is not stored at all here.
    """

    name = "dataset"

    def __init__(self):
        from common.dataset import iter_movies

        self.movies = list(iter_movies())

    def run(self, model: str, qid: str):
        return getattr(self, qid)()

    def supports(self, model: str, qid: str) -> bool:
        return hasattr(self, qid)

    def q1(self):
        return next(m["title"] for m in self.movies if m["id"] == self.probe_id)

    def q2(self):
        return next(m["id"] for m in self.movies if m.get("id_imdb") == self.probe_imdb)

    def q3(self):
        return sum(1 for m in self.movies if m.get("year") == ks.PROBE_YEAR)

    def q4(self):
        return sum(
            1
            for m in self.movies
            if m.get("original_language") == ks.PROBE_LANG
            and m["vote_count"] > ks.PROBE_VOTES
        )

    def q5(self):
        return sum(
            1
            for m in self.movies
            if ks.PROBE_GENRE in m["genre_ids"] and ks.PROBE_GENRE_B in m["genre_ids"]
        )

    def q6(self):
        pool = [m for m in self.movies if ks.PROBE_GENRE in m["genre_ids"]]
        pool.sort(key=lambda m: (-m["popularity"], m["id"]))
        return [m["id"] for m in pool[: ks.TOP_K]]

    def q7(self):
        window = range(self.probe_year - 1, self.probe_year + 2)
        return sum(
            1
            for m in self.movies
            if m.get("year") in window
            and m.get("original_language") == self.probe_lang
            and m["id"] != self.probe_id
        )

    def q8(self):
        return aggregate_genre_year(self.movies)

    def q9(self):
        return aggregate_languages(self.movies)

    def q10(self):
        return aggregate_years(self.movies)


def aggregate_genre_year(movies) -> list:
    acc: dict[tuple[int, int], list] = {}
    for movie in movies:
        year = movie.get("year")
        if year in ks.REPORT_YEARS:
            for genre_id in movie["genre_ids"]:
                row = acc.setdefault((genre_id, year), [0, 0.0])
                row[0] += 1
                row[1] += movie["vote_average"]
    return sorted(
        [g, y, n, total / n] for (g, y), (n, total) in acc.items()
    )


def aggregate_languages(movies) -> list:
    acc: dict[str, list] = {}
    for movie in movies:
        row = acc.setdefault(movie.get("original_language", ""), [0, 0.0])
        row[0] += 1
        row[1] += movie["popularity"]
    return top_languages([(lang, n, total / n) for lang, (n, total) in acc.items()])


def aggregate_years(movies) -> list:
    acc: dict[int, list] = {}
    for movie in movies:
        year = movie.get("year")
        if year in ks.REPORT_YEARS:
            row = acc.setdefault(year, [0, 0.0, 0, 0.0])
            row[0] += 1
            row[1] += movie["popularity"]
            if movie["vote_count"] >= ks.MIN_VOTE_COUNT:
                row[2] += 1
                row[3] += movie["vote_average"]
    return sorted(
        [y, n, pop / n, vote / n_rated if n_rated else 0.0]
        for y, (n, pop, n_rated, vote) in acc.items()
    )
